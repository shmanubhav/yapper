"""The speech engine's private Unix socket: where it lives, who may use it, and a tiny HTTP client.

The engine listens on a Unix domain socket, never a network port, inside a directory only this
account can enter. Every client and the server call check_path() first and refuse a socket whose
directory is not owned by this user or is open to group or others.

Standard library only, Python 3.9 compatible: the hooks run on whatever python3 the Mac has.
"""
import http.client
import os
import socket
import socketserver
import stat

SUN_PATH_MAX = 104          # macOS sockaddr_un.sun_path, including the closing NUL byte


class SocketError(Exception):
    pass


def default_socket():
    """YAPPER_SOCKET if set, else ~/.cache/yapper/speech.sock."""
    return os.environ.get("YAPPER_SOCKET") or os.path.join(os.path.expanduser("~"), ".cache", "yapper", "speech.sock")


def check_path(path, uid=None):
    """Raise SocketError unless `path` is a usable, private socket path."""
    uid = os.getuid() if uid is None else uid
    if not path or not os.path.isabs(path):
        raise SocketError(f"socket path must be absolute: {path!r}")
    if len(os.fsencode(path)) >= SUN_PATH_MAX:
        raise SocketError(f"socket path is {len(os.fsencode(path))} bytes; macOS allows at most "
                          f"{SUN_PATH_MAX - 1}: {path} (set YAPPER_SOCKET to a shorter path)")
    parent = os.path.dirname(path)
    try:
        st = os.stat(parent)
    except OSError as e:
        raise SocketError(f"socket folder {parent} is missing: {e.strerror}")
    if not stat.S_ISDIR(st.st_mode):
        raise SocketError(f"socket folder {parent} is not a folder")
    if st.st_uid != uid:
        raise SocketError(f"socket folder {parent} is owned by uid {st.st_uid}, not you (uid {uid}); refusing it")
    if st.st_mode & 0o077:
        raise SocketError(f"socket folder {parent} is open to other users (mode {stat.S_IMODE(st.st_mode):o}); "
                          f"run: chmod 700 {parent}")
    return path


class UnixHTTPConnection(http.client.HTTPConnection):
    """http.client over a Unix socket. Requests carry `Host: localhost`."""

    def __init__(self, sock_path, timeout=1):
        super().__init__("localhost", timeout=timeout)
        self.sock_path = sock_path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.sock_path)
        except OSError:
            s.close()
            raise
        self.sock = s


def request(method, url, body=None, headers=None, timeout=1, sock_path=None):
    """One request to the engine. Returns (status, body bytes); raises on any failure."""
    path = check_path(sock_path or default_socket())
    conn = UnixHTTPConnection(path, timeout=timeout)
    try:
        conn.request(method, url, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def answers(path, timeout=1):
    """True if something accepts connections on `path` (a live engine), False if nothing does."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        return True
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    except OSError:
        return True             # it exists and did not refuse: treat as alive, never delete it
    finally:
        s.close()


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """A threaded HTTP server on a Unix socket. AF_UNIX peers have no address, so the handler is
    given a fixed ("local", 0) for logging."""
    daemon_threads = True
    request_queue_size = 64     # a deep backlog: a refused connect must mean "nobody is listening"

    def get_request(self):
        req, _ = super().get_request()
        return req, ("local", 0)


# --- pidfiles: each process records itself, so a launcher that lost a start race never does ---

def pid_runs(pid, marker):
    """True if `pid` is alive and its command line contains `marker` (a script path)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass                                    # alive, but not ours to signal
    import subprocess
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return True                             # cannot tell: assume it is still the owner
    return marker in cmd


def write_pidfile(path):
    """Record this process in `path` (mode 600, replaced atomically)."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()}\n")
    os.replace(tmp, path)


def claim_pidfile(path, marker):
    """Create `path` with this pid unless a live process running `marker` already holds it.
    Returns False when another one does (the caller should exit)."""
    for _ in range(2):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                held = int(open(path).read().strip())
            except (OSError, ValueError):
                held = None
            if held and held != os.getpid() and pid_runs(held, marker):
                return False
            try:
                os.unlink(path)                 # stale: its process is gone
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as f:
            f.write(f"{os.getpid()}\n")
        return True
    return False


def release_pidfile(path):
    """Remove `path` if it still names this process."""
    try:
        if int(open(path).read().strip()) == os.getpid():
            os.unlink(path)
    except (OSError, ValueError):
        pass
