#!/usr/bin/env python3
"""player.py SOCKET: plays another Mac's yapper speech on this one (yapper --remote).

yapper --remote starts this on the laptop and reverse-forwards SOCKET over SSH, so the engine on
the other Mac can reach it. One connection per clip: WAV bytes, then a half-close; the answer is
"done" once the clip played or was cut. A connection carrying exactly STOP cuts the clip playing
now and every clip accepted before it.

Standard library only, Python 3.9 compatible: the laptop needs no speech environment.
"""
import os, signal, socket, subprocess, sys, tempfile, threading

sys.dont_write_bytecode = True     # never write __pycache__ into the clone
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import speechsock                  # same folder

AFPLAY = os.environ.get("YAPPER_AFPLAY", "afplay")
SPEED = os.environ.get("YAPPER_TTS_SPEED", "1.07")
MAX_CLIP = 32 * 1024 * 1024        # bytes; a sentence is well under 2 MB


def log(msg):
    print(f"player: {msg}", file=sys.stderr, flush=True)


def complete_wav(data):
    """A whole RIFF file: the header's size field matches the bytes that arrived."""
    return len(data) > 8 and data[:4] == b"RIFF" and int.from_bytes(data[4:8], "little") + 8 == len(data)


class Player:
    def __init__(self, afplay=AFPLAY, speed=SPEED):
        self.afplay, self.speed = afplay, speed
        self.lock = threading.Lock()
        self.proc = None
        self.stop_below = 0

    def _kill(self):
        """Called with the lock held."""
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            p.wait()

    def handle(self, conn, n):
        """Connection number n (counted at accept): a clip to play, or STOP."""
        path = None
        try:
            data = b""
            while len(data) <= MAX_CLIP:
                b = conn.recv(65536)
                if not b:
                    break
                data += b
            if data == b"STOP":
                with self.lock:
                    self.stop_below = max(self.stop_below, n)
                    self._kill()
                return
            proc = None
            if not complete_wav(data):
                log(f"incomplete clip dropped ({len(data)} bytes)")
            else:
                with self.lock:
                    if n > self.stop_below:
                        self._kill()            # one clip at a time
                        fd, path = tempfile.mkstemp(suffix=".wav", prefix="yapper-remote-")
                        with os.fdopen(fd, "wb") as f:
                            f.write(data)
                        proc = subprocess.Popen([self.afplay, "-r", str(self.speed), "-q", "1", path],
                                                start_new_session=True)
                        self.proc = proc
            if proc:
                proc.wait()
            conn.sendall(b"done\n")
        except OSError as e:
            log(f"connection {n}: {e}")
        finally:
            conn.close()
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def close(self):
        with self.lock:
            self._kill()


def serve(path, player, ready=None):
    """Listen on path (its folder must be private) until the socket is closed or a signal arrives."""
    speechsock.check_path(path)
    if os.path.lexists(path):
        os.unlink(path)
    old = os.umask(0o177)          # the socket is created 600
    try:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
    finally:
        os.umask(old)
    srv.listen(16)
    if ready:
        ready(srv)
    n = 0
    try:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return              # closed
            n += 1
            threading.Thread(target=player.handle, args=(conn, n), daemon=True).start()
    finally:
        srv.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        player.close()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: player.py SOCKET")

    def bye(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGHUP, bye)
    try:
        serve(sys.argv[1], Player(), ready=lambda s: log(f"listening on {sys.argv[1]}"))
    except speechsock.SocketError as e:
        log(str(e))
        sys.exit(2)


if __name__ == "__main__":
    main()
