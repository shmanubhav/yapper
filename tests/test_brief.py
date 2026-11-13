"""bin/voice-brief: the UserPromptSubmit hook.

Two jobs: deliver the <speak> contract into this turn's context, and cut speech still playing
from the previous turn. It sits on Claude Code's BLOCKING prompt path, so the slow-server case
below is the important one: a hung speech server must never delay the user's typing.
"""
import json, os, subprocess, sys, threading, time
sys.dont_write_bytecode = True     # keep the clone free of __pycache__
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import unixmock

hits = []


class Fast(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


class Slow(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        time.sleep(5)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


fast = unixmock.Mock(handler=Fast)
slow = unixmock.Mock(handler=Slow)

PAYLOAD = json.dumps({
    "session_id": "t", "transcript_path": "/tmp/x.jsonl", "cwd": "/tmp",
    "prompt_id": "p", "permission_mode": "auto",
    "hook_event_name": "UserPromptSubmit", "prompt": "why is the build failing"})

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


def run(sock, payload=PAYLOAD):
    return subprocess.run(["./bin/voice-brief"], input=payload, text=True,
                          capture_output=True,
                          env={**os.environ, "YAPPER_SOCKET": sock})


# --- the instruction reaches stdout, which is what Claude Code injects into context
hits.clear()
r = run(fast.path)
check(r.returncode == 0, "exits 0")
check("<speak>" in r.stdout, "stdout carries the <speak> tag name")
check("VOICE MODE" in r.stdout.upper(), "stdout states voice mode is active")
check("two to four sentences" in r.stdout.lower(), "stdout carries the length rule")
check(r.stderr.strip() == "", f"fast TTS -> stderr empty {r.stderr!r}")

# --- speech from the previous turn is cut
check(hits == ["/stop"], f"posted to /stop exactly once {hits}")

# --- a HUNG speech server must not stall prompt submission
t0 = time.time()
r = run(slow.path)
elapsed = time.time() - t0
check(0.5 < elapsed < 3, f"hung TTS -> 1s timeout bites, not an instant error ({elapsed:.2f}s, server sleeps 5s)")
check(r.returncode == 0, "hung TTS -> still exits 0")
check("<speak>" in r.stdout, "hung TTS -> instruction still delivered")
check(r.stderr.strip() == "", f"hung TTS -> stderr empty {r.stderr!r}")

# --- a malformed payload must not stop the instruction being delivered
r = run(fast.path, payload="not json at all")
check(r.returncode == 0, "malformed payload -> exits 0")
check("<speak>" in r.stdout, "malformed payload -> instruction still delivered")

# --- nothing may go to stderr; Claude Code surfaces hook stderr to the user
check(r.stderr.strip() == "", f"stderr is empty {r.stderr!r}")

# --- reader closes before the write: must STILL exit 0 with clean stderr
# Regression guard. Buffered print() flushes at interpreter shutdown, after sys.exit(0), so a
# closed reader used to produce exit 120 and "Exception ignored while flushing sys.stdout" on
# stderr - escaping every except block in the script.
r_fd, w_fd = os.pipe()
p = subprocess.Popen(["./bin/voice-brief"], stdin=subprocess.PIPE, stdout=w_fd,
                     stderr=subprocess.PIPE,
                     env={**os.environ,
                          "YAPPER_SOCKET": fast.path})
os.close(w_fd)
os.close(r_fd)
try:
    p.stdin.write(PAYLOAD.encode())
    p.stdin.close()
except (BrokenPipeError, OSError):
    pass
err = p.stderr.read()
p.wait()
check(p.returncode == 0, f"closed stdout reader -> exit 0 (got {p.returncode})")
check(err == b"", f"closed stdout reader -> stderr empty ({len(err)}B: {err[:80]!r})")

# --- the contract lists exactly the tags speak-hook lets through, and asks for 1-3 per block
import importlib.machinery, importlib.util, re as _re


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


hook = load("speak_hook", "./bin/speak-hook")
r = run(fast.path)
listed = set(_re.findall(r"\[([a-z ]+)\]", r.stdout))
check(listed == set(hook.ALLOWED_TAGS), f"contract tags == speak-hook whitelist (diff {sorted(listed ^ set(hook.ALLOWED_TAGS))})")
check(len(hook.ALLOWED_TAGS) == 14, f"14 tags allowed ({len(hook.ALLOWED_TAGS)})")
for bad in ("advertisement", "narration", "shush", "cough", "sniff"):
    check(f"[{bad}]" not in r.stdout, f"excluded tag absent: {bad}")
check("one to three" in r.stdout.lower(), "contract asks for one to three tags per block")
check("two to four sentences" in r.stdout.lower(), "length rule unchanged")

sys.exit(0 if ok else 1)
