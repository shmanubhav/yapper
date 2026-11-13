"""quiet(): hide model and library chatter (progress bars, inference prints) around a call.

Used by the interactive tools (lib/wizard.py, lib/sample.py) so the user sees only yapper's own
lines. Silences both Python-level and file-descriptor-level stdout/stderr, since some of the noise
comes from native code. An exception still propagates, with its message, once output is restored.
The speech server does not use this: tts.log keeps everything.
"""
import contextlib, os, sys


@contextlib.contextmanager
def quiet():
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            yield
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:                       # noqa: BLE001
            pass
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in saved + [devnull]:
            os.close(fd)
