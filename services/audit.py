# Append-only by convention: call log_admin_action right before commit()
# so the audit row and the business change land atomically.
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
    # action: `domain.verb` (e.g. `caterer.validate`). Keep `extra` < 2 KB.
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

    # Mirror the audit row to stdout so Datadog (and the operational log
    # stream) sees admin actions in real time. The DB row stays canonical
    # for forensic queries — this is the observability copy.
    #
    # Deliberately omit `extra={}` from the stdout payload: callers pass
    # values like target email or free-text rejection reasons that are PII
    # and have a different retention/access contract in DB vs. Datadog.
    # Operators who need those details query the AuditLog row by
    # `action + ts + actor_id`.
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
