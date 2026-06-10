import logging
from typing import Any

from app.skills.registry import SkillMatch, match_skill

logger = logging.getLogger(__name__)


async def route_order(order_snapshot: dict[str, Any], business_type: str | None = None) -> SkillMatch | None:
    order_for_match = dict(order_snapshot)
    if business_type:
        order_for_match["business_type"] = business_type

    skill = match_skill(order_for_match)

    if skill:
        logger.info(
            "Routed order to skill_id=%s version=%s priority=%s",
            skill.skill_id, skill.version, skill.priority,
        )
    else:
        logger.info("No skill matched for order")

    return skill
