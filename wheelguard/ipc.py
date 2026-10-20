"""IPC server: newline-delimited JSON over a Unix stream socket (see PROTOCOL.md).

Single-threaded and select()-driven: the daemon loop passes `fds()` to select() and hands the
readable ones to `service()`. Client sockets are non-blocking and writes are never buffered: a
client that cannot take a whole message right now is disconnected, so a stalled GUI cannot slow
the input path.
"""
import json
import logging
import os
import socket
import stat

log = logging.getLogger("wheelguard")
SOCKET_NAME = "wheelguard.sock"
MAX_LINE = 64 * 1024


class AlreadyRunning(Exception):
    pass


def socket_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, SOCKET_NAME)


def claim_path(path):
    """Make `path` free for bind(): unlink a stale socket, refuse if a daemon answers on it."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise OSError(f"{path} exists and is not a socket")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
    except (ConnectionRefusedError, FileNotFoundError):
        log.info("removing stale socket %s", path)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    except OSError:
        raise AlreadyRunning(f"cannot probe {path}") from None
    else:
        raise AlreadyRunning(f"another wheelguard daemon is already listening on {path}")
    finally:
        probe.close()


def encode(msg):
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode()


class Client:
    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self.subscribed = False
        self.closed = False


class Server:
    def __init__(self, ctl, path=None):
        self.ctl = ctl
        self.path = path or socket_path()
        self.listener = None
        self.clients = {}  # fd -> Client
        self._deferred = None  # events published while a command runs go out after its reply
        ctl.publish = self.publish

    # ---------- lifecycle ----------
    def start(self):
        claim_path(self.path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            sock.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        sock.listen(8)
        sock.setblocking(False)
        self.listener = sock
        log.info("listening on %s", self.path)

    def close(self):
        for c in list(self.clients.values()):
            self._drop(c)
        if self.listener is not None:
            self.listener.close()
            self.listener = None
            try:
                os.unlink(self.path)
            except OSError:
                pass

    # ---------- select() integration ----------
    def fds(self):
        fds = list(self.clients)
        if self.listener is not None:
            fds.append(self.listener.fileno())
        return fds

    def service(self, readable):
        """Handle the fds from `fds()` that select() reported readable."""
        for fd in readable:
            if self.listener is not None and fd == self.listener.fileno():
                self._accept()
            elif fd in self.clients:
                self._read(self.clients[fd])

    def _accept(self):
        while True:
            try:
                sock, _ = self.listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError as ex:
                log.warning("accept failed: %s", ex)
                return
            self.add_client(sock)

    def add_client(self, sock):
        sock.setblocking(False)
        client = Client(sock)
        self.clients[sock.fileno()] = client
        return client

    def _drop(self, client):
        client.closed = True
        self.clients.pop(client.sock.fileno(), None)
        try:
            client.sock.close()
        except OSError:
            pass

    def _read(self, client):
        try:
            data = client.sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            self._drop(client)
            return
        client.buf += data
        while not client.closed:
            line, sep, rest = client.buf.partition(b"\n")
            if not sep:
                break
            client.buf = bytearray(rest)
            if line.strip():
                self._line(client, bytes(line))
        if len(client.buf) > MAX_LINE:
            self._drop(client)

    # ---------- sending ----------
    def _send(self, client, msg):
        """Send one whole message or disconnect the client. Never blocks."""
        data = encode(msg)
        try:
            sent = client.sock.send(data)
        except (BlockingIOError, InterruptedError):
            sent = 0
        except OSError:
            self._drop(client)
            return False
        if sent != len(data):
            log.info("dropping client: send buffer full")
            self._drop(client)
            return False
        return True

    def publish(self, msg):
        """Send an event to every subscribed client."""
        if self._deferred is not None:
            self._deferred.append(msg)
            return
        for client in list(self.clients.values()):
            if client.subscribed:
                self._send(client, msg)

    # ---------- commands ----------
    def _line(self, client, line):
        try:
            msg = json.loads(line)
        except ValueError:
            self._send(client, {"type": "error", "message": "invalid JSON"})
            return
        self._deferred = []
        try:
            reply = self.dispatch(client, msg)
        finally:
            events, self._deferred = self._deferred, None
        self._send(client, reply)
        for event in events:
            self.publish(event)

    def dispatch(self, client, msg):
        """Run one command; returns the direct reply. Status events go out via publish()."""
        if not isinstance(msg, dict) or not isinstance(msg.get("cmd"), str):
            return {"type": "error", "message": "expected an object with a \"cmd\" string"}
        cmd = msg["cmd"]
        ctl = self.ctl
        if cmd == "status":
            return ctl.status()
        if cmd == "subscribe":
            client.subscribed = True
            return ctl.status()
        if cmd == "set":
            err = ctl.set_config(msg.get("config"))
        elif cmd == "reload":
            err = ctl.reload()
        elif cmd == "reset_stats":
            ctl.reset_stats()
            err = None
        else:
            return {"type": "error", "message": f"unknown command: {cmd}"}
        return {"type": "error", "message": err} if err else {"type": "ok"}
