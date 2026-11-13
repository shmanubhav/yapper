"""The engine's private Unix socket: lib/speechsock.py, the server's bind/release in lib/speaker.py,
and every client following YAPPER_SOCKET.

Stand-in servers on sockets in private temp folders; nothing loads a model or plays sound.
"""
import json, os, shutil, socket, subprocess, sys, tempfile, time
sys.dont_write_bytecode = True     # keep the clone free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import unixmock                    # also puts lib/ on sys.path
import speechsock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


def problem(path, uid=None):
    try:
        speechsock.check_path(path, uid)
        return None
    except speechsock.SocketError as e:
        return str(e)


TMP = unixmock.private_dir()
BASE = {k: v for k, v in os.environ.items() if not k.startswith("YAPPER_")}
BASE["YAPPER_LOG"] = os.path.join(TMP, "speak.log")
REPLY = json.dumps({"last_assistant_message": "Done. <speak>The socket test spoke.</speak>"})

# --- where the socket lives
env_free = {k: v for k, v in os.environ.items() if k != "YAPPER_SOCKET"}
r = subprocess.run([sys.executable, "-I", "-B", "-c", f"import sys; sys.path.insert(0, {os.path.join(ROOT, 'lib')!r}); "
                    "import speechsock; print(speechsock.default_socket())"], env=env_free, capture_output=True, text=True)
check(r.stdout.strip() == os.path.join(os.path.expanduser("~"), ".cache", "yapper", "speech.sock"),
      f"default socket is ~/.cache/yapper/speech.sock {r.stdout.strip()}")

# --- check_path: private folder only, absolute, inside the sun_path limit
good = os.path.join(TMP, "speech.sock")
check(problem(good) is None, "a socket in a 700 folder of ours is accepted")
check("absolute" in (problem("relative/speech.sock") or ""), "a relative path is refused")
long_path = "/tmp/" + "x" * 100 + "/speech.sock"
check("macOS allows at most 103" in (problem(long_path) or ""), f"a path over the 104-byte sun_path limit is refused clearly")
check("missing" in (problem(os.path.join(TMP, "nope", "speech.sock")) or ""), "a missing folder is refused")
open_dir = os.path.join(TMP, "open")
os.mkdir(open_dir)
os.chmod(open_dir, 0o755)
check("open to other users" in (problem(os.path.join(open_dir, "s.sock")) or "") and "chmod 700" in problem(os.path.join(open_dir, "s.sock")),
      "a folder with mode 755 is refused, with the chmod to fix it")
os.chmod(open_dir, 0o710)
check("open to other users" in (problem(os.path.join(open_dir, "s.sock")) or ""), "any group bit (710) is refused")
check("not you" in (problem(good, uid=os.getuid() + 1) or ""), "a folder owned by another user is refused (simulated uid)")
check("not you" in (problem("/usr/bin/speech.sock") or "") or "open to other users" in (problem("/usr/bin/speech.sock") or ""),
      "a system folder (root-owned) is refused")

# --- the client helper: a round trip over the socket
with unixmock.Mock(code=202) as m:
    status, body = speechsock.request("POST", "/speak", body=b'{"text": "hi"}',
                                      headers={"Content-Type": "application/json"}, sock_path=m.path)
    check(status == 202 and m.hits == [("POST", "/speak", b'{"text": "hi"}')], f"request() round-trips over the socket {m.hits}")
    check(speechsock.answers(m.path) is True, "answers() sees a live socket")
    os.chmod(m.dir, 0o755)
    try:
        speechsock.request("POST", "/stop", sock_path=m.path)
        refused = False
    except speechsock.SocketError:
        refused = True
    check(refused and len(m.hits) == 1, "request() refuses a socket in an open folder before connecting")
    os.chmod(m.dir, 0o700)

    # --- every client follows YAPPER_SOCKET
    m.srv.hits.clear()
    subprocess.run([os.path.join(ROOT, "bin", "speak-hook")], input=REPLY, text=True, capture_output=True,
                   env={**BASE, "YAPPER_SOCKET": m.path}, timeout=10)
    check(m.paths() == ["/speak"] and json.loads(m.hits[0][2])["text"] == "The socket test spoke.",
          f"speak-hook posts /speak on YAPPER_SOCKET {m.paths()}")
    m.srv.hits.clear()
    r = subprocess.run([os.path.join(ROOT, "bin", "voice-brief")], input="{}", text=True, capture_output=True,
                       env={**BASE, "YAPPER_SOCKET": m.path}, timeout=10)
    check(m.paths() == ["/stop"] and "VOICE MODE" in r.stdout, f"voice-brief posts /stop on YAPPER_SOCKET {m.paths()}")
    m.srv.hits.clear()
    r = subprocess.run([sys.executable, "-I", "-B", "-c", f"import sys; sys.path.insert(0, {os.path.join(ROOT, 'lib')!r}); "
                        "import micwatch; print(micwatch.stop_speech())"],
                       env={**BASE, "YAPPER_SOCKET": m.path}, capture_output=True, text=True, timeout=10)
    check(r.stdout.strip() == "True" and m.paths() == ["/stop"], f"micwatch stops speech on YAPPER_SOCKET {m.paths()}")
    m.srv.hits.clear()
    subprocess.run([os.path.join(ROOT, "bin", "shh")], env={**BASE, "YAPPER_SOCKET": m.path}, timeout=10)
    check(m.paths() == ["/stop"], f"shh posts /stop on YAPPER_SOCKET {m.paths()}")

# --- hooks stay silent when there is no engine, or the socket folder is not private
for label, sock in (("missing socket", os.path.join(TMP, "gone.sock")), ("open folder", os.path.join(open_dir, "s.sock")),
                    ("path too long", long_path)):
    for hook, stdin in (("speak-hook", REPLY), ("voice-brief", "{}")):
        r = subprocess.run([os.path.join(ROOT, "bin", hook)], input=stdin, text=True, capture_output=True,
                           env={**BASE, "YAPPER_SOCKET": sock}, timeout=10)
        quiet = r.returncode == 0 and r.stderr == "" and (r.stdout == "" if hook == "speak-hook" else "VOICE MODE" in r.stdout)
        check(quiet, f"{hook}: {label} -> exit 0, nothing on stderr (rc={r.returncode} {r.stderr[:60]!r})")
log = open(BASE["YAPPER_LOG"]).read()
check("TTS-FAIL" in log and "open to other users" in log, "speak-hook logs why it did not speak")
check(oct(os.stat(BASE["YAPPER_LOG"]).st_mode & 0o777) == "0o600", "speak.log is created 600")

# --- the server: private socket, stale cleanup, never steal a live one, unlink on release
import speaker
srv_dir = unixmock.private_dir()
path = os.path.join(srv_dir, "speech.sock")
stale = socket.socket(socket.AF_UNIX)
stale.bind(path)
stale.close()                                   # a socket file nobody listens on
srv = speaker.bind(path)
check(oct(os.stat(path).st_mode & 0o777) == "0o600", "a stale socket is replaced; the new one is mode 600")
try:
    speaker.bind(path)
    stole = True
except speechsock.SocketError as e:
    stole = False
    msg = str(e)
check(not stole and "already answers" in msg, "a second engine refuses a socket that answers")
speechsock.write_pidfile(path + ".speaker.pid")         # what main() does once it owns the socket
check(open(path + ".speaker.pid").read().strip() == str(os.getpid())
      and oct(os.stat(path + ".speaker.pid").st_mode & 0o777) == "0o600", "the engine records its own pid (600)")


class _Stops:
    stopped = 0

    def stop(self):
        _Stops.stopped += 1


speaker.speaker = _Stops()
speaker.release(srv, path)
speaker.speaker = None
check(not os.path.exists(path), "release() removes the socket")
check(not os.path.exists(path + ".speaker.pid"), "release() removes its own pidfile")
check(_Stops.stopped == 1, "release() stops playback first (afplay killed, queued wavs removed)")
with open(path, "w") as f:
    f.write("not a socket")
try:
    speaker.bind(path)
    replaced = True
except speechsock.SocketError:
    replaced = False
check(not replaced and open(path).read() == "not a socket", "a regular file at the socket path is never replaced")
os.unlink(path)
os.chmod(srv_dir, 0o755)
try:
    speaker.bind(path)
    bound = True
except speechsock.SocketError:
    bound = False
check(not bound and not os.path.exists(path), "the server refuses an open folder")
os.chmod(srv_dir, 0o700)
shutil.rmtree(srv_dir, ignore_errors=True)

# --- pidfile claims (lib/micwatch.py uses claim_pidfile, so two launchers never run two watchers)
pf = os.path.join(TMP, "w.pid")
marker = "/some/clone/lib/micwatch.py"
holder = subprocess.Popen(["/bin/sh", "-c", "sleep 30; :", marker])
time.sleep(0.2)
open(pf, "w").write(str(holder.pid))
check(speechsock.claim_pidfile(pf, marker) is False and open(pf).read().strip() == str(holder.pid),
      "a live process running the script keeps its claim")
check(speechsock.claim_pidfile(pf, "/other/lib/micwatch.py") is True and open(pf).read().strip() == str(os.getpid()),
      "a live pid that runs something else (pid reuse) is treated as stale")
holder.kill(); holder.wait()
open(pf, "w").write(str(holder.pid))
check(speechsock.claim_pidfile(pf, marker) is True, "a dead holder's claim is replaced")
speechsock.release_pidfile(pf)
check(not os.path.exists(pf), "release_pidfile removes our own claim")
open(pf, "w").write("1")
speechsock.release_pidfile(pf)
check(os.path.exists(pf), "release_pidfile never removes someone else's")
os.unlink(pf)

# --- the shell check follows a symlinked folder, like os.stat in check_path
real_ok = os.path.join(TMP, "real-ok")
os.mkdir(real_ok, 0o700)
real_open = os.path.join(TMP, "real-open")
os.mkdir(real_open)
os.chmod(real_open, 0o755)
os.symlink(real_ok, os.path.join(TMP, "link-ok"))
os.symlink(real_open, os.path.join(TMP, "link-open"))


def shell_problem(sock):
    return subprocess.run(["bash", "-c", '. "$1"; sock_problem "$2"', "_", os.path.join(ROOT, "lib", "sockcheck.sh"), sock],
                          capture_output=True, text=True, timeout=10).stdout.strip()


check(shell_problem(os.path.join(TMP, "link-ok", "s.sock")) == "" and problem(os.path.join(TMP, "link-ok", "s.sock")) is None,
      "shell and Python agree: a symlink to a private folder is fine")
check("open to other users" in shell_problem(os.path.join(TMP, "link-open", "s.sock"))
      and "open to other users" in (problem(os.path.join(TMP, "link-open", "s.sock")) or ""),
      "shell and Python agree: a symlink to an open folder is refused")
check("macOS allows at most 103" in shell_problem(long_path), "the shell check also enforces the sun_path limit")

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok else 1)
