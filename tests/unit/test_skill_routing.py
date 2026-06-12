"""Unit tests: Skill routing (tests 16-20)"""

from app.skills.registry import match_skill


def test_default_skill_matches_generic_order():
    """simple_text_consistency should match scene_id=7, audit_point_id=9, certificate_type_id=1."""
    order = {"scene_id": "7", "audit_point_id": "9", "certificate_type_id": "1"}
    skill = match_skill(order)
    assert skill is not None, "simple_text_consistency should match scene=7, ap=9, ct=1"
    assert skill.skill_id == "simple_text_consistency"
    assert skill.version == "1.0.0"


def test_skill_prompt_hash_is_computed():
    """Matched skill must have a non-empty prompt hash."""
    order = {"scene_id": "7", "audit_point_id": "9", "certificate_type_id": "1"}
    skill = match_skill(order)
    assert skill is not None, "simple_text_consistency should match"
    assert skill.prompt_hash is not None
    assert len(skill.prompt_hash) > 0


def test_no_match_when_no_skills_enabled():
    """When no skills match the criteria, returns None (was previously catch-all)."""
    order = {"scene_id": "nonexistent", "audit_point_id": "999"}
    skill = match_skill(order)
    # With empty match on disabled catch-all and strict matching on simple_text_consistency,
    # orders that don't meet any criteria should get no match
    assert skill is None, (
        "Orders with no matching criteria should return None "
        "(no more catch-all skill)"
    )


def test_skill_version_in_result():
    """Matched skill must have a version."""
    order = {"scene_id": "7", "audit_point_id": "9", "certificate_type_id": "1"}
    skill = match_skill(order)
    assert skill is not None, "simple_text_consistency should match"
    assert skill.version is not None


def test_partial_match_rejected_when_multi_criteria():
    """When manifest specifies multiple criteria, partial match must not win."""
    # Manifest requires scene=7 AND ap=9 AND ct=1
    # Order only matches scene, not ap or ct
    order = {"scene_id": "7", "audit_point_id": "99", "certificate_type_id": "99"}
    skill = match_skill(order)
    assert skill is None, (
        f"Partial match (scene only) should NOT match when manifest requires ap and ct. "
        f"Got: {skill.skill_id if skill else None}"
    )


def test_all_criteria_match_when_only_one_specified():
    """When manifest only specifies one criterion, matching it should work."""
    from app.skills.registry import _score_match
    # A manifest that only specifies scene_ids
    mf = {
        "match": {
            "scene_ids": ["7"],
            "audit_point_ids": [],
            "certificate_type_ids": [],
            "industry_ids": [],
            "category_ids": [],
        }
    }
    score = _score_match(mf, "7", "", "", [], [], "")
    assert score > 0, f"Should match when only one criterion and it matches"


def test_partial_match_returns_zero_when_any_specified_criterion_fails():
    """When manifest specifies N criteria and any one fails, score must be 0."""
    from app.skills.registry import _score_match
    mf = {
        "match": {
            "scene_ids": ["7"],
            "audit_point_ids": ["9"],
            "certificate_type_ids": ["1"],
            "industry_ids": [],
            "category_ids": [],
        }
    }
    # scene matches but audit_point does not
    score = _score_match(mf, "7", "99", "1", [], [], "")
    assert score == 0, f"Partial match should return 0, got {score}"
