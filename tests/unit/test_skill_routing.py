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
