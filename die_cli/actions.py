import os
import time

import psutil


def _set_status(state, message):
    with state.lock:
        state.status = message


def _terminate_then_kill(proc):
    try:
        proc.terminate()
    except psutil.NoSuchProcess:
        return True, None
    except Exception:
        pass

    try:
        proc.wait(timeout=0.3)
    except psutil.NoSuchProcess:
        return True, None
    except Exception:
        pass

    try:
        if proc.is_running():
            proc.kill()
            proc.wait(timeout=0.5)
    except psutil.NoSuchProcess:
        return True, None
    except Exception as e:
        return False, e

    try:
        if proc.is_running():
            return False, RuntimeError("still alive")
    except psutil.NoSuchProcess:
        return True, None
    except Exception as e:
        return False, e

    return True, None


def _kill_single(pid, name, my_pid, state):
    if pid == my_pid:
        _set_status(state, f"NOPE: won't kill myself ({pid})")
        return

    try:
        proc = psutil.Process(pid)
    except Exception as e:
        _set_status(state, f"FAILED {pid} {name} (Error: {type(e).__name__}: {e})")
        return

    ok, err = _terminate_then_kill(proc)
    if ok:
        _set_status(state, f"KILLED {pid} {name}")
        return

    if isinstance(err, RuntimeError) and str(err) == "still alive":
        _set_status(state, f"STILL ALIVE {pid} {name} (protected/respawn?)")
        return

    _set_status(state, f"FAILED {pid} {name} (Error: {type(err).__name__}: {err})")


def _kill_tree(pid, name, my_pid, state):
    if pid == my_pid:
        _set_status(state, f"NOPE: won't kill myself ({pid})")
        return

    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except Exception as e:
        _set_status(state, f"FAILED TREE {pid} {name} (Erro: {type(e).__name__}: {e})")
        return

    targets = [child for child in children if child.pid != my_pid]
    target_count = len(targets) + 1

    first_error = None
    for child in targets:
        ok, err = _terminate_then_kill(child)
        if not ok and first_error is None:
            first_error = err

    ok, err = _terminate_then_kill(parent)
    if not ok and first_error is None:
        first_error = err

    if first_error is not None:
        _set_status(
            state,
            f"FAILED TREE {pid} {name} (Erro: {type(first_error).__name__}: {first_error})",
        )
        return

    _set_status(state, f"KILLED TREE {pid} {name} ({target_count} procs)")


def action_worker(state):
    my_pid = os.getpid()

    while state.running:
        job = None
        with state.lock:
            if state.action_queue:
                job = state.action_queue.pop(0)

        if job is None:
            time.sleep(0.01)
            continue

        kind = job.get("kind")
        pid = int(job.get("pid", -1))
        name = job.get("name", "?")

        if kind == "KILL":
            _kill_single(pid, name, my_pid, state)
        elif kind == "KILL_TREE":
            _kill_tree(pid, name, my_pid, state)
