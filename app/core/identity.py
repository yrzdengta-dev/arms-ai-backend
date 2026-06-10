import logging

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)


async def get_current_user(
    x_arms_user: str = Header(default="", alias="X-ARMS-User"),
    db: AsyncSession = Depends(get_db),
) -> User:
    account = x_arms_user.strip()
    if not account or len(account) > 128:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-ARMS-User header",
        )

    result = await db.execute(select(User).where(User.arms_account == account))
    user = result.scalars().first()

    if user is None:
        user = User(arms_account=account)
        db.add(user)
        await db.flush()
        await db.refresh(user)
        logger.info("Auto-created user arms_account=%s id=%s", account, user.id)

    return user


async def get_current_user_id(
    x_arms_user: str = Header(default="", alias="X-ARMS-User"),
    db: AsyncSession = Depends(get_db),
) -> str:
    user = await get_current_user(x_arms_user=x_arms_user, db=db)
    return str(user.id)
