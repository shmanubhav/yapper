"""lib/sample.py: argument parsing, clip listing, the play plan and the running-engine refusal.

Pure logic only: run() (the model and the speakers) is never called, and importing the module
loads no model.
"""
import os, socket, sys, tempfile
sys.dont_write_bytecode = True     # keep the clone free of __pycache__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import sample

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


def raises(fn, *a):
    try:
        fn(*a)
    except sample.UsageError as e:
        return str(e)
    return None


check("mlx_audio" not in sys.modules and "speaker" not in sys.modules, "importing sample loads no model code")
check(sample.SPEEDS == (0.95, 1.07, 1.2), f"three speeds {sample.SPEEDS}")

with tempfile.TemporaryDirectory() as tmp:
    voices = os.path.join(tmp, "voices")
    os.makedirs(voices)
    for n in ("b.wav", "a.wav", "notes.txt", ".hidden.wav"):
        open(os.path.join(voices, n), "wb").close()
    own = os.path.join(tmp, "own.wav")
    open(own, "wb").close()

    clips = sample.list_clips([], voices)
    check(clips == [os.path.join(voices, "a.wav"), os.path.join(voices, "b.wav")],
          f"no args -> every .wav in voices/, sorted, nothing else {[os.path.basename(c) for c in clips]}")
    check(sample.list_clips(["b", "a.wav", own], voices) == [os.path.join(voices, "b.wav"), os.path.join(voices, "a.wav"), own],
          "named clips: a name with or without .wav, or a path, in the order given")
    check(raises(sample.list_clips, ["nope"], voices) is not None, "an unknown name -> usage error")
    check(raises(sample.list_clips, [os.path.join(tmp, "x.mp3")], voices) is not None, "a non-.wav path -> usage error")
    empty = os.path.join(tmp, "empty")
    os.makedirs(empty)
    check("no .wav" in (raises(sample.list_clips, [], empty) or ""), "an empty voices/ -> usage error")

    steps = sample.plan(clips)
    check([s[0] for s in steps] == ["voice 1, speed 0.95", "voice 1, speed 1.07", "voice 1, speed 1.2",
                                    "voice 2, speed 0.95", "voice 2, speed 1.07", "voice 2, speed 1.2"],
          f"each clip at each speed, announced as 'voice N, speed X' {[s[0] for s in steps]}")
    check([s[1] for s in steps[:3]] == [clips[0]] * 3 and [s[2] for s in steps[:3]] == [0.95, 1.07, 1.2],
          "clip by clip, slowest first")

check("unknown option" in (raises(sample.parse, ["--fast"]) or ""), "an unknown option -> usage error")
check("yapper --sample" in (raises(sample.parse, ["--help"]) or ""), "--help prints the usage")
VOICES_DIR = os.path.join(ROOT, "voices")
shipped = sorted(os.path.join(VOICES_DIR, n) for n in os.listdir(VOICES_DIR) if n.endswith(".wav"))
check(sample.parse([]) == shipped and os.path.join(VOICES_DIR, "derry_b.wav") in shipped,
      f"with no args, every shipped clip is sampled, derry_b.wav among them ({len(shipped)})")

# The engine refusal: something answering on the socket -> refuse before any model work.
srv_dir = tempfile.mkdtemp(prefix="yp", dir="/tmp")
busy = os.path.join(srv_dir, "speech.sock")
srv = socket.socket(socket.AF_UNIX)
srv.bind(busy)
srv.listen(16)
check(sample.engine_running(busy) is True, "a listening socket reads as a running engine")
called = []
sample.run = lambda steps: called.append(steps)        # never the real model
os.environ["YAPPER_SOCKET"] = busy
rc = sample.main([])
check(rc == 1 and not called, f"engine on the socket -> exits 1, never loads the model (rc={rc})")
srv.close()
check(sample.engine_running(busy) is False, "a socket nobody listens on reads as free")
os.unlink(busy)
check(sample.engine_running(busy) is False, "no socket file reads as free")
open_dir = tempfile.mkdtemp(prefix="yp", dir="/tmp")
os.chmod(open_dir, 0o755)
os.environ["YAPPER_SOCKET"] = os.path.join(open_dir, "speech.sock")
rc = sample.main([])
check(rc == 2 and not called, f"a socket in an open folder -> refused before any check or model work (rc={rc})")
os.rmdir(open_dir)
rc = sample.main(["nope"])
check(rc == 2 and not called, "a bad clip -> exits 2 before any model work")
os.rmdir(srv_dir)

sys.exit(0 if ok else 1)
