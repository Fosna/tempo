"""Durations for tempo: parsing estimates like `45`, `90m`, `1h30m` into whole minutes, and
formatting minutes back for display. The grammar is nudge's."""

import math
import re

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_PAIR = re.compile(r"(\d+)([smhd])")


class BadDuration(ValueError):
    pass


def parse_duration(token):
    """Return seconds for tokens like 10m, 90s, 1h30m, or a bare number of minutes.

    Rejects anything the grammar does not fully consume, so `1m30` and `10x`
    raise instead of quietly meaning `1m` and `10m`.
    """
    tok = token.strip().lower()
    if not tok:
        raise BadDuration("empty duration")

    if tok.isdigit():
        return int(tok) * 60  # bare number means minutes

    total = 0
    consumed = 0
    for m in _PAIR.finditer(tok):
        if m.start() != consumed:
            break
        total += int(m.group(1)) * _UNITS[m.group(2)]
        consumed = m.end()
    if consumed != len(tok) or total == 0:
        raise BadDuration("bad duration: %r (try 45, 90m, 1h30m)" % token)
    return total


def parse_minutes(token):
    """Whole minutes for an estimate, rounded up, at least 1."""
    seconds = parse_duration(token)
    if seconds <= 0:
        raise BadDuration("estimate must be at least 1m: %r" % token)
    return max(1, math.ceil(seconds / 60))


def format_minutes(minutes):
    h, m = divmod(int(round(minutes)), 60)
    if h and m:
        return "%dh%dm" % (h, m)
    return "%dh" % h if h else "%dm" % m
