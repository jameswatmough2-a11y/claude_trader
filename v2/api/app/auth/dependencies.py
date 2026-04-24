"""FastAPI dependencies for auth + DB session + tenant scoping.

Usage in a route:

    @router.get("/decisions")
    def list_decisions(
        tenant: Tenant = Depends(current_tenant),
        db: Session = Depends(get_db),
    ): ...

`current_tenant` is the main gate — it both authenticates the user and
resolves the tenant they belong to. Every per-user route should depend on it.
"""
from __future__ import annotations

from typing import Annotated, Generator

from fastapi import Depends, Header, HTTPException, status
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.auth.clerk import verify_token
from app.db.session import SessionLocal

# Forward-ref imports done inside functions to avoid circulars during scaffold.


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        return verify_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}")


def current_tenant(
    claims: Annotated[dict, Depends(current_user_claims)],
    db: Annotated[Session, Depends(get_db)],
):
    """Resolve the signed-in Clerk user to their Tenant row.

    For v1 multi-tenant each user owns exactly one tenant. If the tenant row
    does not exist yet (webhook hasn't fired), create it lazily.
    """
    from app.models.user import User
    from app.models.tenant import Tenant

    clerk_user_id = claims.get("sub")
    if not clerk_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing sub claim")

    user = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
    if user is None:
        # Lazy-create: Clerk webhook may not have landed yet
        user = User(clerk_user_id=clerk_user_id, email=claims.get("email", ""))
        db.add(user)
        db.flush()

    tenant = db.query(Tenant).filter(Tenant.user_id == user.id).first()
    if tenant is None:
        tenant = Tenant(user_id=user.id)
        db.add(tenant)
        db.flush()
        db.commit()

    return tenant
