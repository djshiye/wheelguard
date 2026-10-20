"""Single-instance support: the first settings app listens on a local socket, later launches hand over
a one-word message ("show" or "tray") and exit."""
import os
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MESSAGES = ("show", "tray")


def socket_path() -> str:
    override = os.environ.get("WHEELGUARD_INSTANCE_SOCKET")
    if override:
        return override
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, "wheelguard-settings.sock")


def parse_message(data: bytes) -> Optional[str]:
    """The first known word in the data, or None. Anything else a stray client sends is ignored."""
    for word in data.decode(errors="replace").split():
        return word if word in MESSAGES else None
    return None


def send(message: str, path: Optional[str] = None, timeout_ms: int = 1000) -> bool:
    """Hand the message to a running instance. False when none answers (nothing is listening or it's stale)."""
    sock = QLocalSocket()
    sock.connectToServer(path or socket_path())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(message.encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    messageReceived = Signal(str)

    def __init__(self, path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._path = path or socket_path()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        """Start listening. send() already failed, so a socket file left behind by a crash is stale."""
        if self._server.listen(self._path):
            return True
        QLocalServer.removeServer(self._path)
        return self._server.listen(self._path)

    def close(self):
        self._server.close()

    def _on_connection(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                self._read(sock)

    def _read(self, sock):
        msg = parse_message(bytes(sock.readAll()))
        if msg:
            self.messageReceived.emit(msg)
