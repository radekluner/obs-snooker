"""Workaround for a real, reproducible ``pynput``/macOS crash inside OBS.

See ``AGENTS.md`` ("pynput keyboard listener must be started on the main
thread on macOS") for the full crash analysis and history. This module
contains the actual fix: calling ``start_keyboard_listener()`` directly from
the main thread (instead of wrapping it in an extra ``threading.Thread``) was
*not* sufficient, because ``pynput.keyboard.Listener`` is itself a
``threading.Thread`` subclass - its ``_run()`` method always executes on the
listener's own background thread, no matter which thread called ``.start()``.
That ``_run()`` method calls ``pynput._util.darwin.keycode_context()``, which
uses several Carbon/Text-Services-Manager (TSM) functions
(``TISCopyCurrentKeyboardInputSource``, ``TISGetInputSourceProperty``, ...) to
read the current keyboard layout. Inside a real Cocoa host application such as
OBS (which runs its own main dispatch queue), these TSM calls abort the whole
process with ``SIGTRAP``/``dispatch_assert_queue_fail`` inside
``TSMGetInputSourceProperty`` when made off the main thread - which they
always are here, since they run on the listener's own thread.

Since the keyboard layout essentially never changes while the listener is
running, this module computes it once - here, explicitly on the caller's
thread (which must be the main thread) - caches the result, and monkeypatches
``pynput.keyboard._darwin.keycode_context`` to hand back the cached value
instead of touching Carbon/TSM again from the listener's background thread.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any, Iterator

_patched = False


def apply_macos_keycode_context_workaround() -> bool:
    """Pre-computes and caches pynput's macOS keyboard-layout context.

    Must be called from the main thread, before ``pynput.keyboard.Listener``
    is constructed/started.

    Returns True if the workaround is (now, or already was) applied, False if
    it was skipped (non-macOS) or could not be applied (e.g. a future pynput
    release changed its internals in a way this workaround doesn't recognize
    anymore). On failure, pynput's default behavior is left in place instead
    of raising - this is a best-effort crash mitigation, not something that
    should ever prevent the rest of the script from loading.
    """
    global _patched
    if _patched:
        return True
    if sys.platform != "darwin":
        return False

    try:
        from pynput._util import darwin as darwin_util
        from pynput.keyboard import _darwin as darwin_keyboard

        # Runs the real Carbon/TSM calls once, here, on the (safe) calling
        # thread, and keeps only the plain extracted values (an int and a
        # bytes object) - no live Carbon references are cached.
        with darwin_util.keycode_context() as cached_context:

            @contextlib.contextmanager
            def _cached_keycode_context() -> Iterator[Any]:
                yield cached_context

            # `Listener._run()` uses the module-local name `keycode_context`
            # (bound via `from pynput._util.darwin import keycode_context`),
            # so it must be patched on `pynput.keyboard._darwin`, not on
            # `pynput._util.darwin` itself.
            darwin_keyboard.keycode_context = _cached_keycode_context
    except Exception as exc:  # pragma: no cover - best-effort workaround
        print(f"Could not apply macOS pynput keycode-context workaround: {exc}")
        return False

    _patched = True
    return True
