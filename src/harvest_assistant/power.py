"""Small Windows helpers for a long unattended run: keep the PC awake, make noise when something is wrong."""

from __future__ import annotations

import ctypes
import logging
import threading

logger = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake(on: bool) -> None:
    """Stop Windows from sleeping or turning the screen off (the game must stay visible to be read).

    The state belongs to the calling thread: call it from the worker thread that runs the cycle."""
    try:
        flags = ES_CONTINUOUS | ((ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED) if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:  # not Windows, or the call is refused: harmless
        logger.debug("keep_awake unavailable", exc_info=True)


def alert() -> None:
    """Three beeps, without blocking the caller."""
    def beep() -> None:
        try:
            import winsound
            for frequency in (880, 660, 880):
                winsound.Beep(frequency, 260)
        except Exception:
            logger.debug("beep unavailable", exc_info=True)

    threading.Thread(target=beep, name="alert", daemon=True).start()
