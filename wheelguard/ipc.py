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
MICE_MAX_AGE = 30.0     # seconds a scan of the input devices is reused (hotplug and session changes end it early)


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
        self._fds = None   # fds() until the next client comes or goes
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
        self.listener, self._fds = sock, None
        log.info("listening on %s", self.path)

    def close(self):
        for c in list(self.clients.values()):
            self._drop(c)
        if self.listener is not None:
            self.listener.close()
            self.listener, self._fds = None, None
            try:
                os.unlink(self.path)
            except OSError:
                pass

    # ---------- select() integration ----------
    def fds(self):
        """What to select() on. The same list object until a client comes or goes or the server stops, so a
        caller in a tight loop can tell with `is` that nothing changed. Don't modify it."""
        if self._fds is None:
            self._fds = list(self.clients)
            if self.listener is not None:
                self._fds.append(self.listener.fileno())
        return self._fds

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
        self._fds = None
        return client

    def _drop(self, client):
        if client.closed:
            return
        client.closed = True
        self.clients.pop(client.sock.fileno(), None)
        self._fds = None
        try:
            client.sock.close()
        except OSError:
            pass
        self.ctl.client_gone(client)

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
    def _send(self, client, msg, data=None):
        """Send one whole message (`data`: already encoded) or disconnect the client. Never blocks."""
        if data is None:
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
        data = None
        for client in list(self.clients.values()):
            if client.subscribed:
                if data is None:
                    data = encode(msg)          # once, however many clients listen
                self._send(client, msg, data)

    # ---------- commands ----------
    def _line(self, client, line):
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            self._send(client, {"type": "error", "message": "invalid JSON"})
            return
        self._deferred = []
        try:
            reply = self.dispatch(client, msg)
        except Exception:
            # A bad command must never take the daemon (and with it the mouse grab) down.
            log.exception("command failed: %.200r", line)
            reply = {"type": "error", "message": "internal error"}
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
            ctl.refresh_mice(MICE_MAX_AGE)
            return ctl.status()
        if cmd == "subscribe":
            client.subscribed = True
            ctl.refresh_mice(MICE_MAX_AGE)
            return ctl.status()
        if cmd == "history":
            return {"type": "history", "days": ctl.history_days()}
        if cmd == "mice":
            return {"type": "mice", "mice": ctl.refresh_mice(MICE_MAX_AGE)}
        if cmd == "set":
            err = ctl.set_config(msg.get("config"))
        elif cmd == "override":
            err = ctl.set_override(msg.get("config"), client)
        elif cmd == "pause":
            err = ctl.set_paused(msg.get("on"))
        elif cmd == "reload":
            err = ctl.reload()
        elif cmd == "clicklock_stop":
            ctl.stop_clicklock("asked to stop")
            err = None
        elif cmd == "reset_stats":
            ctl.reset_stats()
            err = None
        else:
            return {"type": "error", "message": f"unknown command: {cmd}"}
        return {"type": "error", "message": err} if err else {"type": "ok"}
