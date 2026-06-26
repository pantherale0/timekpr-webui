"""
In-process SSE hub for dashboard live updates.

Assumes a single Gunicorn worker (current Docker default). If workers are scaled
beyond 1, use Redis/pub-sub or similar for cross-worker broadcast.
"""

import json
import logging
import queue
import threading
from datetime import datetime, timezone

from src.common.dashboard_helper import build_dashboard_json_snapshot

_LOGGER = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 0.3
_KEEPALIVE_TIMEOUT_SECONDS = 30


class DashboardEventsHub:
    """Thread-safe pub/sub hub with debounced snapshot broadcasts."""

    def __init__(self, debounce_seconds=_DEBOUNCE_SECONDS):
        self._lock = threading.Lock()
        self._subscribers = set()
        self._debounce_seconds = debounce_seconds
        self._pending_reason = None
        self._debounce_timer = None

    def subscribe(self):
        subscriber_queue = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber_queue)
        return subscriber_queue

    def unsubscribe(self, subscriber_queue):
        with self._lock:
            self._subscribers.discard(subscriber_queue)

    def notify_dashboard_changed(self, reason='updated'):
        with self._lock:
            self._pending_reason = reason
            if self._debounce_timer is not None:
                return
            self._debounce_timer = threading.Timer(
                self._debounce_seconds,
                self._flush_pending_broadcast,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _flush_pending_broadcast(self):
        with self._lock:
            reason = self._pending_reason or 'updated'
            self._pending_reason = None
            self._debounce_timer = None
            subscribers = list(self._subscribers)

        if not subscribers:
            return

        try:
            from app import app
            with app.app_context():
                payload = build_sse_snapshot(reason=reason)
        except Exception:
            _LOGGER.exception("Failed to build dashboard SSE snapshot")
            return

        for subscriber_queue in subscribers:
            try:
                subscriber_queue.put_nowait(payload)
            except queue.Full:
                _LOGGER.warning("Dashboard SSE subscriber queue full; dropping update")

    def build_sse_snapshot(self, reason='updated'):
        return build_sse_snapshot(reason=reason)


def build_sse_snapshot(reason='updated'):
    snapshot = build_dashboard_json_snapshot()
    return {
        'type': 'snapshot',
        'reason': reason,
        'ts': datetime.now(timezone.utc).isoformat(),
        'users': snapshot['users'],
        'pending_adjustments': snapshot['pending_adjustments'],
        'pending_approvals': snapshot.get(
            'pending_approvals',
            {'total': 0, 'by_user': {}, 'items': []},
        ),
    }


dashboard_events_hub = DashboardEventsHub()


def notify_dashboard_changed(reason='updated'):
    dashboard_events_hub.notify_dashboard_changed(reason=reason)
