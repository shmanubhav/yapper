"""The speech environment: bin/tts-python runs yapper's own Python and never uv; `yapper --install`
builds it from requirements.lock; the launcher refuses a missing one and stops early when the server dies.

Stub uv, pkill and curl on PATH, a fake Python, temp HOME and YAPPER_ENV. Nothing real starts,
nothing is stopped, nothing speaks.
"""
import os, shutil, subprocess, sys, tempfile, time

# realpath: the launcher resolves its own location with realpath (/tmp vs /private/tmp on macOS)
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
LOCK = os.path.join(ROOT, "requirements.lock")
PY = "3.12.15"

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


UV = """#!/bin/sh
[ "$1" = --version ] && { echo "uv ${UV_VERSION:-0.13.5} (stub)"; exit 0; }
echo "uv $*" >> "$CALLS"
case "$1 $2" in
  "python install") [ "$UV_FAIL" = python ] && exit 1; exit 0 ;;
  "pip sync") [ "$UV_FAIL" = sync ] && exit 1; exit 0 ;;
  "pip check") [ "$UV_FAIL" = check ] && exit 1; exit 0 ;;
esac
if [ "$1" = venv ]; then
  [ "$UV_FAIL" = venv ] && { echo "error: uv will not clear a directory that is not a virtual environment" >&2; exit 2; }
  for last; do :; done
  mkdir -p "$last/bin" && cp "$FAKE_PY" "$last/bin/python" && exit 0
fi
exit 0
"""

FAKE_PY = """#!/bin/sh
echo "python HF_HUB_OFFLINE=${HF_HUB_OFFLINE-} UV_OFFLINE=${UV_OFFLINE-} $* [bars=${HF_HUB_DISABLE_PROGRESS_BARS-} tqdm=${TQDM_DISABLE-}]" >> "$CALLS"
if [ "$1" = -c ]; then
  case "$2" in *python_version*) echo "${FAKE_VER:-3.12.15}" ;; esac
  exit 0
fi
[ -n "$PY_OUT" ] && echo "$PY_OUT"
exit ${PY_RC:-0}
"""

STUBS = {
    "uv": UV,
    "pkill": '#!/bin/sh\necho "pkill $*" >> "$CALLS"\nexit 0\n',
    # curl stands in for the engine on its socket. Without -w it is the launcher's alive() check:
    # up while $UP_FOR calls remain (counted in $ALIVE_N), "always" keeps it up, or down while
    # $DOWN_FOR calls remain and then up (another server took the socket). With -w it is /health:
    # $HEALTH, or 000 for the first $HEALTH_OK_AFTER calls and 200 after.
    "curl": '#!/bin/sh\ncase "$*" in *"--unix-socket "*) ;; *) exit 99 ;; esac\n'
            'case "$*" in *" -w "*) ;; *)\n'
            '  echo "alive" >> "$CALLS"\n  [ -n "$ALIVE_DELAY" ] && sleep "$ALIVE_DELAY"\n'
            '  [ "$UP_FOR" = always ] && exit 0\n'
            '  n=$(cat "$ALIVE_N" 2>/dev/null || echo 0); echo $((n + 1)) > "$ALIVE_N"\n'
            '  if [ -n "$DOWN_FOR" ]; then [ "$n" -lt "$DOWN_FOR" ] && exit 7; exit 0; fi\n'
            '  [ "$n" -lt "${UP_FOR:-0}" ] && exit 0; exit 7 ;;\nesac\n'
            'if [ -n "$HEALTH_OK_AFTER" ]; then\n'
            '  n=$(cat "$CURL_N" 2>/dev/null || echo 0); echo $((n + 1)) > "$CURL_N"\n'
            '  [ "$n" -ge "$HEALTH_OK_AFTER" ] && { printf \'{"root": "%s"}\\n200\' "$YAPPER_ROOT"; exit 0; }; printf 000; exit 0\nfi\n'
            'printf \'{"root": "%s"}\\n%s\' "$YAPPER_ROOT" "${HEALTH:-200}"\n',
    "pgrep": "#!/bin/sh\nexit 0\n",
    "open": "#!/bin/sh\nexit 0\n",
    "python3": '#!/bin/sh\necho $$ > "$YAPPER_SOCKET.micwatch.pid"\nsleep 2\n',          # stands in for the mic watcher; the real one never starts
    "claude": '#!/bin/sh\necho "claude $*" >> "$CALLS"\nexit 0\n',
}


class Box:
    """A temp HOME with stub tools, a fake Python, and an env folder."""

    def __init__(self, tmp, with_uv=True, bash=None):
        self.tmp, self.calls = tmp, os.path.join(tmp, "calls")
        self.stubs = os.path.join(tmp, "stubs")
        os.makedirs(self.stubs, exist_ok=True)
        for name, body in STUBS.items():
            if name == "uv" and not with_uv:
                continue
            self._write(os.path.join(self.stubs, name), body)
        if bash:
            os.symlink(bash, os.path.join(self.stubs, "bash"))
        self.fake_py = os.path.join(tmp, "fake-python")
        self._write(self.fake_py, FAKE_PY)
        self.env_dir = os.path.join(tmp, "env")
        self.venv = os.path.join(self.env_dir, "venv")
        self.link = os.path.join(tmp, "yapper")
        os.symlink(os.path.join(ROOT, "bin", "yapper"), self.link)

    @staticmethod
    def _write(path, body):
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    def env(self, **extra):
        e = {"PATH": f"{self.stubs}:/usr/bin:/bin", "HOME": self.tmp, "CALLS": self.calls,
             "FAKE_PY": self.fake_py, "ALIVE_N": os.path.join(self.tmp, "alive-n"), "CURL_N": os.path.join(self.tmp, "curl-n"),
             "YAPPER_ENV": self.env_dir}
        e.update({k: v for k, v in extra.items() if v is not None})
        return {k: v for k, v in e.items() if not (k in extra and extra[k] is None)}

    def built(self, stamp=None, venv=None):
        venv = venv or self.venv
        os.makedirs(os.path.join(venv, "bin"), exist_ok=True)
        shutil.copy(self.fake_py, os.path.join(venv, "bin", "python"))
        if stamp is None:
            shutil.copy(LOCK, os.path.join(venv, "requirements.lock"))
        else:
            with open(os.path.join(venv, "requirements.lock"), "w") as f:
                f.write(stamp)

    def stamped(self):
        p = os.path.join(self.venv, "requirements.lock")
        return os.path.exists(p) and open(p).read() == open(LOCK).read()

    def log(self):
        return open(self.calls).read().splitlines() if os.path.exists(self.calls) else []

    def run(self, args, **extra):
        if os.path.exists(self.calls):
            os.unlink(self.calls)
        t = time.monotonic()
        try:
            r = subprocess.run(args, env=self.env(**extra), capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired as e:
            r = subprocess.CompletedProcess(args, None, e.stdout or "", e.stderr or "")
            r.stdout = r.stdout.decode() if isinstance(r.stdout, bytes) else r.stdout
            r.stderr = r.stderr.decode() if isinstance(r.stderr, bytes) else r.stderr
        return r, time.monotonic() - t


def has(lines, *parts):
    return any(all(p in line for p in parts) for line in lines)


def ran(lines, *parts):
    """The fake Python itself ran with these parts (a uv command line that mentions python does not count)."""
    return any(line.startswith("python ") and all(p in line for p in parts) for line in lines)


check(os.path.exists(LOCK), "requirements.lock is in the repo")
pins = [l for l in open(LOCK).read().splitlines() if "==" in l and not l.startswith(("#", " "))] if os.path.exists(LOCK) else []
check(len(pins) == 37 and any(p.startswith("mlx-audio==0.5.8") for p in pins)
      and any(p.startswith("soundfile==0.14.0") for p in pins) and any(p.startswith("certifi==") for p in pins),
      f"the lock pins the 37 packages that run today, certifi included ({len(pins)})")
check(os.path.exists(LOCK) and all("--hash=sha256:" in open(LOCK).read().split(p, 1)[1].split("\n", 2)[1] for p in pins),
      "every pin carries a hash")

# 1. bin/tts-python: runs the stamped environment's Python, never uv.
TTS_PY = os.path.join(ROOT, "bin", "tts-python")
with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp)
    r, _ = b.run([TTS_PY, "-c", "pass"])
    check(r.returncode != 0 and "yapper --install" in r.stderr, f"no environment -> refuses and names --install ({r.returncode} {r.stderr.strip()[:90]})")
    check(not has(b.log(), "uv "), "no environment -> uv is never called")
    b.built(stamp="a different lock\n")
    r, _ = b.run([TTS_PY, "x.py", "--flag"])
    check(r.returncode != 0 and not ran(b.log()), "a stamp from another lock -> refuses, Python never runs")
    b.built()
    r, _ = b.run([TTS_PY, "x.py", "--flag"], PY_RC="7")
    check(r.returncode == 7 and ran(b.log(), "", "x.py --flag"), f"matching stamp -> execs the env's Python with the args (rc={r.returncode})")
    check(not has(b.log(), "uv "), "matching stamp -> uv is never called")
    b.built(venv=os.path.join(tmp, ".local", "share", "yapper", "venv"))
    r, _ = b.run([TTS_PY, "y.py"], YAPPER_ENV=None)
    check(r.returncode == 0 and ran(b.log(), "", "y.py"), "YAPPER_ENV unset -> ~/.local/share/yapper")
    r, _ = b.run([os.path.join(ROOT, "bin", "tts-server")])
    check(ran(b.log(), "HF_HUB_OFFLINE=1 UV_OFFLINE= ", "speaker.py"), f"tts-server: Hugging Face offline, no uv setting {b.log()[-1:]}")
    check(ran(b.log(), "[bars=1 tqdm=1]"), f"tts-python hides progress bars by default {b.log()[-1:]}")
    r, _ = b.run([TTS_PY, "q.py"], TQDM_DISABLE="0", HF_HUB_DISABLE_PROGRESS_BARS="0")
    check(ran(b.log(), "q.py", "[bars=0 tqdm=0]"), "... and the user can turn them back on")

# 2. yapper --install builds the environment from the lock, after stopping the server.
with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp)
    engine = subprocess.Popen(["/bin/sh", "-c", "sleep 30; :", os.path.join(ROOT, "lib", "speaker.py")])
    os.makedirs(os.path.join(tmp, ".cache", "yapper"), mode=0o700, exist_ok=True)
    with open(os.path.join(tmp, ".cache", "yapper", "speech.sock.speaker.pid"), "w") as f:
        f.write(str(engine.pid))
    r, _ = b.run([b.link, "--install"])
    log = b.log()
    try:
        engine.wait(3)
    except subprocess.TimeoutExpired:
        engine.kill(); engine.wait()
    check(r.returncode == 0, f"fresh install exits 0 ({r.returncode} {r.stderr.strip()[-120:]})")
    check(engine.returncode is not None and engine.returncode != 0 and not has(log, "pkill"),
          f"the running server is stopped through its pidfile, never pkill (rc={engine.returncode})")
    check(has(log, f"uv python install {PY}", "--no-bin", f"--install-dir {b.env_dir}/python"), f"its own Python {PY}, no shims on PATH")
    check(has(log, "uv venv", "--managed-python", "--no-python-downloads", f"--python {PY}", b.venv), "a venv on that Python")
    check(has(log, "uv pip sync", f"--python {b.venv}/bin/python", "--require-hashes", "--link-mode clone", LOCK),
          "synced to the lock, hashes required, files copied (never linked to uv's cache)")
    check(has(log, "uv pip check", f"--python {b.venv}/bin/python"), "the result is checked")
    check(b.stamped(), "stamped with the lock")
    check(ran(log, "HF_HUB_OFFLINE=0 ", "lib/speaker.py --install"), "then the model fetch runs on the new Python, online")
    first = log
    r, _ = b.run([b.link, "--install"])
    check(r.returncode == 0 and not has(b.log(), "uv venv"), "rerun with a working venv on the pinned Python -> venv kept, no error")
    r, _ = b.run([b.link, "--install"], FAKE_VER="3.12.14")
    check(r.returncode == 0 and has(b.log(), "uv venv", "--clear"), "venv on another Python -> rebuilt with --clear")
    for step in ("python", "sync", "check"):
        b.built()
        r, _ = b.run([b.link, "--install"], UV_FAIL=step)
        check(r.returncode != 0 and not b.stamped() and not has(b.log(), "lib/speaker.py --install"),
              f"{step} step fails -> exits non-zero, stamp removed, no model fetch")
        if step != "check":
            check("UV_SYSTEM_CERTS=1" in r.stderr and "HTTPS_PROXY" in r.stderr and "UV_PYTHON_INSTALL_MIRROR" in r.stderr
                  and "brew upgrade uv" in r.stderr, f"{step} download fails -> names the proxy settings and a uv upgrade")
    b.built()
    r, _ = b.run([b.link, "--install"], UV_FAIL="venv", FAKE_VER="3.12.14")
    check(r.returncode != 0 and not b.stamped() and "move it aside" in r.stderr,
          f"venv cannot be rebuilt -> says to move the folder aside, no stamp ({r.stderr.strip()[-90:]!r})")
    # An old uv cannot download the pinned Python: refuse before touching anything, name the upgrade.
    for old_uv in ("0.8.0", "0.12.3", "0.12.21", "garbage"):
        b.built()
        r, _ = b.run([b.link, "--install"], UV_VERSION=old_uv)
        check(r.returncode != 0 and "0.12.22 or newer" in r.stderr and old_uv in r.stderr
              and "brew upgrade uv" in r.stderr and b.stamped() and not has(b.log(), "uv python install"),
              f"uv {old_uv} -> refused up front, names the minimum and the upgrade, touches nothing")
    for ok_uv in ("0.12.22", "1.0.0"):
        r, _ = b.run([b.link, "--install"], UV_VERSION=ok_uv)
        check(r.returncode == 0, f"uv {ok_uv} -> accepted")

with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp)
    home_venv = os.path.join(tmp, ".local", "share", "yapper", "venv")
    r, _ = b.run([b.link, "--install"], YAPPER_ENV=None)
    check(r.returncode == 0 and has(b.log(), "uv venv", home_venv) and os.path.exists(os.path.join(home_venv, "requirements.lock")),
          "--install with YAPPER_ENV unset -> builds ~/.local/share/yapper")
    r, _ = b.run([TTS_PY, "z.py"], YAPPER_ENV=None)
    check(r.returncode == 0 and ran(b.log(), "z.py"), "... and tts-python runs that environment")

with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp, with_uv=False)
    r, _ = b.run([b.link, "--install"])
    check(r.returncode != 0 and "brew install uv" in r.stderr and not has(b.log(), "pkill"),
          "no uv -> says how to get it, and stops nothing")
    check(os.path.realpath(os.path.join(tmp, ".local", "bin", "yapper")) == os.path.join(ROOT, "bin", "yapper"),
          "the command is linked before anything slow, even when uv is missing")

# 2b. --install links ~/.local/bin/yapper to this checkout, and never replaces anything else.
TARGET = os.path.join(ROOT, "bin", "yapper")
with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp)
    link = os.path.join(tmp, ".local", "bin", "yapper")
    r, _ = b.run([b.link, "--install"], SHELL="/usr/local/bin/fish")   # bash fills in SHELL when unset
    check(os.path.islink(link) and os.readlink(link) == TARGET and "linked" in r.stdout,
          f"fresh HOME -> creates ~/.local/bin and links yapper to this checkout ({os.readlink(link) if os.path.islink(link) else 'none'})")
    check("not on your PATH" in r.stdout and "export PATH=" in r.stdout and ">>" not in r.stdout
          and "open a new terminal" in r.stdout, "PATH lacks it, unknown shell -> prints the export line only")
    r, _ = b.run([b.link, "--install"])
    check("already points at this checkout" in r.stdout, "rerun -> says the link is already this checkout")
    for shell, rc in (("/bin/zsh", "~/.zshrc"), ("/opt/homebrew/bin/bash", "~/.bash_profile")):
        r, _ = b.run([b.link, "--install"], SHELL=shell)
        check(f">> {rc}" in r.stdout and "open a new terminal" in r.stdout, f"{shell} -> the line names {rc}")
    check(not os.path.exists(os.path.join(tmp, ".zshrc")) and not os.path.exists(os.path.join(tmp, ".bash_profile")),
          "no startup file is ever written")
    r, _ = b.run([b.link, "--install"], PATH=f"{b.stubs}:{os.path.join(tmp, '.local', 'bin')}:/usr/bin:/bin")
    check("not on your PATH" not in r.stdout, "~/.local/bin already on PATH -> no PATH advice")
    os.unlink(link)
    os.symlink("/some/other/yapper/bin/yapper", link)
    r, _ = b.run([b.link, "--install"])
    check(os.readlink(link) == "/some/other/yapper/bin/yapper" and "/some/other/yapper/bin/yapper" in r.stderr
          and f'ln -sfn "{TARGET}" "{link}"' in r.stdout, "a link to another checkout -> left alone, the replace command printed")
    os.unlink(link)
    with open(link, "w") as f:
        f.write("mine\n")
    r, _ = b.run([b.link, "--install"])
    check(open(link).read() == "mine\n" and "regular file" in r.stderr and "ln -sfn" in r.stdout,
          "a regular file -> left alone, the replace command printed")

# 3. Starting: a missing environment refuses at once; a server that dies stops the wait at once.
with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp)
    r, d = b.run([b.link], HEALTH="000")
    check(r.returncode != 0 and "yapper --install" in r.stderr and d < 5, f"no engine, no environment -> refuses at once ({d:.1f}s)")
    check(not os.path.exists(os.path.join(tmp, ".cache", "yapper", "tts.log")) and not has(b.log(), "claude "),
          "no engine, no environment -> no server started, no claude")
    r, _ = b.run([b.link, "--model", "x"], UP_FOR="always")
    check(r.returncode == 0 and has(b.log(), "claude ") and "--install" in r.stdout + r.stderr,
          "server already up, environment out of date -> warns, still starts claude")

bashes = [None] + [p for p in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash") if os.path.exists(p)][:1]
for bash in bashes:
    with tempfile.TemporaryDirectory() as tmp:
        b = Box(tmp, bash=bash)
        b.built()
        logdir = os.path.join(tmp, ".cache", "yapper")
        os.makedirs(logdir, mode=0o700)            # yapper refuses a socket folder others can read
        with open(os.path.join(logdir, "tts.log"), "w") as f:
            f.write("old-run-line from yesterday\n")
        r, d = b.run([b.link], HEALTH="000", PY_OUT="boom-this-run", PY_RC="1")
        which = bash or "/bin/bash"
        check(r.returncode != 0 and d < 5, f"{which}: server dies while starting -> stops at once ({d:.1f}s, rc={r.returncode})")
        check("boom-this-run" in r.stderr and "old-run-line" not in r.stderr,
              f"{which}: shows this run's log lines only {r.stderr.strip()[-160:]!r}")

with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp)
    b.built()
    r, d = b.run([b.link, "--model", "x"], PY_RC="1", DOWN_FOR="1", HEALTH_OK_AFTER="3")
    check(r.returncode == 0 and has(b.log(), "claude "), f"our server dies but another answers on the socket -> waits for it, starts claude ({d:.1f}s, rc={r.returncode})")

# 4. --stop waits for the engine to go, so `--stop && --check` starts a fresh server.
with tempfile.TemporaryDirectory() as tmp:
    b = Box(tmp)
    r, _ = b.run([b.link, "--stop"], UP_FOR="3")
    check(r.returncode == 0 and b.log().count("alive") >= 4, f"--stop waits until the engine is gone (alive x{b.log().count('alive')})")
    check(not has(b.log(), "pkill"), "--stop never uses pkill")
    if os.path.exists(os.path.join(tmp, "alive-n")):
        os.unlink(os.path.join(tmp, "alive-n"))
    r, d = b.run([b.link, "--stop"], UP_FOR="always", ALIVE_DELAY="0.1")   # each real check costs time too
    check(r.returncode != 0 and d < 7 and "still" in r.stderr, f"--stop gives up after about 5 s and says an engine still answers ({d:.1f}s, rc={r.returncode})")

sys.exit(0 if ok else 1)
