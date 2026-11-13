"""lib/speaker.py AfPlayer: the local player. A stub afplay first on PATH records what it was
asked to play; the real afplay never runs and nothing sounds."""
import os, shutil, sys, tempfile, time
sys.dont_write_bytecode = True     # keep the clone free of __pycache__

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
W = tempfile.mkdtemp()
LOG = os.path.join(W, "afplay.log")
stub = os.path.join(W, "afplay")
with open(stub, "w") as f:    # logs its args, "plays" for STUB_PLAY_S, logs "end" when done or killed
    f.write(f'#!/bin/sh\necho "args $*" >> {LOG}\ntrap \'echo "end" >> {LOG}; exit 0\' TERM\n'
            f'sleep "${{STUB_PLAY_S:-0.2}}" & wait $!\necho "end" >> {LOG}\n')
os.chmod(stub, 0o755)
os.environ["PATH"] = f"{W}:{os.environ.get('PATH', '/usr/bin:/bin')}"
os.environ.pop("YAPPER_TTS_SPEED", None)

sys.path.insert(0, os.path.join(ROOT, "lib"))
from speaker import AfPlayer, SPEED

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


def lines():
    return open(LOG).read().splitlines() if os.path.exists(LOG) else []


CLIP = os.path.join(ROOT, "voices", "derry_b.wav")

# 1. A clip plays through afplay at the configured rate, pitch kept, and wait() returns when done.
os.environ["STUB_PLAY_S"] = "0.2"
p = AfPlayer(SPEED)
h = p.start(CLIP)
h.wait()
check(lines()[:1] == [f"args -r 1.07 -q 1 {CLIP}"], f"afplay -r <speed> -q 1 <clip>, default speed 1.07 {lines()[:1]}")
check(lines()[-1:] == ["end"], "wait() returns after the clip ends")
p.kill()                                                # killing a finished clip is harmless

# 2. kill() cuts a clip that is playing, at once.
os.unlink(LOG)
os.environ["STUB_PLAY_S"] = "5"
p = AfPlayer(1.2)
h = p.start(CLIP)
time.sleep(0.3)
t = time.time()
p.kill()
h.wait()
check(time.time() - t < 2, f"kill() stops a playing clip ({time.time() - t:.2f}s)")
check(lines()[:1] == [f"args -r 1.2 -q 1 {CLIP}"], f"another speed is passed through {lines()[:1]}")
p.kill()

shutil.rmtree(W, ignore_errors=True)
sys.exit(0 if ok else 1)
