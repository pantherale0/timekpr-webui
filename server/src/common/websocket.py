"""WebSocket helpers for the Flask agent hub."""

from __future__ import annotations

from wsproto.events import (
    AcceptConnection,
    BytesMessage,
    CloseConnection,
    Ping,
    Pong,
    Request,
    TextMessage,
)
from wsproto.frame_protocol import CloseReason
from wsproto.utilities import LocalProtocolError


def disable_permessage_deflate_handshake() -> None:
    """Stop simple-websocket from accepting permessage-deflate.

    simple-websocket unconditionally negotiates ``PerMessageDeflate`` during the
    server handshake. OkHttp on Android then enables ``MessageDeflater``, which
    can throw uncaught ``NullPointerException`` / ``IllegalArgumentException``
    when a connection fails and OkHttp tears the socket down (square/okhttp#6719,
    square/okio#1392). Agent payloads are small JSON; compression buys little here.
    """
    import simple_websocket.ws as sw_ws

    if getattr(sw_ws.Server, '_guardian_no_deflate', False):
        return

    def _handle_events_without_deflate(self):
        keep_going = True
        out_data = b''
        for event in self.ws.events():
            try:
                if isinstance(event, Request):
                    self.subprotocol = self.choose_subprotocol(event)
                    out_data += self.ws.send(AcceptConnection(
                        subprotocol=self.subprotocol,
                        extensions=[],
                    ))
                elif isinstance(event, CloseConnection):
                    if self.is_server:
                        out_data += self.ws.send(event.response())
                    self.close_reason = event.code
                    self.close_message = event.reason
                    self.connected = False
                    self.event.set()
                    keep_going = False
                elif isinstance(event, Ping):
                    out_data += self.ws.send(event.response())
                elif isinstance(event, Pong):
                    self.pong_received = True
                elif isinstance(event, (TextMessage, BytesMessage)):
                    self.incoming_message_len += len(event.data)
                    if self.max_message_size and \
                            self.incoming_message_len > self.max_message_size:
                        out_data += self.ws.send(CloseConnection(
                            CloseReason.MESSAGE_TOO_BIG, 'Message is too big'))
                        self.event.set()
                        keep_going = False
                        break
                    if self.incoming_message is None:
                        self.incoming_message = event.data
                    elif isinstance(event, TextMessage):
                        if not isinstance(self.incoming_message, bytearray):
                            self.incoming_message = bytearray(
                                (self.incoming_message + event.data).encode())
                        else:
                            self.incoming_message += event.data.encode()
                    else:
                        if not isinstance(self.incoming_message, bytearray):
                            self.incoming_message = bytearray(
                                self.incoming_message + event.data)
                        else:
                            self.incoming_message += event.data
                    if not event.message_finished:
                        continue
                    if isinstance(self.incoming_message, (str, bytes)):
                        self.input_buffer.append(self.incoming_message)
                    elif isinstance(event, TextMessage):
                        self.input_buffer.append(
                            self.incoming_message.decode())
                    else:
                        self.input_buffer.append(bytes(self.incoming_message))
                    self.incoming_message = None
                    self.incoming_message_len = 0
                    self.event.set()
                else:  # pragma: no cover
                    pass
            except LocalProtocolError:  # pragma: no cover
                out_data = b''
                self.event.set()
                keep_going = False
        if out_data:
            self.sock.send(out_data)
        return keep_going

    sw_ws.Server._handle_events = _handle_events_without_deflate
    sw_ws.Server._guardian_no_deflate = True
