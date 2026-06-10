import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent
MANIFESTS_DIR = SKILLS_DIR / "manifests"
PROMPTS_DIR = SKILLS_DIR / "prompts"


class SkillMatch:
    def __init__(
        self,
        skill_id: str,
        version: str,
        priority: int,
        prompt_content: str,
        prompt_hash: str,
    ):
        self.skill_id = skill_id
        self.version = version
        self.priority = priority
        self.prompt_content = prompt_content
        self.prompt_hash = prompt_hash


def load_manifests() -> list[dict[str, Any]]:
    manifests = []
    for path in MANIFESTS_DIR.glob("*.yaml"):
        with open(path) as f:
            data = yaml.safe_load(f)
            if data.get("enabled", True):
                manifests.append(data)
    return manifests


def compute_prompt_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def load_prompt(filename: str) -> tuple[str, str]:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {filename}")
    content = path.read_text()
    return content, compute_prompt_hash(content)


def match_skill(order: dict[str, Any]) -> SkillMatch | None:
    manifests = load_manifests()
    matches: list[tuple[dict[str, Any], int]] = []

    scene_id = str(order.get("scene_id", ""))
    audit_point_id = str(order.get("audit_point_id", ""))
    certificate_type_id = str(order.get("certificate_type_id", ""))
    industry_ids = [str(i) for i in order.get("industry_id_list", [])]
    category_ids = [str(c) for c in order.get("category_all_level_ids", [])]
    business_type = order.get("business_type", "")

    for mf in manifests:
        score = _score_match(
            mf, scene_id, audit_point_id, certificate_type_id,
            industry_ids, category_ids, business_type,
        )
        if score > 0:
            matches.append((mf, score))

    if not matches:
        logger.info("No skill matched — manual required")
        return None

    matches.sort(key=lambda x: x[1], reverse=True)
    best_score = matches[0][1]

    tied = [m for m in matches if m[1] == best_score]
    if len(tied) > 1:
        names = [t[0]["skill_id"] for t in tied]
        logger.warning(
            "Multiple skills tied at priority=%s: %s — manual required",
            best_score, names,
        )
        return None

    best = tied[0][0]
    prompt_content, prompt_hash = load_prompt(best["prompt_file"])

    return SkillMatch(
        skill_id=best["skill_id"],
        version=str(best.get("version", "0.0.0")),
        priority=best.get("priority", 0),
        prompt_content=prompt_content,
        prompt_hash=prompt_hash,
    )


def _score_match(
    mf: dict[str, Any],
    scene_id: str,
    audit_point_id: str,
    certificate_type_id: str,
    industry_ids: list[str],
    category_ids: list[str],
    business_type: str,
) -> int:
    score = 0
    match = mf.get("match", {})

    mf_scene = [str(s) for s in match.get("scene_ids", [])]
    mf_ap = [str(a) for a in match.get("audit_point_ids", [])]
    mf_ct = [str(c) for c in match.get("certificate_type_ids", [])]
    mf_ind = [str(i) for i in match.get("industry_ids", [])]
    mf_cat = [str(c) for c in match.get("category_ids", [])]

    if mf_scene and scene_id in mf_scene:
        score += 10
    if mf_ap and audit_point_id in mf_ap:
        score += 10
    if mf_ct and certificate_type_id in mf_ct:
        score += 10
    if mf_ind and any(i in mf_ind for i in industry_ids):
        score += 5
    if mf_cat and any(c in mf_cat for c in category_ids):
        score += 5
    if business_type and match.get("business_type") == business_type:
        score += 15

    if score == 0 and not mf_scene and not mf_ap and not mf_ct and not mf_ind and not mf_cat:
        score = mf.get("priority", 100)

    return score
