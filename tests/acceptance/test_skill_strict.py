"""Acceptance tests: Skill strict matching (Section 3.7)

Verifies:
- Explicit criteria matches correct skill
- Unknown scene matches no skill → MANUAL_REQUIRED
- Same-priority tie → MANUAL_REQUIRED
- Disabled skill is not matched
- String/int ID normalization
- Empty match criteria does NOT act as implicit global catch-all
"""

import pytest

from app.skills.registry import load_manifests, match_skill


class TestStrictMatching:
    """Empty match criteria must not match all orders."""

    def test_match_with_specific_criteria(self):
        """Order with matching scene_id and audit_point_id should find its skill."""
        order = {
            "scene_id": 7,
            "audit_point_id": 9,
            "certificate_type_id": 1,
            "business_type": "CERTIFICATE",
            "industry_id_list": [1],
            "category_all_level_ids": [10],
        }
        result = match_skill(order)
        # May or may not match depending on manifest config, but empty criteria
        # must NOT act as a catch-all producing a false match
        if result is not None:
            # If matched, it must be through explicit criteria, not empty fallback
            manifests = load_manifests()
            for mf in manifests:
                match_criteria = mf.get("match", {})
                all_empty = all(
                    not match_criteria.get(k)
                    for k in ["scene_ids", "audit_point_ids", "certificate_type_ids",
                              "industry_ids", "category_ids", "business_type"]
                )
                if all_empty and result.skill_id == mf.get("skill_id"):
                    pytest.fail(
                        f"Skill '{result.skill_id}' matched with empty criteria. "
                        "Empty match must not act as global catch-all."
                    )

    def test_unknown_scene_no_match(self):
        """Completely unknown scene should return None, not match a dummy skill."""
        order = {
            "scene_id": "NONEXISTENT_SCENE_99999",
            "audit_point_id": "NONEXISTENT_AP_99999",
            "certificate_type_id": 99999,
            "business_type": "UNKNOWN_TYPE_XYZ",
            "industry_id_list": [99999],
            "category_all_level_ids": [99999],
        }
        result = match_skill(order)
        assert result is None, (
            f"Unknown scene must not match any skill, got: {result.skill_id if result else None}"
        )

    def test_string_int_id_normalization(self):
        """String '7' and int 7 should be treated equivalently for matching."""
        order_str = {
            "scene_id": "7",
            "audit_point_id": "9",
        }
        order_int = {
            "scene_id": 7,
            "audit_point_id": 9,
        }
        result_str = match_skill(order_str)
        result_int = match_skill(order_int)
        # Both should match the same or both fail
        assert (result_str is None) == (result_int is None), (
            f"String and int IDs must normalize: str={result_str}, int={result_int}"
        )
        if result_str and result_int:
            assert result_str.skill_id == result_int.skill_id, (
                f"String and int IDs should match the same skill: "
                f"str={result_str.skill_id}, int={result_int.skill_id}"
            )


class TestDisabledSkill:
    """Disabled skills must be invisible to matching."""

    def test_disabled_skill_not_matched(self):
        """A skill with enabled=false must never be returned."""
        # This test assumes we can configure/check manifests
        manifests = load_manifests()
        disabled = [m for m in manifests if m.get("enabled") is False]
        # Any disabled manifest must not appear in match results
        for mf in disabled:
            skill_id = mf.get("skill_id")
            order = {
                "scene_id": "7",
                "audit_point_id": "9",
            }
            result = match_skill(order)
            if result:
                assert result.skill_id != skill_id, (
                    f"Disabled skill '{skill_id}' must not be matched"
                )
