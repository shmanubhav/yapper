"""lib/micwatch.py: edge detection and stop call. No CoreAudio, no real speech server."""
import logging, os, sys, threading, time
sys.dont_write_bytecode = True     # keep the clone free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import unixmock                    # also puts lib/ on sys.path
import micwatch

srv = unixmock.Mock()
ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


# --- rising
check(micwatch.rising({1: False}, {1: True}) == [1], "idle -> on fires")
check(micwatch.rising({1: True}, {1: True}) == [], "on -> on does not fire")
check(micwatch.rising({1: True}, {1: False}) == [], "on -> idle does not fire")
check(micwatch.rising({1: None}, {1: True}) == [1], "None -> on fires")
check(micwatch.rising({}, {1: True}) == [1], "absent -> on fires")
check(micwatch.rising({1: True}, {1: None}) == [], "on -> None does not fire")


# --- watch
def stops_for(seq):
    n = []
    it = iter(seq)
    micwatch.watch(lambda: next(it), lambda: n.append(1), sleep=lambda s: None, ticks=len(seq))
    return len(n)


T, F = True, False
check(stops_for([{1: F}, {1: F}, {1: T}, {1: T}, {1: F}, {1: T}]) == 2, "two starts -> 2 stops")
check(stops_for([{1: T}, {1: T}, {1: F}]) == 0, "already on at startup -> 0 stops")
check(stops_for([{1: F, 2: F}, {1: T, 2: T}]) == 1, "two devices in one tick -> 1 stop")
check(stops_for([{1: F}, {1: F, 2: T}]) == 1, "device appears already on -> 1 stop")
check(stops_for([{1: F}, {1: None}, {1: T}]) == 1, "read failure in between -> 1 stop")

# --- scan_message
lv, tx = micwatch.scan_message([])
check(lv == logging.WARNING and "no input devices" in tx, f"empty scan -> WARNING {tx!r}")
lv, tx = micwatch.scan_message(["HD Webcam", "MacBook Mic"])
check(lv == logging.INFO and "HD Webcam" in tx and "MacBook Mic" in tx, f"non-empty scan -> INFO {tx!r}")

# --- stop_speech
check(micwatch.stop_speech(srv.path) is True, "stop_speech returns True on 2xx")
check(srv.paths() == ["/stop"], f"posted to /stop exactly once {srv.paths()}")
os.environ["YAPPER_SOCKET"] = srv.path
check(micwatch.stop_speech() is True and srv.paths() == ["/stop", "/stop"], "with no argument it follows YAPPER_SOCKET")
t0 = time.time()
r = micwatch.stop_speech(os.path.join(unixmock.private_dir(), "nothing.sock"))
elapsed = time.time() - t0
check(r is False, "stop_speech returns False when no engine answers")
check(elapsed < 3, f"returns promptly ({elapsed:.2f}s)")
srv.close()

sys.exit(0 if ok else 1)
