"""bin/speak-hook decision logic.

There is no marker and no gating. The hook exists only inside voice-settings.json, which only
bin/yapper loads, so if it runs at all it should speak.

The SPOKE assertions are load-bearing. If only failures were logged, an empty speak.log could
not be told apart from a hook that never fired, so 'zero fallbacks' would prove nothing.
"""
import json, os, subprocess, sys, tempfile, threading
sys.dont_write_bytecode = True     # keep the clone free of __pycache__
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import unixmock

LOG = tempfile.mktemp(prefix="speak-", suffix=".log")
os.environ["YAPPER_LOG"] = LOG
spoken = []


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        spoken.append(json.loads(self.rfile.read(n)).get("text", ""))
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()


srv = unixmock.Mock(handler=Mock)
TTS = srv.path                      # the hook reaches the engine through YAPPER_SOCKET

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


def transcript(assistant, user_turns=1, tool_results=0):
    p = tempfile.mktemp(suffix=".jsonl")
    with open(p, "w") as f:
        for i in range(user_turns):
            f.write(json.dumps({"type": "user", "message": {"content": f"q{i}"}}) + "\n")
        for _ in range(tool_results):
            f.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "x"}]}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": assistant}]}}) + "\n")
    return p


def run(tp):
    return subprocess.run(["./bin/speak-hook"], input=json.dumps({"transcript_path": tp}),
                          text=True, capture_output=True,
                          env={**os.environ, "YAPPER_SOCKET": TTS})


def run_payload(payload):
    return subprocess.run(["./bin/speak-hook"], input=json.dumps(payload), text=True,
                          capture_output=True,
                          env={**os.environ, "YAPPER_SOCKET": TTS})


def logsize():
    return os.path.getsize(LOG) if os.path.exists(LOG) else 0


def logsince(offset):
    """Read from a BYTE offset, not a string slice. os.path.getsize returns bytes, and the
    log carries repr()'d text that can contain multi-byte characters, so slicing a decoded
    string by a byte offset silently reads the wrong window."""
    with open(LOG, "rb") as f:
        f.seek(offset)
        return f.read().decode("utf-8", "replace")


# --- the tag is spoken verbatim, with no marker anywhere
spoken.clear()
run(transcript("Long screen answer.\n<speak>Forty seven restarts.</speak>"))
check(spoken == ["Forty seven restarts."], f"speaks the tag {spoken}")

# --- success is logged too
before = logsize()
spoken.clear()
run(transcript("Answer.\n<speak>The subagent found six PRs.</speak>"))
check(spoken == ["The subagent found six PRs."], "speaks the tag again")
check("SPOKE:" in logsince(before), "success writes a SPOKE line")
check("six PRs" in logsince(before), "the SPOKE line records what was said")

# --- a typed turn is NOT special-cased any more; there is no marker to check
spoken.clear()
run(transcript("Reply.\n<speak>Still speaks.</speak>", user_turns=7))
check(spoken == ["Still speaks."], "turn index is irrelevant")

# --- a turn with background work fires Stop repeatedly; every firing speaks
spoken.clear()
run(transcript("Agent finished.\n<speak>Two are stale.</speak>", tool_results=5))
check(spoken == ["Two are stale."], "second Stop after subagent work still speaks")

# --- missing tag still falls back, still logs, and strips markdown
before = logsize()
spoken.clear()
run(transcript("```py\nprint(1)\n```\nThe agent restarted forty seven times. Second sentence."))
check(spoken and "forty seven" in spoken[0], f"missing tag -> speaks prose {spoken}")
check(spoken and "print(1)" not in spoken[0], "code block stripped from speech")
check(spoken and "Second sentence" not in spoken[0], "only the first sentence is spoken")
check("FALLBACK-USED" in logsince(before), "fallback still logged")

# --- SPOKE and FALLBACK-USED must be distinguishable
check("SPOKE:" not in logsince(before), "a fallback does NOT write a SPOKE line")

# --- long tags are capped
spoken.clear()
run(transcript("x\n<speak>" + ("word " * 400) + "</speak>"))
check(spoken and len(spoken[0]) <= 600, f"MAX_CHARS enforced ({len(spoken[0])})")

# --- nothing speakable: no request, no crash
spoken.clear()
r = run(transcript("```py\nprint(1)\n```"))
check(spoken == [], f"nothing speakable -> no TTS request {spoken}")
check(r.returncode == 0, "still exits 0")

# --- a Stop hook must never emit stdout
check(r.stdout == "", f"stdout is empty {r.stdout!r}")

# --- a <speak> tag that is PRESENT BUT EMPTY is its own outcome, not a fallback
before = logsize()
spoken.clear()
run(transcript("Answer on screen.\n<speak></speak>"))
check(spoken == [], f"empty <speak> -> nothing spoken {spoken}")
w = logsince(before)
check("EMPTY-TAG" in w, "empty <speak> -> logged as EMPTY-TAG")
check("FALLBACK-USED" not in w, "empty <speak> -> NOT counted as a forgotten tag")
check("SPOKE:" not in w, "empty <speak> -> NOT counted as a success")

# --- whitespace-only content behaves identically
before = logsize()
spoken.clear()
run(transcript("Answer.\n<speak>   </speak>"))
check(spoken == [], f"whitespace-only <speak> -> nothing spoken {spoken}")
check("EMPTY-TAG" in logsince(before), "whitespace-only <speak> -> EMPTY-TAG")

# --- prose that merely MENTIONS the tag must never have the tag name read aloud
spoken.clear()
run(transcript("Use a <speak> block here. Second sentence."))
check(spoken and "<" not in spoken[0], f"literal tag never reaches speech {spoken}")
check(spoken and "Use a" in spoken[0], f"the surrounding prose is still spoken {spoken}")

# --- a reply that QUOTES an example block and then emits the real one speaks the REAL one
# re.search returns the FIRST match, but the contract puts the real block LAST with nothing
# after it. Quoting <speak> syntax in prose is ordinary in sessions about this harness.
spoken.clear()
run(transcript("Docs say <speak>hi</speak> is the format.\n"
               "Now the real answer.\n"
               "<speak>Six PRs are mergeable.</speak>"))
check(spoken == ["Six PRs are mergeable."], f"last <speak> block wins, not the first {spoken}")

# --- the payload's own copy of the reply is preferred over the transcript file
# Stop can fire before the assistant entry is flushed to the JSONL. Reading the file lost that
# race on live runs and logged FALLBACK-EMPTY on replies that plainly had a <speak> block.
spoken.clear()
run_payload({"transcript_path": "/nonexistent/never/written.jsonl",
             "last_assistant_message": "On screen.\n<speak>Six PRs are mergeable.</speak>"})
check(spoken == ["Six PRs are mergeable."],
      f"payload text used even when the transcript is unreadable {spoken}")

# --- payload wins over a readable transcript that disagrees
spoken.clear()
tp = transcript("Stale.\n<speak>stale transcript text</speak>")
run_payload({"transcript_path": tp,
             "last_assistant_message": "Fresh.\n<speak>fresh payload text</speak>"})
check(spoken == ["fresh payload text"], f"payload takes precedence over the file {spoken}")

# --- absent field still falls back to scanning the transcript
spoken.clear()
tp = transcript("Answer.\n<speak>from the transcript</speak>")
run_payload({"transcript_path": tp})
check(spoken == ["from the transcript"], f"fallback to transcript scan still works {spoken}")

# --- neither source available: no crash, no request
spoken.clear()
r = run_payload({})
check(spoken == [], f"empty payload -> nothing spoken {spoken}")
check(r.returncode == 0, "empty payload -> exits 0")

# --- delivery is logged separately from extraction: TTS-OK on 202, TTS-FAIL on a dead server
before = logsize()
spoken.clear()
run(transcript("Answer.\n<speak>Delivered.</speak>"))
w = logsince(before)
check("SPOKE:" in w and "TTS-OK:" in w, f"live server -> SPOKE and TTS-OK both logged")
check("TTS-FAIL" not in w, "live server -> no TTS-FAIL")

before = logsize()
spoken.clear()
r = subprocess.run(["./bin/speak-hook"], input=json.dumps({"last_assistant_message": "x\n<speak>Undelivered.</speak>"}),
                   text=True, capture_output=True,
                   env={**os.environ, "YAPPER_SOCKET": os.path.join(unixmock.private_dir(), "none.sock")})
w = logsince(before)
check("SPOKE:" in w, "dead server -> extraction still logged as SPOKE")
check("TTS-FAIL:" in w, f"dead server -> delivery logged as TTS-FAIL {w.strip()[-80:]!r}")
check("TTS-OK" not in w, "dead server -> no TTS-OK")
check(r.returncode == 0, "dead server -> still exits 0")

# --- known emotion tags pass through untouched
spoken.clear()
run(transcript("x\n<speak>[sigh] The backup failed again. [chuckle] Classic.</speak>"))
check(spoken == ["[sigh] The backup failed again. [chuckle] Classic."], f"known tags kept {spoken}")

# --- an invented tag is dropped, never read aloud
spoken.clear()
run(transcript("x\n<speak>[smirk] Fine. [laugh] Really fine.</speak>"))
check(spoken == ["Fine. [laugh] Really fine."], f"unknown tag dropped {spoken}")

# --- odd spellings are normalised
spoken.clear()
run(transcript("x\n<speak>[Sigh] Again. [ clear throat ] Right.</speak>"))
check(spoken == ["[sigh] Again. [clear throat] Right."], f"case and spaces normalised {spoken}")

# --- a long block is cut at the last sentence end inside the cap
long = "This sentence is exactly fifty characters long, ok. " * 13
spoken.clear()
run(transcript("x\n<speak>" + long + "</speak>"))
check(spoken and len(spoken[0]) <= 600 and spoken[0].endswith("ok."), f"cut at a sentence end ({len(spoken[0]) if spoken else 0})")

# --- with no sentence end in range, the cut never leaves half a tag
spoken.clear()
run(transcript("x\n<speak>" + ("word " * 118) + "[whispering] and more words after it</speak>"))
check(spoken and len(spoken[0]) <= 600 and "[" not in spoken[0], f"no partial tag ({spoken[0][-30:] if spoken else ''!r})")

sys.exit(0 if ok else 1)
