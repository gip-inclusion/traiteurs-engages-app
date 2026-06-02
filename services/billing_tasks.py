from __future__ import annotations

import logging
import os
import uuid

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import Middleware
import stripe
from sqlalchemy import select

from logging_config import (
    bind,
    get_trace_context,
    reset_trace_context,
    set_trace_context,
)

logger = logging.getLogger(__name__)


class TraceMiddleware(Middleware):
    def before_enqueue(self, broker, message, delay):
        ctx = get_trace_context()
        tid = ctx.get("trace_id")
        if tid:
            message.options.setdefault("trace_id", tid)
            sid = ctx.get("span_id")
            if sid:
                message.options.setdefault("parent_span_id", sid)

    def before_process_message(self, broker, message):
        tid = message.options.get("trace_id")
        set_trace_context(tid)
        bind(
            actor=message.actor_name,
            message_id=message.message_id,
            queue=message.queue_name,
        )

    def after_process_message(self, broker, message, *, result=None, exception=None):
        reset_trace_context()


def _make_broker():
    if os.getenv("DRAMATIQ_TESTING") == "1":
        broker_obj = StubBroker()
    else:
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            raise RuntimeError(
                "REDIS_URL is not set. The web process and the worker both "
                "need it. Check docker-compose.yml (local) or the Scalingo "
                "Redis addon (REDIS_URL is auto-injected once provisioned)."
            )
        broker_obj = RedisBroker(url=redis_url)
    broker_obj.add_middleware(TraceMiddleware())
    return broker_obj


broker = _make_broker()
dramatiq.set_broker(broker)


@dramatiq.actor(
    max_retries=5,
    min_backoff=30_000,
    max_backoff=8 * 60_000,
    throws=(),
)
def send_invoice_for_order(order_id: str) -> None:
    from database import get_session
    from models import Order, OrderStatus
    from services.stripe_service import create_invoice_for_order

    oid = uuid.UUID(order_id)
    with get_session() as db:
        order = db.scalar(select(Order).where(Order.id == oid))
        if not order:
            logger.error("send_invoice_for_order: order %s not found", oid)
            return
        if order.status not in (OrderStatus.invoicing, OrderStatus.delivered):
            logger.info(
                "send_invoice_for_order: order %s in status %s, skipping",
                oid,
                order.status,
            )
            return

        try:
            create_invoice_for_order(db, order)
        except stripe.StripeError:
            logger.exception(
                "send_invoice_for_order: Stripe call failed for order %s",
                oid,
            )
            raise
