import json
import logging
import queue

from flask import Blueprint, Response, jsonify, session, stream_with_context

from src.dashboard_helper import build_dashboard_json_snapshot
from src.dashboard_events import build_sse_snapshot, dashboard_events_hub

_LOGGER = logging.getLogger(__name__)

api_dashboard_bp = Blueprint('api_dashboard', __name__)


@api_dashboard_bp.route('/api/dashboard')
def get_dashboard_snapshot():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    snapshot = build_dashboard_json_snapshot()
    return jsonify({
        'success': True,
        'users': snapshot['users'],
        'pending_adjustments': snapshot['pending_adjustments'],
        'pending_approvals': snapshot.get(
            'pending_approvals',
            {'total': 0, 'by_user': {}, 'items': []},
        ),
    })


@api_dashboard_bp.route('/api/dashboard/events')
def dashboard_events():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    def generate():
        subscriber_queue = dashboard_events_hub.subscribe()
        try:
            initial_payload = build_sse_snapshot(reason='connected')
            yield _format_sse_event('snapshot', initial_payload)

            while True:
                try:
                    payload = subscriber_queue.get(timeout=30)
                except queue.Empty:
                    yield ': keepalive\n\n'
                    continue

                yield _format_sse_event('snapshot', payload)
        finally:
            dashboard_events_hub.unsubscribe(subscriber_queue)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


def _format_sse_event(event_name, payload):
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
