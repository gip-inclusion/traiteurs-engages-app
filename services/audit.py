from __future__ import annotations

import logging
import uuid
from typing import Any

from flask import has_request_context, request

from models import AuditLog, User

_audit_logger = logging.getLogger("app.audit")


def log_admin_action(
    db,
    actor: User | None,
    action: str,
    *,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    ip = None
    ua = None
    if has_request_context():
        ip = request.remote_addr
        ua = (request.user_agent.string or "")[:500] or None

    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            actor_email=actor.email if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            extra=extra,
            ip_address=ip,
            user_agent=ua,
        )
    )

    _audit_logger.info(
        "admin_action",
        extra={
            "event": "admin_action",
            "action": action,
            "actor_id": str(actor.id) if actor else None,
            "target_type": target_type,
            "target_id": str(target_id) if target_id else None,
        },
    )
