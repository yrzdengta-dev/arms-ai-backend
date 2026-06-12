import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.fake_provider import AuditModelRequest, FakeAuditProvider
from app.adapters.llm.openai_provider import OpenAICompatibleProvider
from app.core.config import get_settings
from app.core.time import utc_now
from app.models.audit_result import AuditResult
from app.models.order import Order
from app.schemas.audit import AuditOutput, Decision
from app.services.routing_service import route_order

logger = logging.getLogger(__name__)
settings = get_settings()

PROTOCOL_VERSION = 1

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def compute_audit_input_hash(
    prompt: str,
    order_snapshot: dict[str, Any],
    pdf_text: str = "",
    pdf_sha256s: list[str] | None = None,
    skill_id: str = "",
    skill_version: str = "",
    prompt_hash: str = "",
    rules_hash: str = "",
    model_provider: str = "",
    model_name: str = "",
    protocol_version: int = 1,
) -> str:
    """Compute a deterministic hash of all audit inputs for idempotency.

    Includes every dimension that, if changed, should trigger a new audit run:
    prompt content, order snapshot, PDF content (via SHA-256), skill identity,
    model identity, rules content, and protocol version.
    """
    payload = json.dumps(
        {
            "protocol_version": protocol_version,
            "skill_id": skill_id,
            "skill_version": skill_version,
            "prompt_hash": prompt_hash,
            "rules_hash": rules_hash,
            "model_provider": model_provider,
            "model_name": model_name,
            "prompt": prompt,
            "order_snapshot": order_snapshot,
            "pdf_sha256s": sorted(pdf_sha256s) if pdf_sha256s else [],
            "pdf_text": pdf_text,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_provider() -> "FakeAuditProvider | OpenAICompatibleProvider":
    if settings.LLM_PROVIDER == "fake":
        return FakeAuditProvider()
    if settings.LLM_PROVIDER == "openai_compatible":
        if not settings.LLM_BASE_URL or not settings.LLM_API_KEY:
            raise ValueError("LLM_BASE_URL and LLM_API_KEY required for OpenAI compatible")
        return OpenAICompatibleProvider(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
        )
    raise ValueError(f"Unknown LLM provider: {settings.LLM_PROVIDER}")


async def run_audit(
    db: AsyncSession,
    order: Order,
    pdf_text: str = "",
) -> AuditResult:
    skill = await route_order(order.order_snapshot or {}, order.business_type)

    if skill is None:
        result = AuditResult(
            order_id=order.id,
            order_version=order.order_version,
            decision=Decision.MANUAL_REVIEW.value,
            skill_id=None,
            skill_version=None,
            prompt_version=None,
            model_provider=None,
            model_name=None,
            input_hash=None,
            raw_output={"reason": "No matching skill"},
            normalized_output=AuditOutput(
                decision=Decision.MANUAL_REVIEW,
                summary="No matching skill found for this order",
                manual_review_reasons=["No skill matched"],
            ).model_dump(),
            protocol_version=PROTOCOL_VERSION,
            status="COMPLETED",
            completed_at=utc_now(),
            rules_hash=None,
        )
        db.add(result)
        await db.flush()
        return result

    # --- Collect PDF SHA-256s for this order version ---
    pdf_sha256s = await _get_pdf_sha256s(db, order.id, order.order_version)

    # --- Compute rules hash ---
    rules_hash = _compute_rules_hash(skill.skill_id)

    # --- Compute full audit input hash ---
    provider = _get_provider()
    model_provider = settings.LLM_PROVIDER
    model_name = settings.LLM_MODEL if hasattr(settings, "LLM_MODEL") else getattr(provider, "model", "unknown")
    if model_name == "unknown" and hasattr(provider, "model"):
        model_name = provider.model

    input_hash = compute_audit_input_hash(
        prompt=skill.prompt_content,
        order_snapshot=order.order_snapshot or {},
        pdf_text=pdf_text,
        pdf_sha256s=pdf_sha256s,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        prompt_hash=skill.prompt_hash,
        rules_hash=rules_hash,
        model_provider=model_provider,
        model_name=model_name,
        protocol_version=1,
    )

    # --- Idempotency check: reuse existing result if input hasn't changed ---
    existing = await db.execute(
        select(AuditResult).where(
            AuditResult.order_id == order.id,
            AuditResult.order_version == order.order_version,
            AuditResult.input_hash == input_hash,
        ).limit(1)
    )
    cached = existing.scalars().first()
    if cached is not None:
        logger.info(
            "Audit idempotency hit: reusing cached result order_id=%s input_hash=%s",
            order.id, input_hash[:12],
        )
        return cached

    # --- Run deterministic rules BEFORE AI invocation ---
    deterministic_results = _run_deterministic_rules(
        skill_id=skill.skill_id,
        order_snapshot=order.order_snapshot or {},
        pdf_text=pdf_text,
    )

    request = AuditModelRequest(
        prompt=skill.prompt_content,
        order_snapshot=order.order_snapshot or {},
        pdf_text=pdf_text,
        skill_id=skill.skill_id,
        skill_version=skill.version,
    )

    response = await provider.audit(request)

    # Merge deterministic results into normalized output
    normalized = response.normalized_output.model_dump()
    if deterministic_results:
        ai_rules = normalized.get("rules", [])
        merged_rules = _merge_deterministic_rules(deterministic_results, ai_rules)
        normalized["rules"] = merged_rules

    # --- P1-4: Post-audit output validation ---
    normalized = _validate_audit_output(
        normalized, skill.skill_id, pdf_text,
    )

    final_decision = _compute_final_decision(
        response.decision,
        normalized.get("rules", []),
    )

    result = AuditResult(
        order_id=order.id,
        order_version=order.order_version,
        decision=final_decision,
        business_type=order.business_type,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        prompt_version=skill.prompt_hash,
        model_provider=response.model_provider,
        model_name=response.model_name,
        input_hash=input_hash,
        raw_output=response.raw_output,
        normalized_output=normalized,
        protocol_version=1,
        status="COMPLETED",
        completed_at=utc_now(),
        rules_hash=rules_hash,
    )
    db.add(result)
    await db.flush()
    await db.refresh(result)

    logger.info(
        "Audit completed order_id=%s decision=%s skill=%s deterministic_rules=%d",
        order.id, response.decision, skill.skill_id, len(deterministic_results),
    )
    return result


def _run_deterministic_rules(
    skill_id: str,
    order_snapshot: dict[str, Any],
    pdf_text: str,
) -> list[dict[str, Any]]:
    """Run the skill's deterministic rules against PDF text before AI."""
    rules_yaml = SKILLS_DIR / skill_id / "rules.yaml"
    if not rules_yaml.exists():
        return []

    try:
        rules_config = yaml.safe_load(rules_yaml.read_text())
        if not rules_config or "rules" not in rules_config:
            return []
    except (yaml.YAMLError, OSError):
        logger.warning("Failed to load rules.yaml for skill=%s", skill_id)
        return []

    from app.skills.simple_text_consistency.evaluator import evaluate_rule

    # Collect required field names from rules for the required_check rule
    required_field_names = [
        r.get("source_field", r.get("field", ""))
        for r in rules_config["rules"]
        if r.get("required") and r.get("type") != "required_check"
    ]

    results: list[dict[str, Any]] = []
    for rule_def in rules_config["rules"]:
        rule_type = rule_def.get("type", "exact_match")

        if rule_type == "required_check":
            missing = [
                f for f in required_field_names
                if not _extract_field_value(order_snapshot, f)
            ]
            if missing:
                results.append({
                    "rule_id": rule_def.get("id", "CHECK_REQUIRED_FIELDS"),
                    "result": "MANUAL_REVIEW",
                    "reason": f"Required fields missing or empty: {', '.join(missing)}",
                    "evidence": [],
                })
            else:
                results.append({
                    "rule_id": rule_def.get("id", "CHECK_REQUIRED_FIELDS"),
                    "result": "PASS",
                    "reason": "All required fields present",
                    "evidence": [],
                })
            continue

        field = rule_def.get("source_field", rule_def.get("field", ""))
        source_value = _extract_field_value(order_snapshot, field)
        pdf_values = _extract_pdf_values(pdf_text, field, rule_def)

        result = evaluate_rule(
            rule=rule_def,
            source_value=source_value,
            pdf_values=pdf_values,
            file_name="cert.pdf",
        )
        if result:
            results.append(result)

    return results


def _extract_field_value(snapshot: dict[str, Any], field: str) -> str:
    """Extract a field value from order_snapshot, supporting nested paths."""
    if not field:
        return ""
    # Support dot-notation: "certificate.skc"
    parts = field.split(".")
    value = snapshot
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    return str(value) if value else ""


def _extract_pdf_values(pdf_text: str, field: str, rule_def: dict[str, Any]) -> list[str]:
    """Extract candidate values from PDF text for a given field."""
    if not pdf_text:
        return []
    # Use search patterns from rule definition if available
    search_patterns = rule_def.get("search_patterns", [])
    if search_patterns:
        import re
        values: list[str] = []
        for pattern in search_patterns:
            try:
                matches = re.findall(pattern, pdf_text, re.IGNORECASE)
                values.extend(matches)
            except re.error:
                continue
        return values
    return []


def _compute_final_decision(ai_decision: str, rules: list[dict[str, Any]]) -> str:
    """Compute final decision: deterministic rule results override AI."""
    # Priority: REJECT > MANUAL_REVIEW > AI decision
    for r in rules:
        result = r.get("result", "")
        if result == "REJECT":
            return "REJECT"
    for r in rules:
        result = r.get("result", "")
        if result == "MANUAL_REVIEW":
            return "MANUAL_REVIEW"
    return ai_decision


def _merge_deterministic_rules(
    deterministic: list[dict[str, Any]],
    ai_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge deterministic rules with AI rules, deterministic taking precedence."""
    det_ids = {r.get("rule_id", "") for r in deterministic}
    # Keep all deterministic rules, plus AI rules for non-deterministic fields
    merged = list(deterministic)
    for ai_rule in ai_rules:
        if ai_rule.get("rule_id", "") not in det_ids:
            merged.append(ai_rule)
    return merged


# ---------------------------------------------------------------------------
# P1-3 / P1-4 Helpers
# ---------------------------------------------------------------------------

async def _get_pdf_sha256s(db: AsyncSession, order_id: str, order_version: int) -> list[str]:
    """Return sorted SHA-256 hashes of PDFs for the given order version."""
    from sqlalchemy import select as _select
    from app.models.order_file import OrderFile

    result = await db.execute(
        _select(OrderFile.sha256).where(
            OrderFile.order_id == order_id,
            OrderFile.order_version == order_version,
            OrderFile.sha256.isnot(None),
        )
    )
    return sorted([row[0] for row in result if row[0]])


def _compute_rules_hash(skill_id: str) -> str:
    """Compute a stable SHA-256 over the skill's rules.yaml content."""
    rules_yaml = SKILLS_DIR / skill_id / "rules.yaml"
    if not rules_yaml.exists():
        return ""
    try:
        content = rules_yaml.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except (OSError, hashlib.Error):
        return ""


def _validate_audit_output(
    normalized: dict[str, Any],
    skill_id: str,
    pdf_text: str,
) -> dict[str, Any]:
    """P1-4: Validate LLM output against business rules.

    - Unknown rule_ids → removed
    - Evidence quotes not found in pdf_text → marked unverified
    - PASS with no evidence → degraded to MANUAL_REVIEW
    """
    rules = normalized.get("rules", [])
    if not rules:
        return normalized

    valid_rule_ids = _get_valid_rule_ids(skill_id)
    cleaned_rules: list[dict[str, Any]] = []
    evidence_issues = 0

    for rule in rules:
        rule_id = rule.get("rule_id", "")
        # Unknown rule_id → skip
        if valid_rule_ids and rule_id not in valid_rule_ids:
            logger.warning("Dropping unknown rule_id=%s from audit output", rule_id)
            continue

        # Validate evidence quotes
        evidence_list = rule.get("evidence", [])
        for ev in evidence_list:
            quote = ev.get("quote", "")
            if quote and not _quote_exists_in_text(quote, pdf_text):
                ev["_unverified"] = True
                ev["_unverified_reason"] = "Quote not found in PDF text"
                evidence_issues += 1

        cleaned_rules.append(rule)

    normalized["rules"] = cleaned_rules

    # PASS with no evidence and no rules → degrade
    decision = normalized.get("decision", "")
    if decision == "PASS" and not cleaned_rules:
        logger.warning(
            "AI returned PASS with no valid rules — degrading to MANUAL_REVIEW"
        )
        normalized["decision"] = Decision.MANUAL_REVIEW.value
        reasons: list[str] = normalized.get("manual_review_reasons", [])
        reasons.append("AI returned PASS without sufficient evidence")
        normalized["manual_review_reasons"] = reasons

    if evidence_issues > 0:
        logger.info("Audit output validation: %d unverified evidence quotes", evidence_issues)

    return normalized


def _get_valid_rule_ids(skill_id: str) -> set[str]:
    """Return the set of valid rule_ids for the given skill."""
    rules_yaml = SKILLS_DIR / skill_id / "rules.yaml"
    if not rules_yaml.exists():
        return set()
    try:
        import yaml as _yaml
        rules_config = _yaml.safe_load(rules_yaml.read_text())
        if not rules_config or "rules" not in rules_config:
            return set()
        return {r.get("id", "") for r in rules_config["rules"] if r.get("id")}
    except Exception:
        return set()


def _quote_exists_in_text(quote: str, pdf_text: str) -> bool:
    """Check if a quote (or its normalized form) exists in the PDF text."""
    if not quote or not pdf_text:
        return False
    # Direct substring match
    if quote in pdf_text:
        return True
    # Normalize whitespace and try again
    normalized_quote = " ".join(quote.split())
    normalized_text = " ".join(pdf_text.split())
    return normalized_quote in normalized_text
