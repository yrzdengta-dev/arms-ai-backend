import logging
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.fake_provider import AuditModelRequest, FakeAuditProvider
from app.adapters.llm.openai_provider import OpenAICompatibleProvider
from app.core.config import get_settings
from app.models.audit_result import AuditResult
from app.models.order import Order
from app.schemas.audit import AuditOutput, Decision
from app.services.routing_service import route_order

logger = logging.getLogger(__name__)
settings = get_settings()

SKILLS_DIR = Path(__file__).parent.parent / "skills"


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
        )
        db.add(result)
        await db.flush()
        return result

    # --- Run deterministic rules BEFORE AI invocation ---
    deterministic_results = _run_deterministic_rules(
        skill_id=skill.skill_id,
        order_snapshot=order.order_snapshot or {},
        pdf_text=pdf_text,
    )

    provider = _get_provider()
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

    result = AuditResult(
        order_id=order.id,
        order_version=order.order_version,
        decision=response.decision,
        business_type=order.business_type,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        prompt_version=skill.prompt_hash,
        model_provider=response.model_provider,
        model_name=response.model_name,
        input_hash=response.input_hash,
        raw_output=response.raw_output,
        normalized_output=normalized,
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

    results: list[dict[str, Any]] = []
    for rule_def in rules_config["rules"]:
        field = rule_def.get("field", "")
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
