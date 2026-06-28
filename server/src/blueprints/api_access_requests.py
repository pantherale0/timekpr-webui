"""API blueprint for Guardian Space access requests forwarded from managed clients."""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from src.models import AgentAlert, AgentDevice, db
from src.agent.helper import normalize_agent_alert_payload
from src.common.settings import _get_alert_webhook_settings

_LOGGER = logging.getLogger(__name__)

bp = Blueprint("api_access_requests", __name__, url_prefix="/api/access-request")


@bp.route("", methods=["POST"])
def submit_access_request():
    """
    Receive an access request forwarded from a managed client (child tapped a preset button
    on the Guardian Space blocked overlay). Stores it as an ``access_requested`` AgentAlert
    so it appears on the parent dashboard.

    Request body JSON:
        system_id (str)      – device UUID
        linux_username (str) – account on that device
        reason (str)         – overlay reason: sleep | filtered | locked | signup
        message (str)        – the message the child typed / selected
    """
    data = request.get_json(silent=True) or {}
    system_id = (data.get("system_id") or "").strip()
    linux_username = (data.get("linux_username") or "").strip()
    reason = (data.get("reason") or "").strip()
    message = (data.get("message") or "").strip()

    if not system_id or not linux_username:
        return jsonify({"success": False, "message": "system_id and linux_username are required"}), 400

    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({"success": False, "message": "Unknown device"}), 404

    try:
        payload = normalize_agent_alert_payload(system_id, {
            "event_type": "access_requested",
            "linux_username": linux_username,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "details": {
                "reason": reason,
                "message": message,
                "request_type": "guardian_overlay",
            },
        })
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    webhook_config = _get_alert_webhook_settings()

    alert = AgentAlert(
        system_id=system_id,
        event_type=payload["event_type"],
        linux_username=payload["linux_username"],
        occurred_at=payload["occurred_at"],
        payload_json=payload["payload_json"],
        webhook_enabled_snapshot=webhook_config.get("is_active", False),
    )
    db.session.add(alert)
    db.session.commit()

    _LOGGER.info(
        "Guardian Space access request from %s@%s: reason=%s",
        linux_username,
        system_id,
        reason,
    )

    return jsonify({"success": True, "alert_id": alert.id}), 201


@bp.route("", methods=["GET"])
def list_access_requests():
    """
    Return recent access_requested alerts for the parent dashboard.

    Query params:
        system_id (str, optional) – filter by device
        limit     (int, optional) – max results, capped at 200 (default 50)
    """
    from flask import session, abort
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401

    system_id = request.args.get("system_id")
    limit = min(int(request.args.get("limit", 50)), 200)

    parent_id = session.get('parent_account_id')
    if not parent_id:
        from src.models import ParentAccount
        p = ParentAccount.query.filter_by(email='admin@local').first()
        if p:
            parent_id = p.id

    hh_ids = []
    if parent_id:
        from src.models import ParentAccount
        p = ParentAccount.query.get(parent_id)
        if p:
            hh_ids = [m.household_id for m in p.memberships if m.household_id]

    if system_id:
        from src.common.helpers import parent_has_access_to_device
        if not parent_id or not parent_has_access_to_device(parent_id, system_id):
            abort(403)

    query = AgentAlert.query.filter_by(event_type="access_requested")
    if system_id:
        query = query.filter_by(system_id=system_id)
    elif hh_ids:
        query = query.join(AgentDevice, AgentDevice.system_id == AgentAlert.system_id).filter(
            AgentDevice.household_id.in_(hh_ids)
        )
    else:
        # If no households, return empty list to prevent leakage
        return jsonify([])

    alerts = query.order_by(AgentAlert.occurred_at.desc()).limit(limit).all()

    return jsonify([
        {
            "id": a.id,
            "system_id": a.system_id,
            "linux_username": a.linux_username,
            "occurred_at": a.occurred_at.isoformat() + "Z",
            "details": a.payload.get("details", {}),
        }
        for a in alerts
    ])
