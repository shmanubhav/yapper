"""yapper --setup (lib/wizard.py): an interactive wizard for people setting up without an AI agent.

  1. voice: ask woman/man/either and the accent (tags from voices/README.md), play the matches,
     pick one, or take your own .wav
  2. speed: play the chosen voice at 0.95, 1.07 and 1.2 and pick one
  3. config: write ~/.config/yapper/config (or YAPPER_CONFIG), backing up the old one
  4. dictation: open OpenSuperWhisper and walk through its permissions and hotkey

bin/yapper pauses the speech engine first (like --sample), runs this on the speech environment's
Python, then starts the engine in the chosen voice and speaks a test phrase. The model is loaded
at most once, and only when something is played.

Everything above the Player class is stdlib-only and Python 3.9 compatible, so the tests can
import it with any python3. Nothing at import time loads a model or plays sound.
"""
import os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
VOICES = os.path.join(ROOT, "voices")
SPEEDS = (0.95, 1.07, 1.2)
DEFAULT_SPEED = 1.07
SENTENCE = "Here is a short sample of this voice. The build finished, and two tests need a second look."
# what bin/yapper's config parser accepts for a voice value; anything else could not be read back
CONFIG_VOICE = re.compile(r"^[A-Za-z0-9._/~ +-]+$")
OSW_APPS = ("/Applications/OpenSuperWhisper.app", os.path.expanduser("~/Applications/OpenSuperWhisper.app"))
_BULLET_START = re.compile(r"^- `([^`]+)`")
# - `name.wav` (gender, accent): style text. Anything after the first sentence is provenance.
_BULLET = re.compile(r"^- `([^`/]+\.wav)` \(([a-z]+), ([A-Za-z][A-Za-z -]*)\):\s*(.+)$")
GENDERS = ("woman", "man")


# --- pure parts (tested) -----------------------------------------------------------------------

def _bullets(text):
    """The bullets of the '## Voices' section, continuation lines joined."""
    out, in_list = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            in_list = line.strip() == "## Voices"
            continue
        if not in_list:
            continue
        if line.startswith("- "):
            out.append(line.strip())
        elif out and line.startswith("  ") and line.strip():
            out[-1] = f"{out[-1]} {line.strip()}"
    return out


def parse_voices(text):
    """([{name, gender, accent, style}], [warnings]) from voices/README.md's '## Voices' bullets.
    Format: - `name.wav` (woman|man, Accent): style text. Provenance..."""
    voices, warnings = [], []
    for b in _bullets(text):
        m = _BULLET.match(b)
        if not m or m.group(2) not in GENDERS:
            start = _BULLET_START.match(b)
            if start:
                warnings.append(f"voices/README.md: ignoring a voice line not in the form "
                                f"- `name.wav` (woman|man, Accent): style... -> {b[:70]}")
            continue
        desc = m.group(4).strip()
        style = re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0].rstrip(".")
        voices.append({"name": m.group(1), "gender": m.group(2), "accent": m.group(3).strip(), "style": style})
    return voices, warnings


def list_voices(voices=VOICES):
    """([{name, path, gender, accent, style}], warnings) for every .wav in voices/, sorted by name.
    A clip with no bullet is still listed, with unknown tags (it only shows with 'either'/'any')."""
    try:
        text = open(os.path.join(voices, "README.md"), encoding="utf-8").read()
    except OSError:
        text = ""
    tagged, warnings = parse_voices(text)
    by_name = {v["name"]: v for v in tagged}
    try:
        names = sorted(n for n in os.listdir(voices) if n.endswith(".wav") and not n.startswith("."))
    except OSError:
        names = []
    out = []
    for n in names:
        v = dict(by_name.get(n) or {"name": n, "gender": None, "accent": None,
                                     "style": "(no description in voices/README.md)"})
        v["path"] = os.path.join(voices, n)
        out.append(v)
    return out, warnings


def accent_menu(voices):
    """The accents present, sorted, with 'any' always last."""
    return sorted({v["accent"] for v in voices if v.get("accent")}, key=str.lower) + ["any"]


def filter_voices(voices, gender=None, accent=None):
    """Voices matching gender ('woman'/'man', None = either) and accent (None or 'any' = any)."""
    return [v for v in voices
            if (gender is None or v.get("gender") == gender)
            and (accent in (None, "any") or (v.get("accent") or "").lower() == accent.lower())]


def matches_or_all(voices, gender=None, accent=None):
    """(voices to show, fell_back): the matches, or every voice when nothing matches."""
    found = filter_voices(voices, gender, accent)
    return (found, False) if found else (list(voices), True)


def pick_from_menu(answer, options, default):
    """A 1-based number from `options`, Enter for `default`, else None (ask again)."""
    a = answer.strip()
    if not a:
        return default
    if a.isascii() and a.isdigit() and 1 <= int(a) <= len(options):
        return options[int(a) - 1]
    return None


def _tags(v):
    return ", ".join(t for t in (v.get("gender"), v.get("accent")) if t)


def describe_matches(shown):
    """The lines shown before playing: one sentence for a single match, else a numbered list."""
    if len(shown) == 1:
        v = shown[0]
        return [f"  1 voice matches: {v['style']}" + (f" ({_tags(v)})" if _tags(v) else "") + ". Playing it..."]
    return [f"  {i}. {v['style']}" + (f" ({_tags(v)})" if _tags(v) else "") for i, v in enumerate(shown, 1)]


def voice_prompt(n):
    if n == 1:
        return "Use this voice? Enter = yes, r = replay, a = all voices, b = back, or a path to your own .wav: "
    return f"Pick 1-{n} (Enter = 1), r = replay, a = all voices, b = back, or a path to your own .wav: "


def voice_answer(answer, n):
    """Classify an answer to the voice prompt:
    ("pick", index) | ("replay"|"all"|"back", None) | ("path", text) | ("error", message).
    Enter picks the first (or only) voice; any all-digit answer is a menu choice; only text with
    a '/' or ending in .wav is a path."""
    a = answer.strip()
    low = a.lower()
    if not a or (n == 1 and low in ("y", "yes")):
        return "pick", 0
    if low in ("r", "a", "b"):
        return {"r": "replay", "a": "all", "b": "back"}[low], None
    if a.isascii() and a.isdigit():
        if 1 <= int(a) <= n:
            return "pick", int(a) - 1
        return "error", f"pick a number from 1 to {n}" if n > 1 else "only one voice here: Enter = yes, a = all voices"
    if "/" in a or a.strip("'\"").lower().endswith(".wav"):
        return "path", a
    return "error", "not understood: " + voice_prompt(n).rstrip(": ")


def check_own_wav(raw):
    """(absolute path, None) for a usable .wav the user named, else (None, reason)."""
    path = os.path.abspath(os.path.expanduser(raw.strip().strip("'\"")))
    if not path.endswith(".wav"):
        if path.lower().endswith(".wav"):
            return None, f"the name must end in lowercase .wav (rename it): {path}"
        return None, f"not a .wav file: {path}"
    if not os.path.isfile(path):
        return None, f"no such file: {path}"
    if not os.access(path, os.R_OK):
        return None, f"cannot read {path}"
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError as e:
        return None, f"cannot read {path}: {e.strerror}"
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return None, f"not a WAV file inside (no RIFF/WAVE header): {path}"
    if not CONFIG_VOICE.match(path):
        return None, ("the path has characters the config file cannot hold (only letters, digits, space "
                      f"and . _ - + / ~); rename or move it: {path}")
    return path, None


def voice_value(path, voices=VOICES):
    """What goes after voice=: the bare name for a clip in voices/, else the full path."""
    if os.path.dirname(os.path.realpath(path)) == os.path.realpath(voices):
        return os.path.splitext(os.path.basename(path))[0]
    return path


def resolve_voice(value, voices=VOICES):
    """The clip a voice= value names, exactly as bin/yapper's resolve_voice() reads it, or None."""
    if value.startswith("~/"):
        v = os.path.join(os.path.expanduser("~"), value[2:])
    elif "/" in value:
        v = value
    else:
        v = os.path.join(voices, value if value.endswith(".wav") else value + ".wav")
    return v if v.endswith(".wav") and os.path.isfile(v) else None


def config_voice_problem(value, voices=VOICES):
    """None if bin/yapper will accept voice=<value>; else why it would refuse it."""
    if not CONFIG_VOICE.match(value):
        return (f"'{value}' has characters the config file cannot hold (only letters, digits, space "
                "and . _ - + / ~); rename the file")
    if resolve_voice(value, voices) is None:
        return f"'{value}' does not name a .wav file yapper can find"
    return None


def config_text(voice, speed):
    return f"voice={voice}\nspeed={speed:g}\n"


def read_speed(config_path):
    """The speed in an existing config, if it holds a valid one; else the default."""
    try:
        for line in open(config_path, encoding="utf-8"):
            m = re.match(r"^\s*speed\s*=\s*([0-9.]+)\s*$", line)
            if m and 0.5 <= float(m.group(1)) <= 2.0:
                return float(m.group(1))
    except (OSError, ValueError):
        pass
    return DEFAULT_SPEED


def _replace_with(path, data):
    """Write `data` to a fresh mode-600 temp file beside `path`, then rename it over `path`.
    The rename replaces whatever is at `path` (a planted symlink included) without following it."""
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".yapper-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_config(config_path, text):
    """Write the config (mode 600, folder 700 when new). A symlinked config (dotfiles) is written
    through to its real file. The FIRST old config is kept as <name>.bak; a later run never
    overwrites that backup. Returns (backup path or None, True if the backup was made now)."""
    if os.path.islink(config_path):
        config_path = os.path.realpath(config_path)
    folder = os.path.dirname(config_path) or "."
    os.makedirs(folder, mode=0o700, exist_ok=True)
    backup, made = None, False
    if os.path.isfile(config_path):
        backup = config_path + ".bak"
        if not os.path.lexists(backup):
            with open(config_path, "rb") as f:
                _replace_with(backup, f.read())
            made = True
    _replace_with(config_path, text.encode())
    return backup, made


def pick_speed(answer):
    """1-3 picks a speed; Enter keeps the default; anything else is None (ask again)."""
    a = answer.strip()
    if not a:
        return DEFAULT_SPEED
    if a in ("1", "2", "3"):
        return SPEEDS[int(a) - 1]
    return None


def dictation_step(ask, say, opener=None, apps=OSW_APPS):
    """Walk through OpenSuperWhisper. Returns "ok", "skipped" or "missing-skipped".
    `ask` reads one line from the user; `say` prints; `opener` launches the app."""
    app = next((a for a in apps if os.path.isdir(a)), None)
    if app is None:
        say("OpenSuperWhisper is not installed. It is optional: speech output works without it.")
        say("To talk instead of type, install it with:  brew install --cask opensuperwhisper")
        say("then run yapper --setup again.")
        ask("Press Enter to skip dictation for now: ")
        return "missing-skipped"
    (opener or (lambda p: subprocess.run(["open", "-a", p], check=False)))(app)
    say("Opened OpenSuperWhisper. Now, in that app:")
    say("  1. When macOS asks, allow Microphone and Accessibility (System Settings, Privacy & Security).")
    say("  2. In its settings, pick a dictation hotkey (not Control if you use tmux).")
    say("  3. Click back into this terminal, press your hotkey and say 'hello yapper'.")
    while True:
        line = ask("Your words should appear here (or type s to skip): ")
        if line.strip().lower() == "s":
            return "skipped"
        if line.strip():
            say("Dictation works.")
            return "ok"
        say("Nothing arrived. Check the hotkey and the permissions, then try again.")


# --- the wizard (needs the model and speakers; never run by tests) ----------------------------

class Player:
    """Loads the model on first use, once for the whole wizard. Each clip is rendered once per
    voice (and each announcement once), then replayed from the cache: one 'play' or 'r' plays
    each voice exactly once. Model output is hidden (lib/quiet.py)."""

    def __init__(self):
        self.engine, self.voice, self.cache = None, None, {}

    def _render(self, clip, text):
        from quiet import quiet
        key = (clip, text)
        if key not in self.cache:
            with quiet():
                if self.engine is None:
                    from speaker import ChatterboxEngine
                    self.engine = ChatterboxEngine()
                    self.engine.load_model()
                if self.voice != clip:
                    self.engine.set_voice(clip)
                    self.voice = clip
                self.cache[key] = self.engine.synth(text)
        return self.cache[key]

    def play(self, clip, speed, announce=None):
        from speaker import AfPlayer
        if self.engine is None:
            print("  loading the voice model once (about 30 s)...", flush=True)
        files = ([self._render(clip, announce)] if announce else []) + [self._render(clip, SENTENCE)]
        players = [AfPlayer(1.0)] * bool(announce) + [AfPlayer(speed)]
        try:
            for p, f in zip(players, files):
                p.start(f).wait()
        finally:
            for p in players:
                p.kill()

    def close(self):
        for f in self.cache.values():
            try:
                os.unlink(f)
            except OSError:
                pass
        self.cache.clear()


def ask_menu(question, options, default):
    labels = "  ".join(f"{i}) {o}" for i, o in enumerate(options, 1))
    while True:
        got = pick_from_menu(input(f"{question} {labels}: "), options, default)
        if got is not None:
            return got


def show_and_play(player, shown, speed):
    print()
    for line in describe_matches(shown):
        print(line)
    for i, v in enumerate(shown, 1):
        player.play(v["path"], speed, announce=f"voice {i}" if len(shown) > 1 else None)


def choose_voice(player, speed):
    voices, warnings = list_voices()
    for w in warnings:
        print(f"  warning: {w}")
    if not voices:
        raise SystemExit("yapper: no .wav clips in voices/")
    while True:                                   # b (back) returns here
        gender = ask_menu("\nWhat kind of voice?", ["woman", "man", "either"], "either")
        accent = ask_menu("Accent?", accent_menu(voices), "any")
        shown, fell_back = matches_or_all(voices, None if gender == "either" else gender, accent)
        if fell_back:
            print("  No voice matches that yet; here are all of them.")
        show_and_play(player, shown, speed)
        while True:
            kind, val = voice_answer(input(voice_prompt(len(shown))), len(shown))
            if kind == "replay":
                show_and_play(player, shown, speed)
            elif kind == "all":
                shown = list(voices)
                show_and_play(player, shown, speed)
            elif kind == "back":
                break
            elif kind == "pick":
                why = config_voice_problem(voice_value(shown[val]["path"]))
                if why:
                    print(f"  {why}")
                    continue
                return shown[val]["path"]
            elif kind == "error":
                print(f"  {val}")
            else:
                path, why = check_own_wav(val)
                if why:
                    print(f"  {why}")
                    continue
                print("  Only use a voice you have the speaker's consent to clone.")
                player.play(path, speed)
                if input("Use this voice? [Y/n] ").strip().lower() != "n":
                    return path


def choose_speed(player, clip):
    while True:
        print("\nSpeeds: 1. 0.95   2. 1.07 (default)   3. 1.2")
        if input("Play all three? [Y/n] ").strip().lower() != "n":
            for s in SPEEDS:
                player.play(clip, s, announce=f"speed {s:g}")
        speed = pick_speed(input("Pick 1-3 (Enter for 1.07): "))
        if speed is not None:
            return speed


def main():
    if not sys.stdin.isatty():
        print("yapper --setup is interactive: run it in your own terminal.", file=sys.stderr)
        return 2
    config = os.environ.get("YAPPER_CONFIG") or os.path.expanduser("~/.config/yapper/config")
    print("yapper setup: pick a voice and a speed, write your config, and set up dictation.")
    for var in ("YAPPER_TTS_VOICE_WAV", "YAPPER_TTS_SPEED"):
        if os.environ.get(var):
            print(f"  note: {var} is set in your environment and wins over the config file.")
    player = Player()
    try:
        clip = choose_voice(player, read_speed(config))
        speed = choose_speed(player, clip)
        value = voice_value(clip)
        why = config_voice_problem(value)
        if why:
            print(f"yapper: {why}; nothing was written.", file=sys.stderr)
            return 1
        text = config_text(value, speed)
        print(f"\nWriting {config}:")
        for line in text.splitlines():
            print(f"  {line}")
        backup, made = write_config(config, text)
        if backup and made:
            print(f"  (the old file is kept as {backup})")
        elif backup:
            print(f"  ({backup} already holds your first config; it was left as is)")
        print("\nDictation (optional):")
        dictation_step(input, print)
    except (KeyboardInterrupt, EOFError):
        print("\nyapper: setup stopped; nothing more was changed.", file=sys.stderr)
        return 130
    except Exception as e:                      # noqa: BLE001 - one plain line, not a traceback
        print(f"yapper: setup failed: {e}", file=sys.stderr)
        return 1
    finally:
        player.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
