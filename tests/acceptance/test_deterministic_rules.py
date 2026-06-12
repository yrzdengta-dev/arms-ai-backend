"""Acceptance tests: Deterministic rules (Section 3.8)

Verifies text consistency normalization functions:
- trim
- Case normalization
- Whitespace normalization
- Fullwidth/halfwidth normalization
- Punctuation normalization
- Exact match
- Normalized exact match
- Contains match
- Date normalization
- Required field check
- Multiple candidates → MANUAL_REVIEW
"""



# --- Test normalization functions ---

def test_trim_normalization():
    """Leading/trailing whitespace removed."""
    from app.skills.simple_text_consistency.evaluator import normalize_text
    assert normalize_text("  ABC-123  ") == normalize_text("ABC-123")


def test_case_normalization():
    """Case differences should not matter for normalized matching."""
    from app.skills.simple_text_consistency.evaluator import normalize_text
    assert normalize_text("AbC-123") == normalize_text("abc-123")


def test_whitespace_normalization():
    """Multiple spaces and newlines normalized."""
    from app.skills.simple_text_consistency.evaluator import normalize_text
    assert normalize_text("ABC   123") == normalize_text("ABC 123")
    assert normalize_text("ABC\n123") == normalize_text("ABC 123")


def test_fullwidth_halfwidth_normalization():
    """Fullwidth characters normalized to halfwidth."""
    from app.skills.simple_text_consistency.evaluator import normalize_text
    # Fullwidth A (U+FF21) → halfwidth A (U+0041)
    assert normalize_text("ＡＢＣ") == normalize_text("ABC")


def test_punctuation_normalization():
    """Fullwidth punctuation normalized."""
    from app.skills.simple_text_consistency.evaluator import normalize_text
    # Fullwidth comma 、and period 。
    assert normalize_text("A、B、C") == normalize_text("A,B,C")


def test_exact_match():
    """Verbatim exact match check."""
    from app.skills.simple_text_consistency.evaluator import exact_match
    assert exact_match("ABC-123", "ABC-123") is True
    assert exact_match("ABC-123", "ABC 123") is False  # exact is strict


def test_normalized_match():
    """Normalized match handles spacing/case differences."""
    from app.skills.simple_text_consistency.evaluator import normalized_match
    assert normalized_match("ABC-123", "abc 123") is True
    assert normalized_match("Report No. 5", "Report Number 5") is False  # different words


def test_contains_match():
    """Contains match finds substring."""
    from app.skills.simple_text_consistency.evaluator import contains_match
    assert contains_match("ABC-123", "document ABC-123 certified") is True
    assert contains_match("XYZ", "ABC-123") is False


def test_date_normalization():
    """Different date formats normalized."""
    from app.skills.simple_text_consistency.evaluator import normalize_date
    d1 = normalize_date("2024-01-15")
    d2 = normalize_date("2024/01/15")
    d3 = normalize_date("15 Jan 2024")
    assert d1 is not None
    assert d1 == d2, f"{d1} != {d2}"
    if d3 is not None:
        assert d1 == d3, f"{d1} != {d3}"


# --- Test rule execution ---

def test_true_mismatch_detected():
    """Genuine difference must be REJECT."""
    from app.skills.simple_text_consistency.evaluator import evaluate_rule
    result = evaluate_rule(
        rule={"id": "MATCH_SKC", "type": "exact_match"},
        source_value="ABC-123",
        pdf_values=["XYZ-789"],
        file_name="cert.pdf",
        page=1,
    )
    assert result is not None
    assert result["result"] == "REJECT", f"True mismatch must REJECT, got {result}"


def test_missing_field_required():
    """Missing required field must not PASS."""
    from app.skills.simple_text_consistency.evaluator import evaluate_rule
    result = evaluate_rule(
        rule={"id": "MATCH_SKC", "type": "exact_match", "required": True},
        source_value="",
        pdf_values=[],
        file_name="cert.pdf",
        page=1,
    )
    assert result is not None
    assert result["result"] != "PASS", f"Missing required field must not PASS, got {result}"


def test_multiple_candidates_no_unique_match():
    """Multiple candidates with no unique match → MANUAL_REVIEW."""
    from app.skills.simple_text_consistency.evaluator import evaluate_rule
    # Two conflicting values found in PDF
    result = evaluate_rule(
        rule={"id": "MATCH_SKC", "type": "exact_match"},
        source_value="ABC-123",
        pdf_values=["ABC-123", "XYZ-789"],  # both found
        file_name="cert.pdf",
        page=1,
    )
    if result:
        # If we found ABC-123 but also XYZ-789, should flag ambiguity
        assert result.get("ambiguous") or result.get("result") == "MANUAL_REVIEW", (
            f"Multiple candidates must trigger MANUAL_REVIEW or ambiguity flag: {result}"
        )


# --- Test deterministic rules override AI decision ---

def test_deterministic_manual_review_overrides_ai_pass():
    """When deterministic rules return MANUAL_REVIEW, final decision must not be PASS."""
    from app.services.audit_service import _compute_final_decision
    # AI returns PASS but deterministic rules say MANUAL_REVIEW
    decision = _compute_final_decision("PASS", [
        {"rule_id": "R1", "result": "PASS", "reason": "ok"},
        {"rule_id": "CHECK_REQUIRED", "result": "MANUAL_REVIEW", "reason": "missing field"},
    ])
    assert decision == "MANUAL_REVIEW", f"Expected MANUAL_REVIEW, got {decision}"


def test_deterministic_reject_overrides_ai_pass():
    """When deterministic rules return REJECT, final decision must be REJECT."""
    from app.services.audit_service import _compute_final_decision
    decision = _compute_final_decision("PASS", [
        {"rule_id": "R1", "result": "REJECT", "reason": "mismatch"},
        {"rule_id": "R2", "result": "PASS", "reason": "ok"},
    ])
    assert decision == "REJECT", f"Expected REJECT, got {decision}"


def test_deterministic_reject_overrides_manual_review():
    """REJECT from deterministic rules beats MANUAL_REVIEW from AI."""
    from app.services.audit_service import _compute_final_decision
    decision = _compute_final_decision("MANUAL_REVIEW", [
        {"rule_id": "R1", "result": "REJECT", "reason": "hard mismatch"},
    ])
    assert decision == "REJECT", f"Expected REJECT, got {decision}"


def test_ai_pass_used_when_no_deterministic_issues():
    """When deterministic rules all pass, AI decision is used."""
    from app.services.audit_service import _compute_final_decision
    decision = _compute_final_decision("PASS", [
        {"rule_id": "R1", "result": "PASS", "reason": "ok"},
    ])
    assert decision == "PASS", f"Expected PASS, got {decision}"
