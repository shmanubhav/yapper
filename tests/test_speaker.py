"""lib/speaker.py Speaker: the sentence queue, with a fake engine and a fake player. No model needed."""
import os, sys, tempfile, threading, time
sys.dont_write_bytecode = True     # keep the clone free of __pycache__

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from speaker import Speaker

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


def wait_until(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Handle:
    def __init__(self):
        self.done = threading.Event()

    def wait(self):
        self.done.wait(10)


class FakePlayer:
    """start() records the sentence it was handed; a clip 'plays' until finish() or kill()."""

    def __init__(self):
        self.started, self.kills, self.current = [], 0, None

    def start(self, path):
        with open(path) as f:
            self.started.append(f.read())
        self.current = Handle()
        return self.current

    def kill(self):
        self.kills += 1
        if self.current:
            self.current.done.set()

    def finish(self):
        if self.current:
            self.current.done.set()


class FakeEngine:
    def __init__(self, fail_on=None):
        self.calls, self.files, self.fail_on = [], [], fail_on

    def synth(self, sentence):
        self.calls.append(sentence)
        if self.fail_on and self.fail_on in sentence:
            raise RuntimeError("boom")
        fd, p = tempfile.mkstemp(suffix=".wav")
        os.write(fd, sentence.encode())
        os.close(fd)
        self.files.append(p)
        return p


A = "The first sentence is here. The second sentence is here."

# --- plays sentences in order, one at a time, and deletes every wav
e, p = FakeEngine(), FakePlayer()
s = Speaker(e.synth, p)
s.speak(A)
check(wait_until(lambda: p.started == ["The first sentence is here."]), f"sentence 1 starts first {p.started}")
time.sleep(0.1)
check(len(p.started) == 1, "sentence 2 waits for sentence 1 to finish")
p.finish()
check(wait_until(lambda: p.started == ["The first sentence is here.", "The second sentence is here."]), f"then sentence 2 {p.started}")
p.finish()
check(wait_until(lambda: e.files and all(not os.path.exists(f) for f in e.files)), "every wav deleted after play")

# --- stop mid-reply: kills playback, drops the queued sentence, deletes its wav
e, p = FakeEngine(), FakePlayer()
s = Speaker(e.synth, p)
s.speak(A)
wait_until(lambda: len(p.started) == 1 and len(e.calls) == 2)
s.stop()
time.sleep(0.2)
check(len(p.started) == 1, f"stop: sentence 2 never plays {p.started}")
check(p.kills >= 1, "stop: current playback killed")
check(wait_until(lambda: all(not os.path.exists(f) for f in e.files)), "stop: queued wav deleted")

# --- a new reply cuts the old one off
e, p = FakeEngine(), FakePlayer()
s = Speaker(e.synth, p)
s.speak(A)
wait_until(lambda: len(p.started) == 1)
s.speak("A brand new reply arrives right now.")
check(wait_until(lambda: p.started[-1:] == ["A brand new reply arrives right now."]), f"new reply plays {p.started}")
check("The second sentence is here." not in p.started, "old reply's remaining sentence dropped")
p.finish()

# --- burst: several Stop firings in one turn, only the newest reply is heard
e, p = FakeEngine(), FakePlayer()
s = Speaker(e.synth, p)
for i in range(3):
    s.speak(f"Reply number {i} is the one speaking now.")
check(wait_until(lambda: p.started[-1:] == ["Reply number 2 is the one speaking now."]), f"burst: newest wins {p.started}")
n = len(p.started)
time.sleep(0.2)
check(len(p.started) == n, f"burst: nothing starts after the newest reply {p.started}")
p.finish()

# --- a synth failure drops the rest of that reply but the queue keeps working
e, p = FakeEngine(fail_on="explodes"), FakePlayer()
s = Speaker(e.synth, p)
s.speak("This sentence explodes badly. This one would follow it.")
time.sleep(0.2)
check(p.started == [], f"failure: nothing played {p.started}")
check(e.calls == ["This sentence explodes badly."], f"failure: rest of reply dropped {e.calls}")
s.speak("Still alive after the failure.")
check(wait_until(lambda: p.started == ["Still alive after the failure."]), f"failure: queue still works {p.started}")
p.finish()

# --- stop when idle is harmless; epochs only go up; blank text synthesizes nothing
e, p = FakeEngine(), FakePlayer()
s = Speaker(e.synth, p)
a = s.stop()
b = s.stop()
check(b == a + 1 and s.current() == b, f"epoch increments per stop ({a}, {b})")
s.speak("   ")
time.sleep(0.1)
check(e.calls == [], "blank text -> no synth")

# --- HTTP: health reflects load state; /speak refuses until ready
import json as _json
import speaker as sp
import speechsock

SOCK_DIR = tempfile.mkdtemp(prefix="yp", dir="/tmp")    # 700, and short enough for sun_path
SOCK = os.path.join(SOCK_DIR, "speech.sock")
srv = sp.bind(SOCK)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(method, path, body=None, headers=None):
    st, data = speechsock.request(method, path, body=body, timeout=2, sock_path=SOCK,
                                  headers={"Content-Type": "application/json", **(headers or {})})
    return st, data


def code(method, path, body=None, headers=None):
    return get(method, path, body, headers)[0]


sp._state.update(status="warming", detail="")
check(code("GET", "/health") == 503, "health 503 while warming")
check(code("POST", "/speak", _json.dumps({"text": "hi there"}).encode()) == 503, "speak 503 while warming")
sp._state.update(status="failed", detail="FileNotFoundError: no model")
check(code("GET", "/health") == 500, "health 500 after a load failure")
e, p = FakeEngine(), FakePlayer()
sp.speaker = Speaker(e.synth, p)
sp._state.update(status="ready", detail="")
check(code("GET", "/health") == 200, "health 200 when ready")
check(code("POST", "/speak", _json.dumps({"text": "Spoken through HTTP right now."}).encode()) == 202, "speak 202 when ready")
check(wait_until(lambda: p.started == ["Spoken through HTTP right now."]), f"HTTP speak reaches the queue {p.started}")
h = _json.loads(get("GET", "/health")[1])
check(h.get("ok") is True and h.get("root") == sp.ROOT and os.path.isfile(os.path.join(h.get("root", ""), "lib", "speaker.py")),
      f"health names this checkout's root {h}")
p.finish()
time.sleep(0.1)
e.calls.clear()
check(code("POST", "/speak", _json.dumps({"text": "x" * 1000}).encode()) == 202, "a long text is accepted")
check(wait_until(lambda: e.calls) and sum(len(c) for c in e.calls) <= 600, f"the server speaks at most 600 chars ({sum(len(c) for c in e.calls)})")
p.finish()
check(sp.H.timeout == 5, f"per-request socket timeout 5 s ({sp.H.timeout})")
import socket as _socket
t0 = time.time()
idle = _socket.socket(_socket.AF_UNIX)
idle.connect(SOCK)
idle.settimeout(10)
try:
    idle.recv(1)
except OSError:
    pass
idle.close()
check(time.time() - t0 < 8, f"a silent client is dropped after the timeout ({time.time() - t0:.1f}s)")
check(code("POST", "/stop") == 200, "stop 200")
check(code("POST", "/speak", b"not json") == 400, "bad json 400")
check(code("GET", "/nope") == 404, "unknown path 404")

# --- HTTP hardening: no browser origins, no rebinding hosts, /speak is JSON only
ok_body = _json.dumps({"text": "Hardened path speaks."}).encode()
evil = {"Origin": "https://evil.example"}
check(code("POST", "/speak", ok_body, evil) == 403, "cross-origin speak 403")
check(code("POST", "/stop", b"", evil) == 403, "cross-origin stop 403")
check(code("GET", "/health", None, evil) == 403, "cross-origin health 403")
check(code("POST", "/speak", ok_body, {"Host": "evil.example"}) == 403, "rebinding host 403")
check(code("POST", "/speak", ok_body, {"Content-Type": "text/plain"}) == 415, "text/plain speak 415")
check(code("POST", "/speak", ok_body, {"Content-Type": "application/json; charset=utf-8"}) == 202, "json with charset 202")
check(code("POST", "/stop", b"", {"Content-Type": "application/x-www-form-urlencoded"}) == 200, "stop needs no content type")

# --- Content-Length guard: rejected before the body is read
import http.client


def raw_post(length):
    c = speechsock.UnixHTTPConnection(SOCK, timeout=2)
    try:
        c.putrequest("POST", "/speak")
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", length)
        c.endheaders()
        return c.getresponse().status
    except (OSError, http.client.HTTPException) as e:
        return repr(e)
    finally:
        c.close()


check(raw_post("abc") == 400, "non-integer Content-Length 400")
check(raw_post("-1") == 400, "negative Content-Length 400")
check(raw_post(str(64 * 1024 + 1)) == 413, "oversized Content-Length 413")
check(code("POST", "/speak", ok_body) == 202, "normal speak still 202 after guard")
check(oct(os.stat(SOCK).st_mode & 0o777) == "0o600", "the engine's socket is mode 600")
srv.shutdown()
sp.release(srv, SOCK)
check(not os.path.exists(SOCK), "a clean shutdown removes the socket")
os.rmdir(SOCK_DIR)

sys.exit(0 if ok else 1)
