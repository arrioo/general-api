from fastapi import Depends, Header, HTTPException

from .db import get_conn


async def get_current_user(
    x_user_id: int = Header(..., description="DEMO AUTH: replace with JWT/session in production"),
    conn=Depends(get_conn),
) -> dict:
    row = await conn.fetchrow("SELECT id, name, email, role FROM users WHERE id = $1", x_user_id)
    if not row:
        raise HTTPException(401, "Unknown user")
    return dict(row)


def require_roles(*roles: str):
    async def checker(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return user

    return checker


staff_only = require_roles("staff", "admin")
