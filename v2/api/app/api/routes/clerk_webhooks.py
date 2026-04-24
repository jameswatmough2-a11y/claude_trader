"""Clerk webhooks — called when a user signs up / signs in / is deleted.

Signed with a shared secret (Svix HMAC, per Clerk docs). We validate the
signature before trusting the payload.

On `user.created`: insert a User row + Tenant row. (current_tenant also
lazy-creates, so this is belt-and-braces.)
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks/clerk", tags=["webhooks"])


@router.post("")
async def clerk_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    svix_id: Annotated[str | None, Header(alias="svix-id")] = None,
    svix_timestamp: Annotated[str | None, Header(alias="svix-timestamp")] = None,
    svix_signature: Annotated[str | None, Header(alias="svix-signature")] = None,
) -> dict:
    raw = await request.body()
    # TODO: verify Svix signature using settings.clerk_webhook_secret
    #       (use the `svix` package or a manual HMAC-SHA256 check)
    if not settings.clerk_webhook_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook secret not configured")

    # TODO: parse event type from payload
    # TODO: on 'user.created' -> insert User + Tenant
    # TODO: on 'user.deleted' -> soft-delete or anonymise
    return {"ok": True}
