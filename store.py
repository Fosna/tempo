"""Persistent JSON state for tempo.

One file, rewritten atomically under an flock. Every change is a read-modify-write
inside `transaction()`, so concurrent CLI calls cannot lose each other's updates.
Paths are resolved per call so tests can point TEMPO_HOME somewhere temporary.

Unlike nudge's queue, an unreadable file is never overwritten: it is set aside and
tracking continues on a fresh one. Repairing or merging the set-aside file is a later,
separate step; capturing new work comes first.
"""

import contextlib
import fcntl
import json
import os
import sys
import tempfile
import time

SCHEMA_VERSION = 1


class StoreError(Exception):
    pass


def _home():
    return os.path.expanduser(os.environ.get("TEMPO_HOME", "~/.tempo"))


def _path(name):
    return os.path.join(_home(), name)


def new_state():
    return {"schemaVersion": SCHEMA_VERSION, "tasks": []}


def _lock_timeout():
    try:
        return float(os.environ.get("TEMPO_LOCK_TIMEOUT", "5"))
    except ValueError:
        return 5.0


def _holder_pid(fd):
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 32).decode().strip() or "unknown"
    except OSError:
        return "unknown"


@contextlib.contextmanager
def _locked():
    """Hold an exclusive lock for the block, or fail with the holder's pid.

    The kernel drops a flock when its process dies, so a crash cannot leave a stale
    lock. Only a live, stuck process can hold it past the timeout, and killing that
    pid releases it.
    """
    os.makedirs(_home(), exist_ok=True)
    fd = os.open(_path("tasks.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.monotonic() + _lock_timeout()
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StoreError(
                        "tasks.lock is held by pid %s; if it is stuck, kill it and retry"
                        % _holder_pid(fd)
                    )
                time.sleep(0.05)
        os.ftruncate(fd, 0)
        os.write(fd, ("%d\n" % os.getpid()).encode())
        yield
    finally:
        os.close(fd)


def _valid(data):
    return (
        isinstance(data, dict)
        and isinstance(data.get("schemaVersion"), int)
        and isinstance(data.get("tasks"), list)
    )


def _set_aside(path):
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dest = "%s.corrupt-%s" % (path, stamp)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = "%s.corrupt-%s-%d" % (path, stamp, n)
    os.replace(path, dest)
    return dest


def _read():
    path = _path("tasks.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return new_state()
    except (ValueError, OSError):
        data = None

    if not _valid(data):
        dest = _set_aside(path)
        print(
            "tempo: tasks.json was unreadable; set it aside as %s and started a new one"
            % dest,
            file=sys.stderr,
        )
        return new_state()
    if data["schemaVersion"] > SCHEMA_VERSION:
        raise StoreError(
            "tasks.json has schemaVersion %d; this tempo understands up to %d"
            % (data["schemaVersion"], SCHEMA_VERSION)
        )
    return data


def _write(state):
    path = _path("tasks.json")
    if os.path.exists(path):
        with open(path, "rb") as src, open(_path("tasks.json.bak"), "wb") as dst:
            dst.write(src.read())  # last known-good copy; the file was valid when read
    fd, tmp = tempfile.mkstemp(dir=_home())
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def reports_dir():
    return _path("reports")


def aside_files():
    """Names of tasks.json files that were set aside as unreadable."""
    try:
        return sorted(n for n in os.listdir(_home()) if n.startswith("tasks.json.corrupt-"))
    except FileNotFoundError:
        return []


def read():
    """Snapshot for read-only commands. Never writes."""
    with _locked():
        return _read()


@contextlib.contextmanager
def transaction():
    """Yield the state; save it on a clean exit, discard it if the body raises."""
    with _locked():
        state = _read()
        yield state
        _write(state)
