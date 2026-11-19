"""Remote voice (yapper --remote): lib/player.py, the engine's RoutedPlayer and /player, and the
launcher's SSH forwards. Stub afplay and ssh only: nothing sounds, nothing connects to another Mac."""
import json, os, shutil, socket, stat, struct, subprocess, sys, tempfile, threading, time
sys.dont_write_bytecode = True     # keep the clone free of __pycache__
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import unixmock                    # also puts lib/ on sys.path
import player as pl
import speaker as sp
import speechsock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
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


def wav(n=64):
    body = b"WAVEfmt " + b"\0" * n
    return b"RIFF" + struct.pack("<I", len(body)) + body


W = unixmock.private_dir()
LOG = os.path.join(W, "afplay.log")
STUB = os.path.join(W, "afplay")
with open(STUB, "w") as f:     # logs its args, "plays" for STUB_PLAY_S, logs "end" when done or killed
    f.write(f'#!/bin/sh\necho "args $*" >> {LOG}\ntrap \'echo "end" >> {LOG}; exit 0\' TERM\n'
            f'sleep "${{STUB_PLAY_S:-0.2}}" & wait $!\necho "end" >> {LOG}\n')
os.chmod(STUB, 0o755)


def log_lines():
    return open(LOG).read().splitlines() if os.path.exists(LOG) else []


def start_player(path, play_s="0.2"):
    os.environ["STUB_PLAY_S"] = play_s
    p = pl.Player(afplay=STUB, speed="1.25")
    box = {}
    t = threading.Thread(target=pl.serve, args=(path, p), kwargs={"ready": lambda s: box.update(srv=s)}, daemon=True)
    t.start()
    wait_until(lambda: "srv" in box)
    return box["srv"], t


def send(path, data, timeout=5):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    s.sendall(data)
    s.shutdown(socket.SHUT_WR)
    out = b""
    while True:
        b = s.recv(64)
        if not b:
            break
        out += b
    s.close()
    return out


# --- player: plays a whole WAV, drops a partial one, STOP cuts the clip playing
PSOCK = os.path.join(W, "p.sock")
srv, th = start_player(PSOCK)
check(oct(os.stat(PSOCK).st_mode & 0o777) == "0o600", "player socket is mode 600")
check(send(PSOCK, wav()) == b"done\n", "a complete clip is answered done")
check(any(l.startswith("args -r 1.25 -q 1 ") for l in log_lines()), f"afplay ran at this Mac's speed {log_lines()}")
open(LOG, "w").close()
check(send(PSOCK, wav()[:-10]) == b"dropped\n" and log_lines() == [], f"a partial clip is dropped unplayed, and not answered done {log_lines()}")
srv.close(); th.join(2)

srv, th = start_player(PSOCK, play_s="5")
open(LOG, "w").close()
res = {}
threading.Thread(target=lambda: res.update(r=send(PSOCK, wav(), timeout=10)), daemon=True).start()
check(wait_until(lambda: any(l.startswith("args") for l in log_lines())), "long clip starts")
t0 = time.time()
send(PSOCK, b"STOP")
check(wait_until(lambda: "r" in res, 2) and res["r"] == b"done\n" and time.time() - t0 < 2, "STOP cuts the playing clip at once")
check(log_lines()[-1:] == ["end"], f"afplay was stopped {log_lines()}")
srv.close(); th.join(2)
check(not os.path.exists(PSOCK), "player removes its socket on close")

# a STOP never cuts a clip that arrived after it
class FakeConn:
    def __init__(self, data):
        self.chunks, self.sent = [data, b""], b""

    def recv(self, n):
        return self.chunks.pop(0)

    def sendall(self, b):
        self.sent += b

    def close(self):
        pass


pp = pl.Player(afplay=STUB)
kills = []
pp._kill = lambda: kills.append(1)
pp.playing = 7
pp.handle(FakeConn(b"STOP"), 6)
check(kills == [] and pp.stop_below == 6, "STOP 6 leaves clip 7 playing")
pp.handle(FakeConn(b"STOP"), 8)
check(kills == [1], "STOP 8 cuts clip 7")

opened = tempfile.mkdtemp(prefix="yp", dir="/tmp")
os.chmod(opened, 0o755)
try:
    pl.serve(os.path.join(opened, "p.sock"), pl.Player(afplay=STUB))
    check(False, "player refuses a socket in an open folder")
except speechsock.SocketError:
    check(True, "player refuses a socket in an open folder")


# --- engine routing: a registered player gets the clips; a dead one sends them back to this Mac
class FakeLocal:
    def __init__(self):
        self.started, self.kills = [], 0

    def start(self, path):
        self.started.append(path)
        return type("H", (), {"wait": lambda self: None})()

    def kill(self):
        self.kills += 1


clipf = os.path.join(W, "clip.wav")
open(clipf, "wb").write(wav())
srv, th = start_player(PSOCK)
local = FakeLocal()
r = sp.RoutedPlayer(local)
r.start(clipf).wait()
check(local.started == [clipf], "nothing registered: plays on this Mac")
open(LOG, "w").close()
r.register(PSOCK)
h = r.start(clipf)
check(isinstance(h, sp.RemoteClip), "registered: the clip goes to the remote player")
h.wait()
check(any(l.startswith("args") for l in log_lines()) and local.started == [clipf], f"remote played it, this Mac did not {log_lines()}")
check(r.target == PSOCK, "a good clip keeps the player registered")
srv.close(); th.join(2)
h = r.start(clipf)
check(local.started == [clipf, clipf] and r.target is None, "player gone: back to this Mac and unregistered")
r.kill()
check(local.kills == 1, "kill always stops local playback")


def rude_server(path, mode):
    """Accepts clips and then hangs up ("close") or never answers ("stall")."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    s.listen(4)
    held = []

    def run():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            if mode == "close":
                c.close()
            else:
                held.append(c)
    threading.Thread(target=run, daemon=True).start()
    return s


for mode in ("close", "stall"):
    RS = os.path.join(W, "r-" + mode + ".sock")
    rs = rude_server(RS, mode)
    local = FakeLocal()
    r = sp.RoutedPlayer(local)
    r.register(RS)
    old = sp.REMOTE_SLACK_S
    sp.REMOTE_SLACK_S = 0.5
    t0 = time.time()
    r.start(clipf).wait()
    sp.REMOTE_SLACK_S = old
    check(local.started == [clipf] and r.target is None and time.time() - t0 < 3,
          f"player that {mode}s: the clip plays here instead, and the player is dropped ({time.time() - t0:.1f}s)")
    rs.close()

srv, th = start_player(PSOCK, play_s="5")
open(LOG, "w").close()
local = FakeLocal()
r = sp.RoutedPlayer(local)
r.register(PSOCK)
h = r.start(clipf)
done = threading.Event()
threading.Thread(target=lambda: (h.wait(), done.set()), daemon=True).start()
check(wait_until(lambda: any(l.startswith("args") for l in log_lines())), "remote clip playing")
r.kill()
fin = done.wait(2)
check(fin and local.started == [], f"a cut remote clip ends at once and never falls back here (ended={fin} here={local.started})")
check(r.target == PSOCK, "a cut keeps the player registered")
check(wait_until(lambda: log_lines()[-1:] == ["end"]), f"the player got STOP {log_lines()}")
srv.close(); th.join(2)

DEAD = os.path.join(W, "r-dead.sock")
d = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
d.bind(DEAD)
d.close()                          # a socket file nobody listens on: an SSH connection that ended
r = sp.RoutedPlayer(FakeLocal())
r.register(DEAD)
r.start(clipf)
check(r.target is None and not os.path.exists(DEAD), "a dead r- socket is dropped and removed")

try:
    r.register(os.path.join(opened, "p.sock"))
    check(False, "register refuses a socket in an open folder")
except speechsock.SocketError:
    check(True, "register refuses a socket in an open folder")
try:
    r.register(clipf)
    check(False, "register refuses a path that is not a socket")
except speechsock.SocketError:
    check(True, "register refuses a path that is not a socket")
r.register(PSOCK) if os.path.exists(PSOCK) else None
r.unregister("/elsewhere.sock")
check(r.target is None, "unregister of another player changes nothing")


# --- HTTP /player
ESOCK = os.path.join(W, "speech.sock")
esrv = sp.bind(ESOCK)
threading.Thread(target=esrv.serve_forever, daemon=True).start()


def post(body, ctype="application/json"):
    return speechsock.request("POST", "/player", body=json.dumps(body).encode(), timeout=2, sock_path=ESOCK,
                              headers={"Content-Type": ctype})[0]


sp.router = None
check(post({"sock": PSOCK}) == 503, "/player 503 before the engine is loaded")
sp.router = sp.RoutedPlayer(FakeLocal())
srv, th = start_player(PSOCK)
check(post({"sock": PSOCK, "on": True}) == 200 and sp.router.target == PSOCK, "/player registers")
check(post({"sock": PSOCK, "on": False}) == 200 and sp.router.target is None, "/player on:false unregisters")
check(post({"sock": os.path.join(opened, "p.sock")}) == 400, "/player refuses an open folder")
check(post({"sock": 5}) == 400, "/player refuses a non-string socket")
check(post({"sock": PSOCK}, "text/plain") == 415, "/player is JSON only")
srv.close(); th.join(2)
esrv.shutdown(); sp.release(esrv, ESOCK)


# --- launcher: yapper --remote HOST forwards both sockets over one SSH connection
def run_remote(args, engine_path, label, ssh_rc=0, env_extra=None, ssh_s="0.5", player_s="30"):
    tmp = tempfile.mkdtemp(prefix="yp", dir="/tmp")
    stubs = os.path.join(tmp, "stubs")
    os.makedirs(stubs)
    rec = os.path.join(tmp, "rec")
    bodies = {
        # the probe login prints the engine's socket path; the main login records its args
        "ssh": f'case "$*" in *ConnectTimeout=5*) printf %s "{engine_path}" ;; *) printf "%s\\n" "$@" > {rec}.ssh; sleep {ssh_s}; exit {ssh_rc} ;; esac',
        "python3": f'echo "$YAPPER_SOCKET $* $$" >> {rec}.py; case "$*" in *player.py*) exec sleep {player_s} ;; esac; exec sleep 30',
        "curl": f'for a; do case "$a" in *sock*on*) echo "$a" >> {rec}.curl ;; esac; done; printf 200',
    }
    for name, body in bodies.items():
        p = os.path.join(stubs, name)
        open(p, "w").write(f"#!/bin/sh\n{body}\n")
        os.chmod(p, 0o755)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": tmp, **(env_extra or {})}
    r = subprocess.run([os.path.join(ROOT, "bin", "yapper"), "--remote", *args], env=env,
                       capture_output=True, text=True, timeout=30)
    get = lambda s: open(rec + s).read().splitlines() if os.path.exists(rec + s) else []
    out = (r, get(".ssh"), get(".py"), tmp)
    run_remote.curl = get(".curl")
    return out


r, sargs, py, tmp = run_remote(["laptop-host", "echo", "hi"], "/Users/u/.cache/yapper/speech.sock", "ok")
cache = os.path.join(tmp, ".cache", "yapper", "remote")
check(r.returncode == 0, f"--remote exits with ssh's status (rc={r.returncode} {r.stderr.strip()[:160]})")
R_ = [a for a in sargs if a.startswith("/Users/u/.cache/yapper/r-")]
L_ = [a for a in sargs if a.startswith(cache + "/e-")]
check(len(R_) == 1 and R_[0].split(":")[1].startswith(cache + "/p-"), f"-R puts the player next to the remote engine {R_}")
check(len(L_) == 1 and L_[0].endswith(":/Users/u/.cache/yapper/speech.sock"), f"-L brings the remote engine here {L_}")
check("ExitOnForwardFailure=yes" in sargs and sargs[-3:] == ["laptop-host", "echo", "hi"], f"forwards must work; host and command last {sargs[-4:]}")
check(any("lib/player.py" in l for l in py) and any(l.startswith(cache + "/e-") and "lib/micwatch.py" in l for l in py),
      f"player runs here, and the mic watcher cuts through the forwarded engine {py}")
check(stat.S_IMODE(os.stat(cache).st_mode) == 0o700, "the local socket folder is private")
check(not any(n.endswith(".sock") for n in os.listdir(cache)), "local sockets cleaned up on exit")


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


kids = [int(l.split()[-1]) for l in py if l.split()[-1].isdigit()]
check(len(kids) == 2 and wait_until(lambda: not any(alive(k) for k in kids)), f"player and mic watcher stop with the connection {kids}")
check("unbound" not in r.stderr, f"clean exit {r.stderr.strip()[:120]}")
check(not any(a.startswith("-") and "8791" in a for a in sargs) and not any(":127.0.0.1:" in a for a in sargs), "no TCP forward")
shutil.rmtree(tmp, ignore_errors=True)

r, sargs, py, tmp = run_remote(["laptop-host"], "/tmp/x; touch /tmp/pwned", "bad")
check(r.returncode != 0 and sargs == [] and "unexpected engine socket" in r.stderr, "an odd engine path from the host is refused")
shutil.rmtree(tmp, ignore_errors=True)
r, sargs, py, tmp = run_remote(["laptop-host"], "/Users/u/.cache/yapper/speech.sock", "lost", ssh_rc=255)
check(r.returncode == 255 and "lost or could not reach laptop-host" in r.stderr, f"a dropped connection exits 255 and says so ({r.returncode})")
shutil.rmtree(tmp, ignore_errors=True)
r, sargs, py, tmp = run_remote(["laptop-host"], "/odd; path", "override", env_extra={"YAPPER_REMOTE_SOCKET": "/Users/u/s/speech.sock"})
check(r.returncode == 0 and any(a.endswith(":/Users/u/s/speech.sock") for a in sargs), "YAPPER_REMOTE_SOCKET names the engine and skips the probe")
shutil.rmtree(tmp, ignore_errors=True)
r, sargs, py, tmp = run_remote(["laptop-host"], "/Users/u/.cache/yapper/speech.sock", "reg", ssh_s="2", player_s="0.5",
                               env_extra={"YAPPER_REMOTE_REGISTER_S": "0.2"})
c = run_remote.curl
check(c[:1] and '"on": true' in c[0] and c[-1:] and '"on": false' in c[-1] and sum('"on": false' in x for x in c) == 1,
      f"registers while the player runs, then unregisters once when it exits {c}")
shutil.rmtree(tmp, ignore_errors=True)
r, sargs, py, tmp = run_remote([], "/x.sock", "nohost")
check(r.returncode != 0 and "usage: yapper --remote HOST" in r.stderr, "--remote needs a host")
shutil.rmtree(tmp, ignore_errors=True)

shutil.rmtree(W, ignore_errors=True)
shutil.rmtree(opened, ignore_errors=True)
sys.exit(0 if ok else 1)
