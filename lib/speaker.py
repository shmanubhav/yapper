"""
tts-server: resident local TTS. Chatterbox Turbo in one fixed voice, loaded once at startup.
The reference clip voices/derry_b.wav is a synthetic voice designed with Qwen3-TTS VoiceDesign, not a real person.

  GET  /health  -> 503 warming, 200 ready, 500 load failed
  POST /speak   {"text": "..."} -> 202; cuts off anything still playing
  POST /stop    -> stop playback and drop anything queued
Served over a Unix socket in a private folder (lib/speechsock.py), never a network port.
Clips play on this Mac through afplay.
"""
import json, logging, os, queue, signal, stat, subprocess, sys, tempfile, threading
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
# Pinned: this TTS model revision, plus the hashed packages in requirements.lock. Not pinned:
# mlx-audio itself fetches the speech tokenizer (mlx-community/S3TokenizerV2, model.safetensors)
# at its latest revision during --install.
REVISION = os.environ.get("YAPPER_TTS_REVISION", "b2d0a13aa7cfff0a06d9acb247ae91c8f19a6d75")
VOICE_WAV = os.environ.get("YAPPER_TTS_VOICE_WAV", os.path.join(ROOT, "voices", "derry_b.wav"))
MAX_BODY = 64 * 1024  # largest accepted request body, bytes
MAX_CHARS = 600       # longest text spoken per request, as in bin/speak-hook
REQUEST_TIMEOUT_S = 5
SPEED = float(os.environ.get("YAPPER_TTS_SPEED", "1.07"))


class ChatterboxEngine:
    """Pinned model, loaded once; the voice is fixed by preparing conditionals from one clip."""
    SR = 24000

    def __init__(self, model=MODEL, revision=REVISION, voice_wav=VOICE_WAV):
        self.model_id, self.revision, self.voice_wav = model, revision, voice_wav
        self.model = None

    def load_model(self):
        from mlx_audio.tts.utils import load_model
        self.model = load_model(self.model_id, revision=self.revision)

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


_state = {"status": "warming", "detail": ""}
speaker = None


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
    global speaker
    try:
        engine.load()
        speaker = Speaker(engine.synth, AfPlayer(SPEED))
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
