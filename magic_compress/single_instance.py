"""Single-instance coordination via a shared-memory lock + a local socket.

The first process to start becomes the *primary* and owns a QLocalServer.
Later launches (e.g. Explorer firing the "Add to archive…" verb once per
selected file) connect to it, forward their action as JSON, and exit. The
primary raises its window and dispatches the forwarded action — which lets it
coalesce a burst of single-file "add" invocations into one Create dialog.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, QSharedMemory, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    message_received = Signal(dict)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        # QSharedMemory.create() succeeds for exactly one process → clean election.
        self._lock = QSharedMemory(key + "-lock")
        self.is_primary = False

        if self._lock.attach():
            self.is_primary = False          # someone else holds the lock
        elif self._lock.create(1):
            self.is_primary = True
            self._start_server()
        else:
            # Couldn't attach or create (unexpected) — behave as a standalone.
            self.is_primary = True
            self._start_server()

    def _start_server(self) -> None:
        QLocalServer.removeServer(self._key)  # clear any stale socket file
        self._server = QLocalServer(self)
        self._server.listen(self._key)
        self._server.newConnection.connect(self._on_new_connection)

    def send(self, payload: dict, retries: int = 12) -> bool:
        """Secondary → primary. Returns True if the message was delivered."""
        data = json.dumps(payload).encode("utf-8")
        for _ in range(retries):
            sock = QLocalSocket()
            sock.connectToServer(self._key)
            if sock.waitForConnected(150):
                sock.write(data)
                sock.flush()
                sock.waitForBytesWritten(1000)
                sock.disconnectFromServer()
                if sock.state() != QLocalSocket.UnconnectedState:
                    sock.waitForDisconnected(500)
                return True
            sock.abort()
        return False

    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        buffer = bytearray()

        def on_ready() -> None:
            buffer.extend(bytes(conn.readAll()))

        def on_disconnected() -> None:
            buffer.extend(bytes(conn.readAll()))  # grab data not yet read
            try:
                payload = json.loads(bytes(buffer).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = None
            if isinstance(payload, dict):
                self.message_received.emit(payload)
            conn.deleteLater()

        conn.readyRead.connect(on_ready)
        conn.disconnected.connect(on_disconnected)
        # Data may already be buffered by the time we accept the connection.
        if conn.bytesAvailable():
            on_ready()
