"""Mic watcher: stops speech the moment any input device starts recording.
Polls CoreAudio's per-device "running somewhere" flag; stdlib only."""
import ctypes, ctypes.util, logging, os, signal, struct, sys, time

sys.dont_write_bytecode = True     # never write __pycache__ into the clone
import speechsock          # same folder; the socket comes from YAPPER_SOCKET, exported by the launcher

POLL_S = 0.05
RESCAN_S = 5.0

log = logging.getLogger("micwatch")


def rising(prev, now):
    """Device ids that went from idle (False, None or absent) to in use (True)."""
    return [d for d, v in now.items() if v is True and prev.get(d) is not True]


def _cc(s):
    return struct.unpack(">I", s.encode())[0]


class _Addr(ctypes.Structure):
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]


def scan_message(names):
    """(log level, text) for a device scan; empty means dictation cannot be detected."""
    if not names:
        return logging.WARNING, "no input devices: dictation cannot be detected"
    return logging.INFO, "watching: " + ", ".join(names)


class CoreAudio:
    def __init__(self):
        ca, cf = ctypes.util.find_library("CoreAudio"), ctypes.util.find_library("CoreFoundation")
        if not ca or not cf:
            raise OSError("CoreAudio or CoreFoundation framework not found")
        self._ca = ctypes.CDLL(ca)
        self._cf = ctypes.CDLL(cf)
        self._cf.CFRelease.argtypes = [ctypes.c_void_p]
        p = ctypes.POINTER
        self._ca.AudioObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32, p(_Addr), ctypes.c_uint32, ctypes.c_void_p, p(ctypes.c_uint32)]
        self._ca.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32, p(_Addr), ctypes.c_uint32, ctypes.c_void_p, p(ctypes.c_uint32), ctypes.c_void_p]
        self._cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        self._glob, self._input = _cc("glob"), _cc("inpt")

    def _get(self, obj, sel, scope=None):
        a = _Addr(_cc(sel), self._glob if scope is None else scope, 0)
        n = ctypes.c_uint32(0)
        if self._ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(a), 0, None, ctypes.byref(n)) or not n.value:
            return None
        buf = ctypes.create_string_buffer(n.value)
        if self._ca.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(n), buf):
            return None
        return buf.raw[:n.value]

    def devices(self):
        raw = self._get(1, "dev#")
        if not raw:
            return []
        raw = raw[:len(raw) // 4 * 4]    # truncate a ragged buffer
        return [d for d in struct.unpack(f"{len(raw) // 4}I", raw) if self._get(d, "stm#", self._input)]

    def running(self, dev):
        raw = self._get(dev, "gone")
        return bool(struct.unpack("I", raw)[0]) if raw and len(raw) == 4 else None

    def name(self, dev):
        fallback = f"device {dev}"
        raw = self._get(dev, "lnam")
        if not raw or len(raw) != 8:
            return fallback
        ref = struct.unpack("Q", raw)[0]
        if not ref:
            return fallback
        try:
            out = ctypes.create_string_buffer(256)
            if not self._cf.CFStringGetCString(ref, out, 256, 0x08000100):
                return fallback
            return out.value.decode(errors="replace") or fallback
        finally:
            self._cf.CFRelease(ref)


def stop_speech(sock_path=None):
    """POST /stop to the speech server. Never raises; True on any 2xx."""
    try:
        status, _ = speechsock.request("POST", "/stop", body=b"", timeout=1, sock_path=sock_path)
        return 200 <= status < 300
    except Exception as e:
        log.info("stop request failed: %s", e)
        return False


def watch(read_states, stop, poll=POLL_S, sleep=time.sleep, ticks=None):
    """Call stop() once per tick in which any device went idle -> in use. First reading only seeds."""
    prev = None
    n = 0
    while ticks is None or n < ticks:
        now = read_states()
        if prev is not None:
            fired = rising(prev, now)
            if fired:
                log.info("mic started on device(s) %s, stopping speech", fired)
                stop()
        prev = now
        n += 1
        sleep(poll)


def main():
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    pidfile = speechsock.default_socket() + ".micwatch.pid"
    if not speechsock.claim_pidfile(pidfile, os.path.realpath(__file__)):
        log.info("another mic watcher already runs for this socket; exiting")
        return
    try:
        ca = CoreAudio()
    except OSError as e:
        log.error("%s", e)
        speechsock.release_pidfile(pidfile)
        sys.exit(1)
    cache = {"devs": [], "at": None, "failed": False}

    def read_states():
        try:
            now = time.monotonic()
            first = cache["at"] is None
            if first or now - cache["at"] >= RESCAN_S:
                devs = ca.devices()
                if first or sorted(devs) != sorted(cache["devs"]):
                    level, text = scan_message([f"{ca.name(d)} ({d})" for d in devs])
                    log.log(level, "%s", text)
                cache["devs"], cache["at"] = devs, now
            states = {d: ca.running(d) for d in cache["devs"]}
            cache["failed"] = False
            return states
        except Exception as e:
            if not cache["failed"]:
                log.warning("CoreAudio read failed: %s", e)
            cache["failed"] = True
            return {}

    try:
        watch(read_states, stop_speech)
    except KeyboardInterrupt:
        pass
    finally:
        speechsock.release_pidfile(pidfile)


if __name__ == "__main__":
    main()
