"""Deterministic text consistency rule evaluator.

All rules evaluate BEFORE AI invocation.
Rules that are deterministic should never go to AI.
"""

import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any


def normalize_text(text: str) -> str:
    """Full normalization: trim, case-fold, collapse whitespace, fullwidth→halfwidth, normalize punctuation."""
    if not text:
        return ""
    # Fullwidth to halfwidth
    t = unicodedata.normalize("NFKC", text)
    # Trim
    t = t.strip()
    # Case-fold
    t = t.casefold()
    # Collapse whitespace (including newlines)
    t = re.sub(r"\s+", " ", t)
    # Normalize separators — hyphen, en-dash, em-dash, bullet → space
    t = re.sub(r"[-–—•]\s*", " ", t)
    t = re.sub(r"\s+", " ", t)
    # Normalize fullwidth punctuation
    t = t.replace("、", ",").replace("。", ".").replace("，", ",").replace("．", ".")
    t = t.replace("（", "(").replace("）", ")").replace("：", ":").replace("；", ";")
    return t


def exact_match(source_value: str, pdf_value: str) -> bool:
    """Verbatim character-for-character match."""
    return source_value == pdf_value


def normalized_match(source_value: str, pdf_value: str) -> bool:
    """Match after normalization (case, whitespace, fullwidth)."""
    return normalize_text(source_value) == normalize_text(pdf_value)


def contains_match(source_value: str, pdf_value: str) -> bool:
    """Case-insensitive substring match."""
    return normalize_text(source_value) in normalize_text(pdf_value)


def normalize_date(date_str: str) -> str | None:
    """Normalize common date formats to YYYY-MM-DD."""
    if not date_str:
        return None
    t = date_str.strip()
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%d %b %Y", "%d %B %Y",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Try ISO short
    try:
        return datetime.fromisoformat(t).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        pass
    # Try: just digits parsing
    digits = re.findall(r"\d+", t)
    if len(digits) >= 3:
        y, m, d = digits[0], digits[1], digits[2]
        if len(y) == 4:
            try:
                return datetime(int(y), int(m), int(d)).strftime("%Y-%m-%d")
            except ValueError:
                return None
    return None


def date_match(source_value: str, pdf_value: str) -> bool:
    source_date = normalize_date(source_value)
    pdf_date = normalize_date(pdf_value)
    return source_date is not None and pdf_date is not None and source_date == pdf_date


MATCH_FUNCTIONS: dict[str, Callable[[str, str], bool]] = {
    "exact_match": exact_match,
    "normalized_match": normalized_match,
    "contains_match": contains_match,
    "date_match": date_match,
}


def evaluate_rule(
    rule: dict[str, Any],
    source_value: str,
    pdf_values: list[str],
    file_name: str = "",
    page: int = 1,
) -> dict[str, Any] | None:
    """Evaluate a single rule against PDF extracted values.

    Returns a rule result dict, or None if rule should be skipped.
    """
    rule_id = rule.get("id", rule.get("rule_id", "UNKNOWN"))
    rule_type = rule.get("type", "exact_match")
    required = rule.get("required", False)

    # required_check is handled in _run_deterministic_rules before calling evaluate_rule.
    # If it reaches here, skip gracefully.
    if rule_type == "required_check":
        return None

    # Missing required source
    if not source_value or not source_value.strip():
        if required:
            return {
                "rule_id": rule_id,
                "result": "MANUAL_REVIEW",
                "reason": f"Required field '{rule_id}' has no source value",
                "evidence": [],
            }
        return None

    # No PDF values found
    if not pdf_values:
        if required:
            return {
                "rule_id": rule_id,
                "result": "MANUAL_REVIEW",
                "reason": f"No PDF text found for rule '{rule_id}'",
                "evidence": [],
            }
        return None

    # Multiple candidates — must flag ambiguity regardless of match count
    if len(pdf_values) > 1:
        match_fn = MATCH_FUNCTIONS.get(rule_type, normalized_match)
        matches = [v for v in pdf_values if match_fn(source_value, v)]
        if len(matches) == 0:
            return {
                "rule_id": rule_id,
                "result": "REJECT",
                "reason": f"No match for '{source_value}' among {len(pdf_values)} candidates",
                "evidence": [],
            }
        return {
            "rule_id": rule_id,
            "result": "MANUAL_REVIEW",
            "reason": (
                f"Multiple candidates ({len(pdf_values)}) for '{source_value}', "
                f"{len(matches)} matched — requires human review"
            ),
            "evidence": [
                {"file_name": file_name, "page": page, "quote": m} for m in matches[:3]
            ],
            "ambiguous": True,
        }

    # Single candidate
    match_fn = MATCH_FUNCTIONS.get(rule_type, normalized_match)
    if match_fn(source_value, pdf_values[0]):
        return {
            "rule_id": rule_id,
            "result": "PASS",
            "reason": f"Match: '{source_value}'",
            "evidence": [{"file_name": file_name, "page": page, "quote": pdf_values[0]}],
        }
    return {
        "rule_id": rule_id,
        "result": "REJECT",
        "reason": f"Mismatch: source='{source_value}' vs pdf='{pdf_values[0]}'",
        "evidence": [{"file_name": file_name, "page": page, "quote": pdf_values[0]}],
    }
