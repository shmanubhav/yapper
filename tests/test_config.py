"""~/.config/yapper/config: KEY=value lines read as data by bin/yapper, never sourced.

Runs `yapper --config`, which parses the file and prints what it would use, then exits. Stub
tools on PATH and a temp HOME; nothing starts and nothing speaks.
"""
import os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_VOICE = os.path.join(ROOT, "voices", "derry_b.wav")

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


tmp = tempfile.mkdtemp()
STUBS = os.path.join(tmp, "stubs")
os.makedirs(STUBS)
for name, body in {"curl": "exit 7", "pgrep": "exit 0", "pkill": "exit 0",
                   "open": "exit 1", "claude": "exit 0"}.items():
    with open(os.path.join(STUBS, name), "w") as f:
        f.write(f"#!/bin/sh\n{body}\n")
    os.chmod(os.path.join(STUBS, name), 0o755)
CFG = os.path.join(tmp, ".config", "yapper", "config")
os.makedirs(os.path.dirname(CFG))
MARK = os.path.join(tmp, "pwned")


def run(text, args=("--config",), **env):
    if text is None:
        if os.path.exists(CFG):
            os.unlink(CFG)
    else:
        with open(CFG, "w") as f:
            f.write(text)
    e = {"PATH": f"{STUBS}:/usr/bin:/bin", "HOME": tmp, **env}
    r = subprocess.run([os.path.join(ROOT, "bin", "yapper"), *args], env=e,
                       capture_output=True, text=True, timeout=20)
    vals = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] in ("voice", "speed"):
            vals[parts[0]] = parts[1]
    return r, vals


r, v = run(None)
check(r.returncode == 0 and v == {"voice": DEFAULT_VOICE, "speed": "1.07"} and "(none)" in r.stdout,
      f"no config file -> built-in voice and speed {v}")

r, v = run("voice=derry_b\nspeed=1.2\n")
check(r.returncode == 0 and v == {"voice": DEFAULT_VOICE, "speed": "1.2"}, f"a name in voices/ and a speed {v}")

r, v = run("# my voice\n\n  voice = derry_b.wav  \r\ncolour=blue\nspeed=0.95")
check(r.returncode == 0 and v == {"voice": DEFAULT_VOICE, "speed": "0.95"},
      f"comments, blank lines, spaces, CRLF, an unknown key and no final newline are fine {v} {r.stderr.strip()[-80:]!r}")

own = os.path.join(tmp, "my voice.wav")
open(own, "wb").close()
r, v = run(f"voice={own}\n")
check(r.returncode == 0 and v.get("voice") == own, f"an absolute .wav path, with a space {v}")
r, v = run("voice=~/my voice.wav\n")
check(r.returncode == 0 and v.get("voice") == own, f"a ~/ path {v}")

r, v = run("speed=1.2\nvoice=derry_b\n", YAPPER_TTS_SPEED="0.95", YAPPER_TTS_VOICE_WAV=own)
check(r.returncode == 0 and v == {"voice": own, "speed": "0.95"}, f"environment variables win over the file {v}")

alt = os.path.join(tmp, "alt.conf")
with open(alt, "w") as f:
    f.write("speed=1.5\n")
r, v = run("speed=1.2\n", YAPPER_CONFIG=alt)
check(r.returncode == 0 and v.get("speed") == "1.5" and alt in r.stdout, f"YAPPER_CONFIG points at another file {v}")

# Hostile or broken values: a clear error with the line number, and nothing ever runs.
for text, what in [
    (f"voice=$(touch {MARK})\n", "command substitution"),
    (f"voice=`touch {MARK}`\n", "backticks"),
    (f"speed=1; touch {MARK}\n", "a second command after the value"),
    (f"voice=derry_b\nspeed=$(touch {MARK})\n", "substitution in speed"),
]:
    r, _ = run(text)
    check(r.returncode != 0 and "line" in r.stderr and not os.path.exists(MARK),
          f"{what} -> refused, never executed ({r.stderr.strip()[-90:]!r})")
for text, what in [("speed=fast\n", "speed not a number"), ("speed=3\n", "speed out of range"),
                   ("speed=0.2\n", "speed too slow"), ("voice=nosuchvoice\n", "voice not in voices/"),
                   ("voice=/etc/passwd\n", "voice not a .wav"), ("just words\n", "line without =")]:
    r, _ = run(text)
    check(r.returncode != 0 and "line 1" in r.stderr, f"{what} -> clear error {r.stderr.strip()[-90:]!r}")

r, _ = run("speed=fast\n", args=("--status",))
check(r.returncode == 0, "a bad config never blocks --status")
r, _ = run("speed=fast\n", args=("--stop",))
check(r.returncode == 0, "a bad config never blocks --stop")

check(os.path.isfile(os.path.join(ROOT, "config.example")), "config.example ships")
r, v = run(open(os.path.join(ROOT, "config.example")).read())
check(r.returncode == 0, f"config.example parses as it ships {v} {r.stderr.strip()[-80:]!r}")

shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if ok else 1)
