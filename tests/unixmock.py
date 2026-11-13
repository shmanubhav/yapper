"""Test helper (not a test file): a stand-in speech engine on a Unix socket in a private temp folder.

The folder is made with mkdtemp (mode 700) under /tmp so the socket path stays well inside the
104-byte macOS limit. Nothing here loads a model or plays sound.
"""
import os, shutil, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lib"))
import speechsock  # noqa: E402


def private_dir():
    return tempfile.mkdtemp(prefix="yp", dir="/tmp")


class Recorder(BaseHTTPRequestHandler):
    """Records (method, path, body) on server.hits and answers server.code (default 200)."""

    def log_message(self, *a):
        pass

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        self.server.hits.append((self.command, self.path, body))
        delay = getattr(self.server, "delay", 0)
        if delay:
            import time
            time.sleep(delay)
        self.send_response(getattr(self.server, "code", 200))
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = _answer


class Mock:
    """with Mock() as m: m.path is the socket, m.hits what arrived."""

    def __init__(self, handler=Recorder, code=200, delay=0):
        self.dir = private_dir()
        self.path = os.path.join(self.dir, "speech.sock")
        self.srv = speechsock.UnixHTTPServer(self.path, handler)
        self.srv.hits, self.srv.code, self.srv.delay = [], code, delay
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def hits(self):
        return self.srv.hits

    def paths(self):
        return [h[1] for h in self.srv.hits]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
