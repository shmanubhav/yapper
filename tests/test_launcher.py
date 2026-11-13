"""bin/yapper hands Claude this checkout's location and the engine's socket, wherever the repo is cloned.

Runs the real launcher through a symlink with stub tools on PATH: the speech engine reports
healthy, OpenSuperWhisper is present or missing as each case needs, and a fake `claude` records
what it was started with. `open` is always a stub, so no app launches. Nothing real starts and
nothing speaks.
"""
import os, shutil, subprocess, sys, tempfile

# realpath: the launcher resolves its own location with realpath (/tmp vs /private/tmp on macOS)
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


STUBS = {
    # /health -> 200 from an engine whose root is $FAKE_ROOT (default: this checkout); /speak -> $SPEAK_CODE
    # the engine is up unless $CURL_DOWN; every request must go over --unix-socket "$YAPPER_SOCKET"
    "curl": 'case "$*" in *"--unix-socket $YAPPER_SOCKET "*) ;; *) exit 99 ;; esac; [ -n "$CURL_DOWN" ] && exit 7; '
            'case "$*" in *speak*) printf "${SPEAK_CODE:-202}" ;; '
            '*) printf \'{"ok": true, "status": "ready", "root": "%s"}\\n%s\' "${FAKE_ROOT:-$YAPPER_ROOT}" "${HEALTH_CODE:-200}" ;; esac',
    "pgrep": "exit 0",                                  # OpenSuperWhisper running
    "open": "exit 0",
    # stands in for `python3 lib/micwatch.py`: stays alive MIC_ALIVE_S; the real mic watcher never starts
    # (it records itself in the pidfile, as lib/micwatch.py does)
    "python3": 'echo $$ > "$YAPPER_SOCKET.micwatch.pid"; sleep "${MIC_ALIVE_S:-2}"',
    "claude": 'printf "%s\\n" "$YAPPER_ROOT" "$YAPPER_SOCKET" "$@" > "$RECORD"',
}
# OpenSuperWhisper missing: `pgrep -x OpenSuperWhisper` fails and `open` cannot launch the app.
NO_OSW = {"pgrep": '[ "$1" = -x ] && exit 1; exit 0', "open": "exit 1"}


def run(tmp, args, extra_env=None, stubs=None, label="x"):
    stub_dir = os.path.join(tmp, f"stubs-{label}")
    os.makedirs(stub_dir, exist_ok=True)
    for name, body in {**STUBS, **(stubs or {})}.items():
        path = os.path.join(stub_dir, name)
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(path, 0o755)
    link = os.path.join(tmp, "yapper")                 # like ~/.local/bin/yapper
    if not os.path.islink(link):
        os.symlink(os.path.join(ROOT, "bin", "yapper"), link)
    record = os.path.join(tmp, f"record-{label}")
    if os.path.exists(record):
        os.unlink(record)
    env = {"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": tmp, "RECORD": record, **(extra_env or {})}
    r = subprocess.run([link, *args], env=env, capture_output=True, text=True, timeout=30)
    got = open(record).read().splitlines() if os.path.exists(record) else []
    return r, got


def launch(tmp, label, extra_env, sock=None):
    sock = sock or os.path.join(tmp, ".cache", "yapper", "speech.sock")
    r, got = run(tmp, ["--model", "x"], extra_env, label=label)
    check(r.returncode == 0, f"{label}: launcher exits 0 through the stub claude (rc={r.returncode} {r.stderr.strip()[:120]})")
    check(got[:1] == [ROOT], f"{label}: YAPPER_ROOT = this checkout {got[:1]}")
    check(got[1:2] == [sock], f"{label}: YAPPER_SOCKET exported for the hooks = {sock} {got[1:2]}")
    check(got[2:] == ["--settings", os.path.join(ROOT, "voice-settings.json"), "--model", "x"],
          f"{label}: claude gets this checkout's settings, then the caller's args {got[2:]}")
    check(f'"{os.path.join(ROOT, "bin", "shh")}"' in r.stdout, f"{label}: banner names this checkout's shh, quoted")


with tempfile.TemporaryDirectory() as tmp:
    launch(tmp, "unset", {})                                        # catches a missing `export`
    launch(tmp, "stale", {"YAPPER_ROOT": "/stale/checkout"})        # must be overridden
    own = tempfile.mkdtemp(prefix="yp", dir="/tmp")
    launch(tmp, "socket", {"YAPPER_SOCKET": os.path.join(own, "s.sock")}, sock=os.path.join(own, "s.sock"))
    shutil.rmtree(own, ignore_errors=True)

    # The socket's folder must be private, and the path must fit macOS's 104-byte limit.
    cache = os.path.join(tmp, ".cache", "yapper")
    os.chmod(cache, 0o755)
    r, got = run(tmp, ["--model", "x"], label="open-dir")
    check(r.returncode != 0 and not got and "open to other users" in r.stderr and "chmod 700" in r.stderr,
          f"a socket folder open to others -> refuses to start, says how to fix {r.stderr.strip()[-90:]!r}")
    r, _ = run(tmp, ["--status"], label="open-dir-status")
    check(r.returncode == 0 and "refused: socket folder" in r.stdout, "--status reports the refused socket folder")
    os.chmod(cache, 0o700)
    r, got = run(tmp, ["--model", "x"], {"YAPPER_SOCKET": "/tmp/" + "y" * 110 + "/s.sock"}, label="long")
    check(r.returncode != 0 and not got and "macOS allows at most 103" in r.stderr, "a socket path over 103 bytes -> refused clearly")

    # A caller's --settings would replace voice-settings.json (claude keeps only the last one).
    for args in (["--settings", "x.json"], ["--settings=x.json"], ["-p", "hi", "--settings", "x.json"]):
        r, got = run(tmp, args, label="settings")
        check(r.returncode != 0 and not got and "--settings" in r.stderr, f"refuses a caller's {' '.join(args)}")

    # Dictation is optional: OpenSuperWhisper missing -> a warning, and claude still starts.
    r, got = run(tmp, ["--model", "x"], stubs=NO_OSW, label="no-osw")
    check(r.returncode == 0 and got[2:3] == ["--settings"], f"no OpenSuperWhisper -> claude still starts (rc={r.returncode})")
    check("continuing without dictation" in r.stderr and "speech output still works" in r.stderr,
          f"no OpenSuperWhisper -> says so and carries on {r.stderr.strip()[-120:]!r}")
    r, _ = run(tmp, ["--status"], stubs=NO_OSW, label="status")
    check(r.returncode == 0 and "not running (optional" in r.stdout, f"--status reports dictation as optional {r.stdout.strip()[-120:]!r}")
    r, _ = run(tmp, ["--status"], label="status-ok")
    check("OpenSuperWhisper running" in r.stdout, "--status reports dictation running")

    # --sample never runs beside an engine: ours is paused first; one that will not stop -> refuses, names --stop.
    r, _ = run(tmp, ["--sample"], {"YAPPER_ENV": os.path.join(tmp, "noenv")}, label="sample")
    check(r.returncode != 0 and "yapper --stop" in r.stderr,
          f"--sample with an engine that will not stop -> refuses, names --stop {r.stderr.strip()[-100:]!r}")

    # The config reaches the engine (and claude) as exported variables; the user's env still wins.
    cfg = os.path.join(tmp, ".config", "yapper", "config")
    os.makedirs(os.path.dirname(cfg))
    with open(cfg, "w") as f:
        f.write("voice=derry_b\nspeed=1.2\n")
    rec = {"claude": 'printf "%s\\n" "${YAPPER_TTS_VOICE_WAV-unset}" "${YAPPER_TTS_SPEED-unset}" > "$RECORD"'}
    r, got = run(tmp, [], stubs=rec, label="cfg")
    check(r.returncode == 0 and got == [os.path.join(ROOT, "voices", "derry_b.wav"), "1.2"],
          f"config voice and speed are exported to the processes yapper starts {got}")
    r, got = run(tmp, [], {"YAPPER_TTS_SPEED": "0.95"}, stubs=rec, label="cfg-env")
    check(r.returncode == 0 and got[1:] == ["0.95"], f"a speed already in the environment wins over the file {got}")
    os.unlink(cfg)

    # Another engine on the socket (a different checkout's root) is never used.
    r, got = run(tmp, ["--model", "x"], {"FAKE_ROOT": "/some/other/yapper"}, label="foreign")
    check(r.returncode != 0 and not got and "held by another engine (root /some/other/yapper)" in r.stderr
          and "YAPPER_SOCKET" in r.stderr, f"a foreign engine on the socket -> refuses, names its root {r.stderr.strip()[-110:]!r}")
    r, _ = run(tmp, ["--status"], {"FAKE_ROOT": "/some/other/yapper"}, label="foreign-status")
    check("held by another engine" in r.stdout, "--status names a foreign engine")

    # --check passes only when /speak answers 202.
    r, _ = run(tmp, ["--check"], {"SPEAK_CODE": "503"}, label="check-bad")
    check(r.returncode != 0 and "spoke a test phrase" not in r.stdout and "HTTP 503" in r.stderr,
          f"--check with /speak 503 -> fails {r.stderr.strip()[-80:]!r}")
    r, _ = run(tmp, ["--check"], label="check-ok")
    check(r.returncode == 0 and "spoke a test phrase" in r.stdout, "--check with /speak 202 -> passes")

    # Mic watcher: tracked by a pidfile and confirmed alive before "running" is printed.
    mic_pid = os.path.join(cache, "speech.sock.micwatch.pid")
    check(os.path.isfile(mic_pid), "the mic watcher's pid is recorded")
    check(oct(os.stat(cache).st_mode & 0o777) == "0o700" and oct(os.stat(mic_pid).st_mode & 0o777) == "0o600",
          f"umask 077: cache dir 700, pidfile 600 ({oct(os.stat(cache).st_mode & 0o777)})")
    os.unlink(mic_pid)
    r, got = run(tmp, ["--model", "x"], {"MIC_ALIVE_S": "0"}, label="mic-dead")
    check(r.returncode == 0 and got and "mic watcher did not start" in r.stderr and "NOT RUNNING" in r.stdout,
          f"a mic watcher that dies at once -> warned, never reported running {r.stderr.strip()[-80:]!r}")

    # --stop kills only a pid whose command line still runs this checkout's script.
    import time
    def proc(script):
        return subprocess.Popen(["/bin/sh", "-c", "sleep 30; :", script])
    mine, other = proc(os.path.join(ROOT, "lib", "speaker.py")), proc("/some/other/yapper/lib/speaker.py")
    mic_other = proc("/some/other/yapper/lib/micwatch.py")
    time.sleep(0.2)
    spk_pid = os.path.join(cache, "speech.sock.speaker.pid")
    open(spk_pid, "w").write(str(mine.pid))
    open(mic_pid, "w").write(str(mic_other.pid))           # a reused pid: not ours
    r, _ = run(tmp, ["--stop"], {"CURL_DOWN": "1"}, label="stop")
    try:
        mine.wait(3)
    except subprocess.TimeoutExpired:
        pass
    check(r.returncode == 0 and mine.poll() is not None, "--stop kills this checkout's engine from its pidfile")
    check(mic_other.poll() is None, "--stop leaves a pid that runs another checkout's script")
    check(not os.path.exists(spk_pid), "--stop clears the pidfile of the engine it stopped")
    check(os.path.exists(mic_pid) and open(mic_pid).read().strip() == str(mic_other.pid),
          "--stop keeps a pidfile whose live pid is not ours (another clone may own it)")
    dead = subprocess.Popen(["/usr/bin/true"])
    dead.wait()
    open(mic_pid, "w").write(str(dead.pid))
    run(tmp, ["--stop"], {"CURL_DOWN": "1"}, label="stop-stale")
    check(not os.path.exists(mic_pid), "--stop clears a stale pidfile (its pid is gone)")
    r, _ = run(tmp, ["--status"], {"HEALTH_CODE": "503"}, label="status-warming")
    check("starting (loading the model)" in r.stdout, "--status shows our warming engine as starting, not DOWN")
    r, _ = run(tmp, ["--status"], {"HEALTH_CODE": "500"}, label="status-failed")
    check("failed to load" in r.stdout, "--status shows a failed load")
    r, _ = run(tmp, ["--stop"], {"CURL_DOWN": "1"}, label="stop-none")
    check(r.returncode == 0 and other.poll() is None, "no pidfile -> nothing is killed")
    for p in (mine, other, mic_other):
        p.kill()
        p.wait()

sys.exit(0 if ok else 1)
