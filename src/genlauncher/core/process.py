"""Follow the real game process instead of the process we spawned.

Steam's ``-applaunch`` helper is a hand-off: it tells the already running
client to start the game and returns at once. Waiting on *that* process means
the launcher believes the session is over while Proton is still loading, so it
unlinks and restores the vanilla archives — the mod the user selected never
gets read. The game itself has to be watched directly.

Proton mode does not have this problem: ``proton run`` blocks for the lifetime
of the Windows process.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

PROC_ROOT = Path("/proc")
POLL_INTERVAL = 0.5

# Steam normally hands the game over within seconds; a minute is generous
# enough for a client update check without leaving the UI disabled for
# minutes when the launch silently fails.
START_TIMEOUT = 60.0


def normalize_names(names: Iterable[str]) -> set[str]:
    """Lower-cased file names to look for, empty ones dropped."""
    return {n.strip().casefold() for n in names if n and n.strip()}


def pids_matching(names: set[str]) -> set[int]:
    """Pids whose command line mentions one of ``names``.

    Wine and Proton show up with the Windows path in their command line, so the
    file name is matched anywhere in it rather than as a whole argument.
    """
    if not names:
        return set()

    needles = sorted(
        n.strip().casefold() for n in names if n and n.strip()
    )
    found: set[int] = set()
    try:
        entries = list(PROC_ROOT.iterdir())
    except OSError:  # pragma: no cover - /proc missing is not survivable anyway
        return set()

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            # Gone already, or owned by another user: both are fine to skip.
            continue
        if not raw:
            continue
        # ``bytes`` has no casefold(); .lower() is enough because the
        # names we look for are plain ASCII file names.
        cmd = raw.replace(b"\0", b" ").lower()
        if any(needle.encode("utf-8", "ignore") in cmd for needle in needles):
            found.add(int(entry.name))
    return found


def wait_for_start(names: set[str], timeout: float = START_TIMEOUT) -> bool:
    """Block until one of ``names`` is running. True if it turned up in time."""
    if not names:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pids_matching(names):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def wait_for_exit(names: set[str], poll_interval: float = POLL_INTERVAL) -> None:
    """Block until none of ``names`` is running."""
    while pids_matching(names):
        time.sleep(poll_interval)


def wait_for_session(
    names: set[str],
    start_timeout: float = START_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> bool:
    """Wait for the game to start and then to finish.

    Returns True when a matching process was actually observed, False when the
    start window passed without one. The caller needs to know the difference:
    a False means "we could not see the game", not "the game is done".
    """
    if not names:
        return False

    deadline = time.monotonic() + start_timeout
    seen = False

    while True:
        running = bool(pids_matching(names))
        if running:
            seen = True
        elif seen:
            return True
        elif time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)
