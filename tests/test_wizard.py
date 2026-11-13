"""yapper --setup (lib/wizard.py): the pure parts only. No model, no audio, no real app launched.

Covers the non-terminal refusal, the voice list from voices/README.md, own-.wav checks, config
writing (backup, mode 600, only the voice and speed lines, readable by bin/yapper), the speed
pick, and the dictation step with a fake missing (and a fake present) OpenSuperWhisper.
"""
import os, shutil, stat, subprocess, sys, tempfile
sys.dont_write_bytecode = True     # keep the clone free of __pycache__

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import wizard

ok = True


def check(c, label):
    global ok
    print(f"  {'PASS' if c else 'FAIL'}  {label}")
    ok = ok and c


check("speaker" not in sys.modules and "mlx_audio" not in sys.modules, "importing the wizard loads no model code")
TMP = tempfile.mkdtemp()

# --- non-terminal refusal: nothing asked, nothing written
home = os.path.join(TMP, "home")
os.makedirs(home, mode=0o700)
r = subprocess.run([os.path.join(ROOT, "bin", "yapper"), "--setup"], stdin=subprocess.DEVNULL, capture_output=True,
                   text=True, timeout=20, env={"PATH": "/usr/bin:/bin", "HOME": home})
check(r.returncode == 2 and "--sample" in r.stderr and "interactive" in r.stderr, f"no terminal -> exit 2, points at --sample ({r.returncode})")
check(not os.path.exists(os.path.join(home, ".config", "yapper", "config")), "no terminal -> no config written")
r = subprocess.run([sys.executable, "-I", "-B", os.path.join(ROOT, "lib", "wizard.py")], stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=20)
check(r.returncode == 2 and "interactive" in r.stderr, "lib/wizard.py on its own also refuses without a terminal")

# --- voice tags: - `name.wav` (woman|man, Accent): style. provenance
voices, warns = wizard.parse_voices("""# Voices
- `stray.wav` (man, Welsh): outside the Voices section, ignored
## Voices
Intro text.
- `a.wav` (woman, Irish): dry and deadpan, brisk. Synthetic, made from a text description.
- `b.wav` (man, British): warm and slow. Synthetic,
  continued on the next line.
- `c.wav` (woman, British): bright, quick
- `bad.wav`: no tags at all
- `odd.wav` (robot, Irish): unknown gender
- not a voice bullet
## Bring your own
- `a.wav` (man, Welsh): another section, ignored
""")
check([(v["name"], v["gender"], v["accent"], v["style"]) for v in voices] ==
      [("a.wav", "woman", "Irish", "dry and deadpan, brisk"), ("b.wav", "man", "British", "warm and slow"),
       ("c.wav", "woman", "British", "bright, quick")],
      f"name, gender, accent and the first-sentence style are parsed, only from ## Voices {voices}")
check(len(warns) == 2 and "bad.wav" in warns[0] and "odd.wav" in warns[1] and all("ignoring" in w for w in warns),
      f"malformed voice bullets are ignored with a warning {warns}")
check(wizard.accent_menu(voices) == ["British", "Irish", "any"], "the accent menu comes from the tags, 'any' last")
check(wizard.accent_menu(voices + [{"name": "d.wav", "gender": "man", "accent": "Australian", "style": "x"}])
      == ["Australian", "British", "Irish", "any"], "a new accent tag shows up with no code change")
names = lambda vs: [v["name"] for v in vs]
check(names(wizard.filter_voices(voices, "woman", "British")) == ["c.wav"], "woman x British")
check(names(wizard.filter_voices(voices, "man", "any")) == ["b.wav"], "man x any")
check(names(wizard.filter_voices(voices, None, "Irish")) == ["a.wav"], "either x Irish")
check(names(wizard.filter_voices(voices, None, "any")) == ["a.wav", "b.wav", "c.wav"], "either x any -> all")
check(names(wizard.filter_voices(voices, "woman", "british")) == ["c.wav"], "accent match ignores case")
shown, fell_back = wizard.matches_or_all(voices, "man", "Irish")
check(fell_back and names(shown) == ["a.wav", "b.wav", "c.wav"], "no match -> falls back to every voice")
shown, fell_back = wizard.matches_or_all(voices, "woman", "Irish")
check(not fell_back and names(shown) == ["a.wav"], "a match -> only the matches")
check([wizard.pick_from_menu(x, ["woman", "man", "either"], "either") for x in ("1", "2", "3", "", "4", "w")]
      == ["woman", "man", "either", "either", None, None], "menu picks: number, Enter default, else ask again")

# The shipped voices/: every .wav has exactly one well-formed bullet, every bullet has a .wav.
VDIR = os.path.join(ROOT, "voices")
readme = open(os.path.join(VDIR, "README.md"), encoding="utf-8").read()
wavs = sorted(n for n in os.listdir(VDIR) if n.endswith(".wav"))
tagged, warns = wizard.parse_voices(readme)
tagged_names = [v["name"] for v in tagged]
raw_bullets = [b for b in wizard._bullets(readme) if b.startswith("- `")]
check(not warns and len(raw_bullets) == len(tagged), f"every voice bullet in voices/README.md is well formed {warns}")
check(sorted(set(tagged_names)) == sorted(tagged_names), f"no clip has two bullets {tagged_names}")
check(sorted(tagged_names) == wavs, f"bullets and clips match one to one (bullets {sorted(tagged_names)}, clips {wavs})")
check("derry_b.wav" in wavs and all(v["gender"] in wizard.GENDERS and v["accent"] and v["style"] for v in tagged),
      "derry_b.wav is shipped, and every voice has a gender, an accent and a style")
shipped, _ = wizard.list_voices()
check(names(shipped) == wavs and all(v["gender"] for v in shipped), "list_voices gives every shipped clip its tags")
vdir = os.path.join(TMP, "voices")
os.makedirs(vdir)
for n in ("z.wav", "a.wav", "notes.txt"):
    open(os.path.join(vdir, n), "wb").close()
listed, _ = wizard.list_voices(vdir)
check(names(listed) == ["a.wav", "z.wav"] and "no description" in listed[0]["style"]
      and wizard.filter_voices(listed, "woman", "any") == [] and len(wizard.filter_voices(listed, None, "any")) == 2,
      "a clip with no bullet still lists, but only matches either/any")

# --- the voice prompt: digits are menu picks, Enter is 1, only '/' or .wav is a path
check(wizard.voice_prompt(3) == "Pick 1-3 (Enter = 1), r = replay, a = all voices, b = back, or a path to your own .wav: ",
      "several matches: the prompt says Enter = 1 and offers all/back")
check(wizard.voice_prompt(1) == "Use this voice? Enter = yes, r = replay, a = all voices, b = back, or a path to your own .wav: "
      and "1-1" not in wizard.voice_prompt(1), "one match: a yes/no prompt with all/back, never 1-1")
check([wizard.voice_answer(x, 1) for x in ("", "y", "YES", "a", "b", "r")]
      == [("pick", 0), ("pick", 0), ("pick", 0), ("all", None), ("back", None), ("replay", None)],
      "one match: Enter/y = yes, a = all voices, b = back, r = replay")
check(wizard.voice_answer("a", 3) == ("all", None) and wizard.voice_answer("B", 3) == ("back", None), "a and b work with several matches")
check(wizard.voice_answer("2", 1)[0] == "error" and "a = all voices" in wizard.voice_answer("2", 1)[1],
      "a number with one match points at a = all voices")
cases = {"": ("pick", 0), "1": ("pick", 0), " 3 ": ("pick", 2), "r": ("replay", None), "R": ("replay", None),
         "~/v.wav": ("path", "~/v.wav"), "my voice.WAV": ("path", "my voice.WAV"), "/tmp/x": ("path", "/tmp/x")}
for answer, want in cases.items():
    check(wizard.voice_answer(answer, 3) == want, f"{answer!r} -> {want}")
kind, msg = wizard.voice_answer("4", 3)
check(kind == "error" and msg == "pick a number from 1 to 3", f"an out-of-range number is a menu error, never a path ({kind}: {msg})")
check(wizard.voice_answer("0", 3)[0] == "error" and wizard.voice_answer("12", 3)[0] == "error", "0 and 12 of 3 are menu errors")
check(wizard.voice_answer("derry", 3)[0] == "error", "a bare word is neither a pick nor a path")
one = [{"name": "q.wav", "gender": "woman", "accent": "Australian", "style": "broad and laid-back"}]
check(wizard.describe_matches(one) == ["  1 voice matches: broad and laid-back (woman, Australian). Playing it..."],
      f"one match reads as a sentence {wizard.describe_matches(one)}")
two = one + [{"name": "r.wav", "gender": "man", "accent": "Australian", "style": "relaxed drawl"}]
check(wizard.describe_matches(two) == ["  1. broad and laid-back (woman, Australian)", "  2. relaxed drawl (man, Australian)"],
      "several matches are numbered")

# --- the voice step end to end with scripted answers and a fake player (no model, no audio)
import builtins


class FakePlayer:
    def __init__(self):
        self.played = []

    def play(self, clip, speed, announce=None):
        self.played.append(os.path.basename(clip))


def run_voice_step(answers):
    feed, real_input = iter(answers), builtins.input
    builtins.input = lambda prompt="": next(feed)
    fp = FakePlayer()
    out = []
    real_print = builtins.print
    builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
    try:
        return os.path.basename(wizard.choose_voice(fp, 1.07)), fp.played, out
    finally:
        builtins.input, builtins.print = real_input, real_print


ALL = sorted(n for n in os.listdir(os.path.join(ROOT, "voices")) if n.endswith(".wav"))
# man + British (one match), then b (back), then woman + Irish, pick 2
got, played, out = run_voice_step(["2", str(wizard.accent_menu(wizard.list_voices()[0]).index("British") + 1), "b",
                                   "1", str(wizard.accent_menu(wizard.list_voices()[0]).index("Irish") + 1), "2"])
irish_women = [v["name"] for v in wizard.filter_voices(wizard.list_voices()[0], "woman", "Irish")]
check(played[0] == "british_man.wav" and got == irish_women[1] and any("1 voice matches" in o for o in out),
      f"b goes back to the questions; a new filter, then a pick ({played} -> {got})")
# man + British, then a (all voices), pick the last one
got, played, out = run_voice_step(["2", str(wizard.accent_menu(wizard.list_voices()[0]).index("British") + 1), "a", str(len(ALL))])
check(played[1:] == ALL and got == ALL[-1], f"a plays every shipped voice, numbered; a pick from that list works ({played})")
# one match, Enter = yes
got, played, _ = run_voice_step(["2", str(wizard.accent_menu(wizard.list_voices()[0]).index("British") + 1), ""])
check(got == "british_man.wav" and played == ["british_man.wav"], "one match: Enter takes it, played once")

# --- each voice is rendered once and replayed from the cache (no model: a fake engine)
class FakeEngine:
    def __init__(self):
        self.synths, self.voices = [], []

    def set_voice(self, clip):
        self.voices.append(clip)

    def synth(self, text):
        print("Generating speech tokens: 100%")              # library chatter, must not show
        os.write(2, b"S3 Token -> Mel Inference...\n")
        self.synths.append(text)
        fd, p = tempfile.mkstemp(suffix=".wav", dir=TMP)
        os.close(fd)
        return p


pl = wizard.Player()
pl.engine = FakeEngine()
paths = [pl._render("a.wav", wizard.SENTENCE), pl._render("a.wav", "voice 1"), pl._render("a.wav", wizard.SENTENCE),
         pl._render("b.wav", wizard.SENTENCE), pl._render("a.wav", wizard.SENTENCE)]
check(pl.engine.synths == [wizard.SENTENCE, "voice 1", wizard.SENTENCE] and paths[0] == paths[2] == paths[4],
      f"a replay of the same voice does not synthesize again {pl.engine.synths}")
pl.close()
check(not any(os.path.exists(p) for p in paths), "the cached clips are removed when the wizard ends")

# --- quiet(): library output (Python and native) is hidden; errors still surface
code = f"""
import os, sys
sys.path.insert(0, {os.path.join(ROOT, 'lib')!r})
from quiet import quiet
print("before")
with quiet():
    print("Fetching 8 files"); print("Download complete", file=sys.stderr)
    os.write(1, b"native stdout\\n"); os.write(2, b"native stderr\\n")
print("after")
try:
    with quiet():
        raise RuntimeError("model load failed: no such file")
except RuntimeError as e:
    print("caught:", e)
print("end")
"""
r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=10)
check(r.stdout.split("\n")[:4] == ["before", "after", "caught: model load failed: no such file", "end"] and r.stderr == "",
      f"quiet() hides chatter, keeps our lines and the error message {r.stdout!r} {r.stderr!r}")

# --- own .wav checks
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "
spaced = os.path.join(TMP, "my voice.wav")
open(spaced, "wb").write(WAV)
path, why = wizard.check_own_wav(f"  '{spaced}'  ")
check(path == spaced and why is None, f"a path with spaces (even quoted, as Finder drags it) is accepted {why}")
path, why = wizard.check_own_wav(os.path.join(TMP, "nope.wav"))
check(path is None and "no such file" in why, f"a missing file is refused: {why}")
txt = os.path.join(TMP, "notes.txt")
open(txt, "w").write("x")
check(wizard.check_own_wav(txt)[0] is None, "a non-.wav name is refused")
fake = os.path.join(TMP, "fake.wav")
open(fake, "wb").write(b"not audio at all")
check("RIFF" in (wizard.check_own_wav(fake)[1] or ""), "a .wav without a RIFF/WAVE header is refused")
odd = os.path.join(TMP, "voice(1).wav")
open(odd, "wb").write(WAV)
check("characters" in (wizard.check_own_wav(odd)[1] or ""), "a path the config could not hold is refused")
locked = os.path.join(TMP, "locked.wav")
open(locked, "wb").write(WAV)
os.chmod(locked, 0)
if os.access(locked, os.R_OK):
    check(True, "unreadable file (skipped: running as a user who can read anything)")
else:
    check("cannot read" in (wizard.check_own_wav(locked)[1] or ""), "an unreadable file is refused")
os.chmod(locked, 0o600)

# --- config: only two lines, mode 600, old file backed up, readable by bin/yapper
check(wizard.voice_value(os.path.join(ROOT, "voices", "derry_b.wav")) == "derry_b", "a clip in voices/ is written by name")
check(wizard.voice_value(spaced) == spaced, "an own clip is written as its full path")
cfg_dir = os.path.join(home, ".config", "yapper")
cfg = os.path.join(cfg_dir, "config")
backup, made = wizard.write_config(cfg, wizard.config_text("derry_b", 1.2))
check(backup is None and not made and open(cfg).read() == "voice=derry_b\nspeed=1.2\n", "a fresh config holds exactly the two lines")
check(stat.S_IMODE(os.stat(cfg).st_mode) == 0o600 and stat.S_IMODE(os.stat(cfg_dir).st_mode) == 0o700,
      "config 600, new folder 700")
with open(cfg, "a") as f:
    f.write("# my note\n")
backup, made = wizard.write_config(cfg, wizard.config_text(spaced, 0.95))
check(made and backup == cfg + ".bak" and open(backup).read().endswith("# my note\n")
      and stat.S_IMODE(os.stat(backup).st_mode) == 0o600, "the old config is kept as config.bak, mode 600")
check(open(cfg).read() == f"voice={spaced}\nspeed=0.95\n", "the new config has only the voice and speed lines")
check(sorted(os.listdir(cfg_dir)) == ["config", "config.bak"], f"nothing else is written {os.listdir(cfg_dir)}")
check(wizard.read_speed(cfg) == 0.95 and wizard.read_speed(os.path.join(TMP, "none")) == 1.07, "read_speed reads it back, default 1.07")
r = subprocess.run([os.path.join(ROOT, "bin", "yapper"), "--config"], capture_output=True, text=True, timeout=20,
                   env={"PATH": "/usr/bin:/bin", "HOME": home})
check(r.returncode == 0 and spaced in r.stdout and "0.95" in r.stdout, f"bin/yapper reads the written config back {r.stderr.strip()[-80:]!r}")

# the FIRST backup is kept: a third run leaves config.bak alone
backup, made = wizard.write_config(cfg, wizard.config_text("derry_b", 1.07))
check(backup == cfg + ".bak" and not made and open(backup).read().endswith("# my note\n"),
      "a later run never overwrites the first config.bak")

# planted symlinks at the backup or temp names are replaced, never followed
victim = os.path.join(TMP, "victim")
open(victim, "w").write("untouched\n")
os.unlink(cfg + ".bak")
os.symlink(victim, cfg + ".bak")
wizard.write_config(cfg, wizard.config_text("derry_b", 1.2))
check(open(victim).read() == "untouched\n", "a symlink planted at config.bak is not followed")
for name in ("config.new",):
    os.symlink(victim, os.path.join(cfg_dir, name))
wizard.write_config(cfg, wizard.config_text("derry_b", 1.2))
check(open(victim).read() == "untouched\n" and sorted(n for n in os.listdir(cfg_dir) if not n.startswith(".yapper-"))
      == ["config", "config.bak", "config.new"] and not any(n.startswith(".yapper-") for n in os.listdir(cfg_dir)),
      "temp files come from mkstemp; nothing named .new is written through, no temp file left behind")
os.unlink(os.path.join(cfg_dir, "config.new"))

# a symlinked config (dotfiles) is written through to its real file
dot = os.path.join(TMP, "dotfiles")
os.makedirs(dot, mode=0o700)
real = os.path.join(dot, "yapper.conf")
open(real, "w").write("voice=derry_b\nspeed=1.2\n")
link_home = os.path.join(TMP, "linkhome")
os.makedirs(link_home, mode=0o700)
link = os.path.join(link_home, "config")
os.symlink(real, link)
wizard.write_config(link, wizard.config_text("derry_b", 0.95))
check(os.path.islink(link) and open(real).read() == "voice=derry_b\nspeed=0.95\n" and os.path.exists(real + ".bak"),
      "a symlinked config stays a symlink; its real file is updated and backed up beside it")

# --- the final voice value must be one bin/yapper accepts
check(wizard.config_voice_problem("derry_b") is None and wizard.config_voice_problem(spaced) is None, "shipped name and own path are accepted")
check("characters" in (wizard.config_voice_problem("my(voice)") or ""), "a name with () is refused before writing")
check("yapper can find" in (wizard.config_voice_problem("nosuch") or ""), "a name with no clip is refused")
upper = os.path.join(TMP, "Mine.WAV")
open(upper, "wb").write(WAV)
check("lowercase .wav" in (wizard.check_own_wav(upper)[1] or ""), "Mine.WAV is refused (bin/yapper only reads lowercase .wav)")
check(wizard.resolve_voice(upper) is None and wizard.resolve_voice(spaced) == spaced, "resolve_voice matches the launcher's rule")
odd_dir = os.path.join(TMP, "oddvoices")
os.makedirs(odd_dir)
open(os.path.join(odd_dir, "my(voice).wav"), "wb").write(WAV)
open(os.path.join(odd_dir, "Loud.WAV"), "wb").write(WAV)
check([v["name"] for v in wizard.list_voices(odd_dir)[0]] == ["my(voice).wav"], "an upper-case .WAV in voices/ is not offered")
check("characters" in (wizard.config_voice_problem(wizard.voice_value(os.path.join(odd_dir, "my(voice).wav"), odd_dir), odd_dir) or ""),
      "a menu pick whose name the config cannot hold is refused")

# --- digits: only ASCII digits are numbers ("²" must not crash int())
check(wizard.voice_answer("²", 3)[0] == "error" and wizard.pick_from_menu("²", ["a", "b"], "a") is None, "superscript digits are not menu picks")

# --- main(): any error is one plain line and exit 1, never a traceback
class _TTY:
    def isatty(self):
        return True


import io
real_stdin, real_stderr, real_choose = sys.stdin, sys.stderr, wizard.choose_voice
sys.stdin, sys.stderr = _TTY(), io.StringIO()
wizard.choose_voice = lambda *a: (_ for _ in ()).throw(RuntimeError("model load failed: weights missing"))
real_stdout = sys.stdout
sys.stdout = io.StringIO()
try:
    rc = wizard.main()
    err = sys.stderr.getvalue()
finally:
    sys.stdin, sys.stderr, sys.stdout, wizard.choose_voice = real_stdin, real_stderr, real_stdout, real_choose
check(rc == 1 and err.strip() == "yapper: setup failed: model load failed: weights missing", f"main(): one plain error line, exit 1 ({rc} {err!r})")

# --- speed pick
check([wizard.pick_speed(a) for a in ("", "1", "2", "3", " 3 ", "4", "fast")] == [1.07, 0.95, 1.07, 1.2, 1.2, None, None],
      "Enter -> 1.07, 1-3 pick, anything else asks again")

# --- dictation: a fake missing app, and a fake present one (never really opened)
said, opened = [], []
answers = iter([""])
res = wizard.dictation_step(lambda p: next(answers), said.append, opener=opened.append,
                            apps=(os.path.join(TMP, "Missing.app"),))
check(res == "missing-skipped" and not opened and any("brew install --cask opensuperwhisper" in s for s in said)
      and any("optional" in s for s in said), f"missing app -> install line, skippable, nothing opened ({res})")
app = os.path.join(TMP, "OpenSuperWhisper.app")
os.makedirs(app)
said.clear()
answers = iter(["", "hello yapper"])
res = wizard.dictation_step(lambda p: next(answers), said.append, opener=opened.append, apps=(app,))
text = " ".join(said)
check(res == "ok" and opened == [app], f"present app -> opened, typed words pass ({res})")
check("Microphone" in text and "Accessibility" in text and "hotkey" in text and "tmux" in text and "hello yapper" in text,
      "the permission and hotkey steps are spelled out")
check("Nothing arrived" in text, "an empty line asks again")
answers = iter(["s"])
check(wizard.dictation_step(lambda p: next(answers), said.append, opener=opened.append, apps=(app,)) == "skipped", "s skips")

launcher = open(os.path.join(ROOT, "bin", "yapper")).read()
setup_block = launcher[launcher.index('= "--setup" ]'):launcher.index("load_config\nsock_check")]
check('echo "To change voice or speed later, run yapper --setup again."' in setup_block
      and setup_block.index("start with: yapper") < setup_block.index("To change voice or speed later"),
      "--setup ends by saying how to change voice or speed later")

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok else 1)
