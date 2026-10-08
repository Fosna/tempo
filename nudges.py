"""Adapter around the nudge CLI.

Tempo only talks to nudge through its command line, so the two projects stay
independent. Callers must never hold the tasks.json lock while calling in here: a
slow or hung nudge would then hold the lock too. And a failure here must never stop
tracking, so every problem surfaces as NudgeError for the caller to turn into a
warning.

`TEMPO_NUDGE` overrides the command (default: `nudge` on the PATH, else the shim
nudge's installer writes to ~/.local/bin, which is often not on the PATH).
"""

import os
import shlex
import shutil
import subprocess


# Where nudge's install.py writes its shim.
FALLBACK = os.path.expanduser("~/.local/bin/nudge")


class NudgeError(Exception):
    pass


def find():
    """Path to the nudge command, or None if it is not installed."""
    found = shutil.which("nudge")
    if found:
        return found
    return FALLBACK if os.access(FALLBACK, os.X_OK) else None


def _command():
    configured = os.environ.get("TEMPO_NUDGE")
    cmd = shlex.split(configured) if configured else [find() or ""]
    if not cmd[0]:
        raise NudgeError("nudge not found on the PATH or in ~/.local/bin "
                         "(install it, or set TEMPO_NUDGE)")
    return cmd


def _run(args, timeout=10):
    try:
        p = subprocess.run(
            _command() + args, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NudgeError("could not run nudge: %s" % e)
    return p.returncode, p.stdout, p.stderr


def schedule(seconds, message):
    """Schedule one notification; return its job id."""
    rc, out, err = _run(["in", "%ds" % seconds, message, "--title", "tempo"])
    fields = out.split()
    if rc != 0 or not fields:
        raise NudgeError("nudge in failed: %s" % (err.strip() or out.strip() or rc))
    return fields[0]


def cancel(job_id):
    """Cancel a pending job. A job that is already gone (fired or cancelled) is fine."""
    rc, out, err = _run(["cancel", job_id])
    if rc != 0 and "no such job" not in err:
        raise NudgeError("nudge cancel failed: %s" % (err.strip() or rc))


def pending_ids():
    rc, out, err = _run(["list"])
    if rc != 0:
        raise NudgeError("nudge list failed: %s" % (err.strip() or rc))
    return {line.split()[0] for line in out.splitlines() if line.strip() and line[0] != "("}


def daemon_status():
    """(ok, detail): whether scheduled nudges will actually fire."""
    rc, out, err = _run(["status"])
    return rc == 0, (out + err).strip()
