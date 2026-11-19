"""
tts-server: resident local TTS. Chatterbox Turbo in one fixed voice, loaded once at startup.
The reference clip voices/derry_b.wav is a synthetic voice designed with Qwen3-TTS VoiceDesign, not a real person.

  GET  /health  -> 503 warming, 200 ready, 500 load failed
  POST /speak   {"text": "..."} -> 202; cuts off anything still playing
  POST /stop    -> stop playback and drop anything queued
  POST /player  {"sock": "...", "on": true|false} -> 200; route clips to a remote player (yapper --remote)
Served over a Unix socket in a private folder (lib/speechsock.py), never a network port.
Clips play on this Mac through afplay, unless a remote player is registered (opt-in, yapper --remote):
then they go to that player's socket, which SSH forwards to the other Mac.
"""
import hashlib, json, logging, os, queue, signal, socket, stat, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler

import speechsock
from sentences import split_sentences

log = logging.getLogger("speaker")


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


class Speaker:
    """Sentence queue: plays sentence N while sentence N+1 is generated. Every speak/stop bumps
    the epoch; work from an older epoch is dropped wherever it is found."""

    def __init__(self, synth, player):
        self._synth, self._player = synth, player
        self._lock = threading.Lock()
        self._epoch = 0
        self._jobs = queue.Queue()
        self._ready = queue.Queue()
        threading.Thread(target=self._generate_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def current(self):
        with self._lock:
            return self._epoch

    def speak(self, text):
        epoch = self._cut()
        sentences = split_sentences(text)
        if sentences:
            self._jobs.put((epoch, sentences))
        return epoch

    def stop(self):
        return self._cut()

    def _cut(self):
        with self._lock:            # same lock as the start in _play_loop: no stale clip can slip in
            self._epoch += 1
            self._player.kill()
            epoch = self._epoch
        while True:
            try:
                _, path = self._ready.get_nowait()
            except queue.Empty:
                return epoch
            _unlink(path)

    def _generate_loop(self):
        while True:
            epoch, sentences = self._jobs.get()
            for sentence in sentences:
                if epoch != self.current():
                    break
                try:
                    path = self._synth(sentence)
                except Exception as e:      # noqa: BLE001 - one bad sentence must not kill the server
                    log.error("synth failed, dropping the rest of this reply: %s", e)
                    break
                if epoch != self.current():
                    _unlink(path)
                    break
                self._ready.put((epoch, path))

    def _play_loop(self):
        while True:
            epoch, path = self._ready.get()
            try:
                with self._lock:
                    handle = self._player.start(path) if epoch == self._epoch else None
                if handle:
                    handle.wait()
            except Exception as e:          # noqa: BLE001
                log.error("playback failed: %s", e)
            finally:
                _unlink(path)


ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))   # realpath, as bin/yapper
MODEL = os.environ.get("YAPPER_TTS_MODEL", "mlx-community/chatterbox-turbo-fp16")
# Pinned: this TTS model revision, the speech tokenizer below, and the hashed packages in
# requirements.lock. mlx-audio fetches the tokenizer at its latest revision on its own; load_model
# then replaces every one of those weights with the pinned file, checked by sha256.
REVISION = os.environ.get("YAPPER_TTS_REVISION", "b2d0a13aa7cfff0a06d9acb247ae91c8f19a6d75")
S3TOK_REPO, S3TOK_FILE = "mlx-community/S3TokenizerV2", "model.safetensors"
S3TOK_REVISION = "e0c9886f0e1c35ae85b1f27277416fb19fc72bec"
S3TOK_SHA256 = "928726bc1f206a613d36b8f49e297eae9c5593a21bf9b92ddfe2c23f85eb92cc"
VOICE_WAV = os.environ.get("YAPPER_TTS_VOICE_WAV", os.path.join(ROOT, "voices", "derry_b.wav"))
MAX_BODY = 64 * 1024  # largest accepted request body, bytes
MAX_CHARS = 600       # longest text spoken per request, as in bin/speak-hook
REQUEST_TIMEOUT_S = 5
SPEED = float(os.environ.get("YAPPER_TTS_SPEED", "1.07"))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pinned_tokenizer_path(download=None):
    """Fetch (or find in the cache) the speech tokenizer at its pinned revision; refuse a wrong hash.

    A commit pin is served from the cache without downloading again, so a bad cached file is
    fetched once more with force_download (online, as in --install) before refusing."""
    if download is None:
        from huggingface_hub import hf_hub_download as download
    kw = dict(repo_id=S3TOK_REPO, filename=S3TOK_FILE, revision=S3TOK_REVISION)
    path = download(**kw)
    got = _sha256(path)
    if got != S3TOK_SHA256:
        log.warning("speech tokenizer %s has sha256 %s; fetching it again", path, got[:16])
        try:
            fresh = download(force_download=True, **kw)
            path, got = fresh, _sha256(fresh)
        except Exception as e:          # noqa: BLE001  offline (the engine) or no network
            log.warning("could not fetch the speech tokenizer again: %s", e)
    if got != S3TOK_SHA256:
        raise RuntimeError(f"speech tokenizer sha256 {got[:16]} != pinned {S3TOK_SHA256[:16]}; "
                           f"rerun yapper --install online ({path})")
    return path


class ChatterboxEngine:
    """Pinned model, loaded once; the voice is fixed by preparing conditionals from one clip."""
    SR = 24000

    def __init__(self, model=MODEL, revision=REVISION, voice_wav=VOICE_WAV):
        self.model_id, self.revision, self.voice_wav = model, revision, voice_wav
        self.model = None

    def load_model(self):
        from mlx_audio.tts.utils import load_model
        self.model = load_model(self.model_id, revision=self.revision)
        import mlx.core as mx
        tok = self.model._s3tokenizer
        weights = mx.load(pinned_tokenizer_path())
        if hasattr(tok, "sanitize"):
            weights = tok.sanitize(weights)
        tok.load_weights(list(weights.items()), strict=True)   # every tokenizer weight from the pinned file
        mx.eval(tok.parameters())
        log.info("speech tokenizer pinned at %s@%s (sha256 checked)", S3TOK_REPO, S3TOK_REVISION[:8])

    def set_voice(self, voice_wav):
        """Fix the voice from one reference clip; lib/sample.py calls this once per clip."""
        self.voice_wav = voice_wav
        self.model.prepare_conditionals(voice_wav)

    def load(self):
        self.load_model()
        self.set_voice(self.voice_wav)
        self.render("Ready.")                       # absorbs the first-generation cost

    def render(self, text):
        import numpy as np
        return np.concatenate([np.asarray(r.audio).reshape(-1) for r in self.model.generate(text=text)])

    def synth(self, text):
        import soundfile as sf
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="yapper-")
        os.close(fd)
        sf.write(path, self.render(text), self.SR)
        return path


class AfPlayer:
    """afplay -q 1 keeps pitch when the rate changes. Called with Speaker's lock held."""

    def __init__(self, speed):
        self.speed, self._proc = speed, None

    def start(self, path):
        self._proc = subprocess.Popen(["afplay", "-r", str(self.speed), "-q", "1", path],
                                      start_new_session=True)
        return self._proc

    def kill(self):
        p, self._proc = self._proc, None
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


REMOTE_CONNECT_S = 1      # reaching a registered player; a dead one falls back to this Mac at once
REMOTE_SLACK_S = 5        # on top of a clip's own length (at half speed), before it counts as stalled
REMOTE_PROGRESS_S = 5     # longest a send to the player may make no progress
REMOTE_MIN_BPS = 20000    # slowest link supported, bytes per second (160 kbit/s): the transfer's budget
REMOTE_CLIP_MAX_S = 120   # never wait longer than this for one clip


def clip_seconds(data):
    """Length of a WAV clip from its byte rate (header offset 28); a guess for an odd header."""
    rate = int.from_bytes(data[28:32], "little") if len(data) >= 44 else 0
    return max(len(data) - 44, 0) / (rate if rate > 0 else 48000)


class RemoteClip:
    """One clip sent to a remote player: WAV bytes, a half-close, then "got" once the whole clip
    arrived (transfer budget: the slowest supported link) and "done" once it played (play budget:
    the clip's own length at half speed, plus slack). Anything else (a hang-up, "dropped", a stall
    past either budget) stops the clip there and plays it on this Mac."""

    def __init__(self, sock, path, on_fail, fallback):
        self._sock, self._path = sock, path
        self._on_fail, self._fallback = on_fail, fallback
        self.closed = False
        self._fd_lock = threading.Lock()     # shutdown (cut) and close (wait) never overlap

    def wait(self):
        failed = False
        try:
            with open(self._path, "rb") as f:
                data = f.read()
            start = time.monotonic()
            cap = start + REMOTE_CLIP_MAX_S
            # The player answers "got" once the whole clip has arrived, then "done" once it played.
            # The transfer has its own budget (the slowest supported link), so a slow link never
            # counts against the clip's playing time.
            got_by = min(start + len(data) / REMOTE_MIN_BPS + REMOTE_SLACK_S, cap)
            view, sent = memoryview(data), 0
            while sent < len(data):
                t = min(REMOTE_PROGRESS_S, got_by - time.monotonic())
                if t <= 0:
                    raise socket.timeout("the player stalled taking the clip")
                self._sock.settimeout(t)            # a send that makes no progress gives up
                sent += self._sock.send(view[sent:sent + 65536])
            self._sock.shutdown(socket.SHUT_WR)
            buf = b""

            def line(by):
                nonlocal buf
                while b"\n" not in buf:
                    t = by - time.monotonic()
                    if t <= 0:
                        raise socket.timeout("the player stalled")
                    self._sock.settimeout(t)
                    b = self._sock.recv(64)
                    if not b:
                        raise OSError("the player hung up")
                    buf += b
                first, buf = buf.split(b"\n", 1)
                return first
            got = line(got_by)
            if got != b"got":
                raise OSError(f"the player did not take the clip ({got[:20]!r})")
            if line(min(time.monotonic() + clip_seconds(data) * 2 + REMOTE_SLACK_S, cap)) != b"done":
                raise OSError("the player did not finish the clip")
        except OSError as e:
            if not self.closed:         # a cut shuts the socket down on purpose
                log.warning("remote player failed: %s; playing this clip here", e)
                failed = True
        finally:
            with self._fd_lock:
                self.closed = True
                try:
                    self._sock.close()  # only this thread closes the socket: no fd reuse under a recv
                except OSError:
                    pass
        if failed:
            self._on_fail()
            h = self._fallback(self)
            if h:
                h.wait()

    def close(self):
        """Cut: wakes wait() wherever it blocks. Shutdown only; wait() itself closes the socket, so
        its fd is never freed (and reused) while that thread may still be about to read it."""
        with self._fd_lock:
            if self.closed:
                return
            self.closed = True
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class RoutedPlayer:
    """Plays through a registered remote player (yapper --remote) while one is reachable, else
    through `local`. Opt-in: with nothing registered, every clip plays on this Mac.
    start() and kill() are called with Speaker's lock held."""

    def __init__(self, local):
        self.local = local
        self.target = None
        self._clip = None
        self._lock = threading.Lock()

    def register(self, path):
        """Route clips to the player socket at path; it must sit in a private folder of ours."""
        speechsock.check_path(path)
        if not (os.path.lexists(path) and stat.S_ISSOCK(os.lstat(path).st_mode)):
            raise speechsock.SocketError(f"{path} is not a socket")
        with self._lock:
            changed, self.target = self.target != path, path
        if changed:
            log.info("speech now plays through the remote player %s", path)

    def unregister(self, path=None):
        """Back to this Mac; with a path, only if that player is still the one registered."""
        with self._lock:
            if self.target is None or (path is not None and path != self.target):
                return
            old, self.target = self.target, None
        log.info("remote player %s gone; speech plays on this Mac again", old)

    def _connect(self, path, timeout):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(speechsock.check_path(path))
        except (OSError, speechsock.SocketError):
            s.close()
            raise
        return s

    def _gone(self, path):
        """The SSH connection that carried this player has ended: drop it, and its leftover socket."""
        self.unregister(path)
        if os.path.basename(path).startswith("r-"):
            try:
                if stat.S_ISSOCK(os.lstat(path).st_mode) and not speechsock.answers(path):
                    os.unlink(path)
            except OSError:
                pass

    def _send_stop(self, target):
        try:                            # numbered after any clip, so STOP cuts it on the player
            s = self._connect(target, REMOTE_CONNECT_S)
            try:
                s.sendall(b"STOP")
                s.shutdown(socket.SHUT_WR)
            finally:
                s.close()
        except (OSError, speechsock.SocketError):
            pass

    def _failed(self, target):
        """A slow player may still be playing the clip: stop it there before it plays here."""
        self._send_stop(target)
        self.unregister(target)

    def _fallback(self, clip):
        """Play a clip the remote player failed, here; unless it was cut meanwhile."""
        with self._lock:
            if self._clip is not clip:  # kill() cleared it: this clip was cut
                return None
            self._clip = None
            return self.local.start(clip._path)

    def start(self, path):
        target = self.target
        if target:
            try:
                s = self._connect(target, REMOTE_CONNECT_S)
            except (OSError, speechsock.SocketError) as e:
                log.warning("remote player %s unreachable (%s)", target, e)
                self._gone(target)
            else:
                clip = RemoteClip(s, path, lambda: self._failed(target), self._fallback)
                with self._lock:
                    self._clip = clip
                return clip
        return self.local.start(path)

    def kill(self):
        with self._lock:                # a cut clip never falls back after this
            clip, self._clip = self._clip, None
        if clip:
            clip.close()
        self.local.kill()
        target = self.target
        if target:
            self._send_stop(target)

_state = {"status": "warming", "detail": ""}
speaker = None
router = None             # the RoutedPlayer, once loaded


class H(BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT_S     # per-connection socket timeout: a stalled client cannot hold a thread

    def log_message(self, *a):
        pass

    def _send(self, code, body=b""):
        try:
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                    # the client hung up before reading the answer: nothing to do

    def _local(self):
        # Browsers always send Origin; a rebinding page sends a foreign Host. Allow neither.
        if self.headers.get("Origin") is not None:
            return False
        return self.headers.get("Host") == "localhost"

    def _player(self, raw):
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self._send(415, b"json only"); return
        if router is None:
            self._send(503, b"warming"); return
        try:
            body = json.loads(raw)
            sock, on = body.get("sock"), body.get("on", True)
            if not isinstance(sock, str) or not isinstance(on, bool):
                raise ValueError
        except (ValueError, AttributeError):
            self._send(400, b"bad json"); return
        if not on:
            router.unregister(sock)
            self._send(200, b"local"); return
        try:
            router.register(sock)
        except speechsock.SocketError as e:
            self._send(400, str(e).encode()[:200]); return
        self._send(200, b"remote")

    def do_GET(self):
        if not self._local():
            self._send(403, b"forbidden"); return
        if self.path != "/health":
            self._send(404); return
        # The root lets the launcher tell this checkout's engine from another install on the same socket.
        st = _state["status"]
        body = {"ok": st == "ready", "status": st, "root": ROOT}
        if st == "failed":
            body["detail"] = _state["detail"]
        self._send({"ready": 200, "warming": 503}.get(st, 500), json.dumps(body, ensure_ascii=False).encode())

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            self._send(400, b"bad length"); return
        if n > MAX_BODY:
            self._send(413, b"too large"); return
        # Read the (bounded) body before any refusal, so the client gets the answer instead of a
        # broken pipe from writing into a closed Unix socket.
        raw = self.rfile.read(n) if n else b"{}"
        if not self._local():
            self._send(403, b"forbidden"); return
        if self.path == "/stop":
            if speaker:
                speaker.stop()
            self._send(200, b"stopped"); return
        if self.path == "/player":
            self._player(raw); return
        if self.path != "/speak":
            self._send(404); return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self._send(415, b"json only"); return
        if _state["status"] != "ready" or speaker is None:
            self._send(503, b"warming"); return
        try:
            text = (json.loads(raw).get("text") or "").strip()[:MAX_CHARS]   # same cap as speak-hook
        except (ValueError, AttributeError):
            self._send(400, b"bad json"); return
        self._send(202, b"speaking")
        if text:
            log.info("speak %r", text[:70])
            speaker.speak(text)


def _load(engine):
    global speaker, router
    try:
        engine.load()
        router = RoutedPlayer(AfPlayer(SPEED))
        speaker = Speaker(engine.synth, router)
        _state.update(status="ready", detail="")
        log.info("speaker ready, model=%s@%s voice=%s speed=%.2f", MODEL, REVISION[:8], VOICE_WAV, SPEED)
    except Exception as e:                      # noqa: BLE001 - reported through /health as 500
        _state.update(status="failed", detail=f"{e.__class__.__name__}: {str(e)[:160]}")
        log.error("load failed: %s", _state["detail"])


def main():
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    engine = ChatterboxEngine()
    if "--install" in sys.argv[1:]:
        engine.load()
        print("installed: model, tokenizer and voice are cached for offline use")
        return
    path = speechsock.default_socket()
    try:
        srv = bind(path)
    except speechsock.SocketError as e:
        log.error("%s", e)
        sys.exit(2)
    # Only the process that owns the socket records itself, so a lost start race leaves no stale pid.
    speechsock.write_pidfile(path + ".speaker.pid")
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))     # clean shutdown removes the socket
    threading.Thread(target=_load, args=(engine,), daemon=True).start()
    log.info("speaker http on %s (health 503 until warm)", path)
    try:
        srv.serve_forever()
    finally:
        release(srv, path)


def bind(path, uid=None):
    """Bind the engine's socket: private folder only; never steal a live socket; clear a stale one."""
    speechsock.check_path(path, uid)
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            raise speechsock.SocketError(f"{path} exists and is not a socket; refusing to replace it")
        if speechsock.answers(path):
            raise speechsock.SocketError(f"another engine already answers on {path}; run yapper --stop first")
        os.unlink(path)                                       # stale: nothing listens on it
    old = os.umask(0o077)
    try:
        srv = speechsock.UnixHTTPServer(path, H)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    srv.bound_ino = os.lstat(path).st_ino
    return srv


def release(srv, path):
    """Stop playback (afplay, queued temp wavs), close the server, and remove its socket and pidfile
    unless another engine has replaced them."""
    if speaker is not None:
        try:
            speaker.stop()
        except Exception:                       # noqa: BLE001 - shutting down regardless
            pass
    srv.server_close()
    speechsock.release_pidfile(path + ".speaker.pid")
    try:
        if os.lstat(path).st_ino == getattr(srv, "bound_ino", None):
            os.unlink(path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
