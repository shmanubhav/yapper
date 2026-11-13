"""bin/shh: silence speech in progress, over the engine's private socket.

The only hard requirement is that it never fails loudly. It is typed mid-thought, often when
the speech server is not running at all, and a stack trace or a non-zero exit in that moment
is worse than doing nothing. It also never talks to a socket in a folder others can reach.
"""
import os, shutil, subprocess, sys, time
sys.dont_write_bytecode = True     # keep the clone free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import unixmock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


def run(sock):
    return subprocess.run([os.path.join(ROOT, "bin", "shh")], capture_output=True, text=True,
                          env={**os.environ, "YAPPER_SOCKET": sock}, timeout=10)


# --- reaches /stop on the socket
with unixmock.Mock() as m:
    r = run(m.path)
    check(r.returncode == 0, "exits 0 against a live server")
    check(m.paths() == ["/stop"], f"posted to /stop exactly once {m.paths()}")

    # --- a folder open to others: refused, silently
    os.chmod(m.dir, 0o755)
    r = run(m.path)
    check(r.returncode == 0 and r.stderr == "" and m.paths() == ["/stop"],
          f"socket folder mode 755 -> never contacted, still silent {m.paths()}")
    os.chmod(m.dir, 0o700)

# --- server not running: must stay silent and fast
d = unixmock.private_dir()
t0 = time.time()
r = run(os.path.join(d, "speech.sock"))
elapsed = time.time() - t0
check(r.returncode == 0, "exits 0 with no server listening")
check(r.stderr.strip() == "", f"prints nothing to stderr {r.stderr!r}")
check(elapsed < 3, f"returns promptly ({elapsed:.2f}s)")
r = run("/" + "x" * 120 + "/speech.sock")
check(r.returncode == 0 and r.stderr == "", "a socket path over the 104-byte limit -> silent")
shutil.rmtree(d, ignore_errors=True)

sys.exit(0 if ok else 1)
