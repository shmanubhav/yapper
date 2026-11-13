"""yapper --sample: hear each voice clip at each speed, so you can pick one by ear.

One short-lived process. It loads the pinned model once (the same loader as lib/speaker.py),
then for every clip and every speed it announces "voice N, speed X" and speaks one neutral
sentence. It starts no server: two copies of the model would need about 9 GB of memory, so
`yapper --sample` pauses a running engine first and starts it again afterwards. Run directly,
this script refuses while an engine answers on the socket.

  yapper --sample                  every .wav in voices/
  yapper --sample derry_b my.wav   named clips: a name in voices/ or a path to a .wav
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICES = os.path.join(ROOT, "voices")
SPEEDS = (0.95, 1.07, 1.2)
SENTENCE = "Here is a short sample of this voice. The build finished, and two tests need a second look."


class UsageError(Exception):
    pass


def engine_running(sock_path=None):
    """True when yapper's engine answers on its socket (YAPPER_SOCKET or the default)."""
    import speechsock
    path = speechsock.check_path(sock_path or speechsock.default_socket())   # raises SocketError
    return speechsock.answers(path)


def resolve_clip(arg, voices=VOICES):
    """A path (anything with a slash) or a name in voices/, with or without .wav."""
    if "/" in arg:
        path = os.path.expanduser(arg)
    else:
        path = os.path.join(voices, arg if arg.endswith(".wav") else arg + ".wav")
    if not path.endswith(".wav") or not os.path.isfile(path):
        raise UsageError(f"not a .wav file: {arg}")
    return path


def list_clips(args, voices=VOICES):
    """The clips to try, in order: the ones named, or every .wav in voices/ sorted by name."""
    if args:
        return [resolve_clip(a, voices) for a in args]
    try:
        names = sorted(n for n in os.listdir(voices) if n.endswith(".wav") and not n.startswith("."))
    except OSError:
        names = []
    if not names:
        raise UsageError(f"no .wav clips in {voices}")
    return [os.path.join(voices, n) for n in names]


def plan(clips, speeds=SPEEDS):
    """(announcement, clip, speed) for every clip and speed, clip by clip."""
    return [(f"voice {i}, speed {s:g}", clip, s)
            for i, clip in enumerate(clips, 1) for s in speeds]


def parse(argv):
    if any(a in ("-h", "--help") for a in argv):
        raise UsageError(__doc__.strip())
    bad = [a for a in argv if a.startswith("-")]
    if bad:
        raise UsageError(f"unknown option {bad[0]}; usage: yapper --sample [clip ...]")
    return list_clips(argv)


def run(steps):                                     # needs the model and speakers; never run by tests
    from itertools import groupby
    from speaker import AfPlayer, ChatterboxEngine
    from quiet import quiet                         # hide model chatter; the user sees our lines only
    engine = ChatterboxEngine()
    print("yapper: loading the voice model once (about 30 s)...", flush=True)
    with quiet():
        engine.load_model()
    for clip, group in groupby(steps, key=lambda step: step[1]):
        with quiet():
            engine.set_voice(clip)
            sample_wav = engine.synth(SENTENCE)
        try:
            for announce, _, speed in group:
                print(f"  {announce}: {os.path.basename(clip)}", flush=True)
                with quiet():
                    label = engine.synth(announce)
                players = [AfPlayer(1.0), AfPlayer(speed)]
                try:
                    players[0].start(label).wait()
                    players[1].start(sample_wav).wait()
                finally:
                    for p in players:          # afplay runs in its own session: Ctrl-C must stop it
                        p.kill()
                    os.unlink(label)
        finally:
            os.unlink(sample_wav)


def main(argv):
    try:
        steps = plan(parse(argv))
    except UsageError as e:
        print(f"yapper: {e}", file=sys.stderr)
        return 2
    import speechsock
    try:
        running = engine_running()
    except speechsock.SocketError as e:
        print(f"yapper: {e}", file=sys.stderr)
        return 2
    if running:
        print("yapper: the speech engine is running; run yapper --stop first, then yapper --sample",
              file=sys.stderr)
        return 1
    try:
        run(steps)
    except KeyboardInterrupt:
        print("\nyapper: sample stopped", file=sys.stderr)
        return 130
    print("yapper: done. put the one you liked in ~/.config/yapper/config (voice=..., speed=...)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
