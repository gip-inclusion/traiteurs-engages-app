import logging
from datetime import datetime

import stripe
from flask import flash, g, redirect, render_template, url_for

from blueprints.middleware import login_required, role_required
from database import get_db
from extensions import limiter
from services.stripe_service import (
    create_account_link,
    create_connect_account,
    get_account,
)

logger = logging.getLogger(__name__)


def register(bp):
    @bp.route("/stripe")
    @login_required
    @role_required("caterer")
    def stripe_status():
        caterer = g.current_user.caterer
        if caterer.stripe_account_id:
            try:
                status = get_account(caterer.stripe_account_id)
                db = get_db()
                db.add(caterer)
                caterer.stripe_charges_enabled = status["charges_enabled"]
                caterer.stripe_payouts_enabled = status["payouts_enabled"]
                db.commit()
            except stripe.StripeError:
                logger.exception("Failed to fetch Stripe account status")
        return render_template(
            "caterer/stripe.html", user=g.current_user, caterer=caterer
        )

    @bp.route("/stripe/onboard", methods=["POST"])
    @limiter.limit("5 per minute")
    @login_required
    @role_required("caterer")
    def stripe_onboard():
        caterer = g.current_user.caterer
        db = get_db()
        db.add(caterer)
        if not caterer.stripe_account_id:
            result = create_connect_account(caterer)
            caterer.stripe_account_id = result["id"]
            logger.info(
                "stripe_account_created",
                extra={
                    "event": "stripe_account_created",
                    "caterer_id": str(caterer.id),
                    "stripe_account_id": caterer.stripe_account_id,
                },
            )
        refresh_url = url_for("caterer.stripe_status", _external=True)
        return_url = url_for("caterer.stripe_complete", _external=True)
        link_url = create_account_link(
            caterer.stripe_account_id, refresh_url, return_url
        )
        db.commit()
        return redirect(link_url)

    @bp.route("/stripe/complete")
    @login_required
    @role_required("caterer")
    def stripe_complete():
        caterer = g.current_user.caterer
        if caterer.stripe_account_id:
            try:
                status = get_account(caterer.stripe_account_id)
                db = get_db()
                db.add(caterer)
                caterer.stripe_charges_enabled = status["charges_enabled"]
                caterer.stripe_payouts_enabled = status["payouts_enabled"]
                # Only consider the onboarding "completed" the first time
                # both flags flip true — re-visits to /stripe/complete are
                # idempotent in DB, and logging only on the transition
                # avoids one event per refresh.
                first_completion = (
                    status["charges_enabled"]
                    and status["payouts_enabled"]
                    and caterer.stripe_onboarded_at is None
                )
                if status["charges_enabled"] and status["payouts_enabled"]:
                    caterer.stripe_onboarded_at = datetime.utcnow()
                    flash("Compte Stripe connecte avec succes.", "success")
                else:
                    flash(
                        "Verification en cours. Certaines fonctionnalites ne sont pas encore actives.",
                        "warning",
                    )
                db.commit()
                if first_completion:
                    logger.info(
                        "stripe_onboarding_completed",
                        extra={
                            "event": "stripe_onboarding_completed",
                            "caterer_id": str(caterer.id),
                            "stripe_account_id": caterer.stripe_account_id,
                        },
                    )
            except stripe.StripeError:
                logger.exception("Failed to verify Stripe account on completion")
                flash("Erreur lors de la verification du compte Stripe.", "error")
        return redirect(url_for("caterer.stripe_status"))
