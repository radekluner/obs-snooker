"""Tests for the pynput/macOS TSM-crash workaround.

See ``obs_snooker/darwin_keycode_patch.py`` and ``AGENTS.md`` for the full
crash analysis this workaround addresses. These tests only run on macOS
(where ``pynput``'s Darwin backend - and therefore this workaround - is
relevant); they are skipped elsewhere.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="pynput's Darwin keycode-context backend only exists on macOS",
)


def test_apply_workaround_patches_keycode_context() -> None:
    import obs_snooker.darwin_keycode_patch as patch_module

    # Reset module-level idempotency guard so this test is independent of
    # test execution order / other tests already having applied it.
    patch_module._patched = False

    from pynput.keyboard import _darwin as darwin_keyboard

    original_keycode_context = darwin_keyboard.keycode_context
    try:
        applied = patch_module.apply_macos_keycode_context_workaround()

        assert applied is True
        assert darwin_keyboard.keycode_context is not original_keycode_context
    finally:
        darwin_keyboard.keycode_context = original_keycode_context
        patch_module._patched = False


def test_apply_workaround_is_idempotent() -> None:
    import obs_snooker.darwin_keycode_patch as patch_module

    patch_module._patched = False
    from pynput.keyboard import _darwin as darwin_keyboard

    original_keycode_context = darwin_keyboard.keycode_context
    try:
        first = patch_module.apply_macos_keycode_context_workaround()
        patched_context = darwin_keyboard.keycode_context
        second = patch_module.apply_macos_keycode_context_workaround()

        assert first is True
        assert second is True
        # Calling it again must not re-patch (same object as after first call).
        assert darwin_keyboard.keycode_context is patched_context
    finally:
        darwin_keyboard.keycode_context = original_keycode_context
        patch_module._patched = False


def test_cached_context_is_reusable_context_manager() -> None:
    import obs_snooker.darwin_keycode_patch as patch_module

    patch_module._patched = False
    from pynput.keyboard import _darwin as darwin_keyboard

    original_keycode_context = darwin_keyboard.keycode_context
    try:
        patch_module.apply_macos_keycode_context_workaround()

        # The patched context manager must be usable more than once (this is
        # exactly what happens every time `Listener._run()` starts) without
        # re-invoking any Carbon/TSM calls.
        with darwin_keyboard.keycode_context() as context_a:
            keyboard_type_a, layout_data_a = context_a
        with darwin_keyboard.keycode_context() as context_b:
            keyboard_type_b, layout_data_b = context_b

        assert keyboard_type_a == keyboard_type_b
        assert layout_data_a == layout_data_b
    finally:
        darwin_keyboard.keycode_context = original_keycode_context
        patch_module._patched = False
