"""voice-settings.json is the whole gating mechanism, so its shape is worth asserting.

The voice.enabled checks are not cosmetic: unless built-in dictation is explicitly disabled
here, Claude Code's cloud dictation would be on and audio would no longer stay on this machine.
"""
import json, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = json.load(open(os.path.join(ROOT, "voice-settings.json")))

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


hooks = cfg.get("hooks", {})


def commands(event):
    return [h["command"] for group in hooks.get(event, []) for h in group.get("hooks", [])]


def run(cmd, env):
    """Run a hook command the way Claude Code does: through a shell."""
    return subprocess.run(["sh", "-c", cmd], env=env, capture_output=True, text=True, timeout=10)


ups = commands("UserPromptSubmit")
stops = commands("Stop")
TEMPLATE = '[ -z "${YAPPER_ROOT:-}" ] || exec "${YAPPER_ROOT}/bin/%s"'

check(len(ups) == 1, f"exactly one UserPromptSubmit hook {ups}")
check(len(stops) == 1, f"exactly one Stop hook {stops}")
check(ups == [TEMPLATE % "voice-brief"], f"UserPromptSubmit -> voice-brief under YAPPER_ROOT {ups}")
check(stops == [TEMPLATE % "speak-hook"], f"Stop -> speak-hook under YAPPER_ROOT {stops}")
check("HOME" not in json.dumps(hooks), "no hook falls back to a fixed checkout path")

# yapper exports YAPPER_ROOT, so the hooks find this checkout wherever it lives. Without it
# (settings loaded by something other than yapper) the hooks do nothing, quietly.
base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"}
with tempfile.TemporaryDirectory() as tmp:
    for root in (os.path.join(tmp, "plain"), os.path.join(tmp, "a b")):
        os.makedirs(os.path.join(root, "bin"))
        for name in ("voice-brief", "speak-hook"):
            stub = os.path.join(root, "bin", name)
            with open(stub, "w") as f:
                f.write(f'#!/bin/sh\necho "ran {name}"\n')
            os.chmod(stub, 0o755)
    for c, name in [(c, "voice-brief") for c in ups] + [(c, "speak-hook") for c in stops]:
        real = os.path.join(ROOT, "bin", name)
        check(os.path.isfile(real) and os.access(real, os.X_OK), f"executable in this checkout: bin/{name}")
        r = run(c, {**base, "YAPPER_ROOT": os.path.join(tmp, "plain")})
        check(r.returncode == 0 and r.stdout.strip() == f"ran {name}", f"{name}: with YAPPER_ROOT -> runs that checkout's script {r.stdout!r}")
        r = run(c, {**base, "YAPPER_ROOT": os.path.join(tmp, "a b")})
        check(r.returncode == 0 and r.stdout.strip() == f"ran {name}", f"{name}: a checkout path with a space still runs {r.stdout!r}")
        for label, env in (("unset", base), ("empty", {**base, "YAPPER_ROOT": ""})):
            r = run(c, env)
            check(r.returncode == 0 and r.stdout == "" and r.stderr == "",
                  f"{name}: YAPPER_ROOT {label} -> exits 0 silently (rc={r.returncode} {r.stderr.strip()[:60]!r})")

# the audio leak, closed
check(cfg.get("voice", {}).get("enabled") is False, "voice.enabled is false")
check(cfg.get("voiceEnabled") is False, "voiceEnabled (deprecated form) is false")

# UserPromptSubmit blocks the prompt; its budget must stay well under the 30s cap
up_timeouts = [h.get("timeout") for g in hooks.get("UserPromptSubmit", []) for h in g["hooks"]]
check(all(t is not None and t <= 15 for t in up_timeouts), f"UserPromptSubmit timeout <= 15s {up_timeouts}")

sys.exit(0 if ok else 1)
