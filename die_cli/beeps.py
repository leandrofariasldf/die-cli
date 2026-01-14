import time

try:
    import winsound
except Exception:
    winsound = None


def _beep(freq, duration_ms):
    if winsound is not None:
        try:
            winsound.Beep(freq, duration_ms)
            return
        except Exception:
            pass
    try:
        print("\a", end="", flush=True)
    except Exception:
        pass


def beep_short():
    _beep(1200, 25)


def beep_long():
    _beep(800, 120)


def beep_short_triplet():
    for _ in range(3):
        beep_short()
        time.sleep(0.03)
