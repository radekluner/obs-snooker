"""Tests for the standalone scoreboard window.

These tests require a working Tkinter/Tcl-Tk installation with access to a
display (or a virtual one, e.g. Xvfb on Linux CI). They are skipped
automatically when Tkinter is not installed, or when it fails to open a
window (e.g. no display / broken Tcl installation), so the rest of the test
suite (``test_match_state.py``) keeps working in headless environments.
"""

import pytest

tk = pytest.importorskip("tkinter")

from obs_snooker.match_state import MatchState  # noqa: E402
from obs_snooker.scoreboard_window import (  # noqa: E402
    ScoreboardWindow,
    _name_font_size,
    _state_from_payload,
)


def _tk_display_available() -> bool:
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


pytestmark = pytest.mark.skipif(
    not _tk_display_available(),
    reason="No usable Tkinter/Tcl-Tk display in this environment",
)


def test_window_reflects_initial_state():
    state = MatchState(
        player1_name="Alice",
        player2_name="Bob",
        player1_frames=3,
        player2_frames=1,
        player1_frame_score=45,
        current_player=1,
        frames_to_win=10,
    )
    window = ScoreboardWindow(state)
    try:
        window.pump()
        assert window.player1_name_var.get() == "ALICE"
        assert window.player2_name_var.get() == "BOB"
        assert window.player1_frames_var.get() == "3"
        assert window.player2_frames_var.get() == "1"
        assert "45" in window.player1_frame_score_var.get()
        assert "Race to 10" in window.race_label_var.get()
    finally:
        window.destroy()


def test_update_from_state_refreshes_labels():
    state = MatchState()
    window = ScoreboardWindow(state)
    try:
        state.add_frame_points(1, 2)
        window.update_from_state(state)
        window.pump()
        assert "2" in window.player1_frame_score_var.get()
    finally:
        window.destroy()


def test_update_from_state_highlights_current_player():
    state = MatchState(current_player=1)
    window = ScoreboardWindow(state)
    try:
        window.pump()
        assert window.current_player == 1

        state.switch_turn()
        window.update_from_state(state)
        window.pump()
        assert window.current_player == 2
    finally:
        window.destroy()


def test_pump_returns_false_after_destroy():
    state = MatchState()
    window = ScoreboardWindow(state)
    window.destroy()
    assert window.pump() is False


def test_state_from_payload_builds_matching_match_state():
    payload = {
        "player1_name": "Alice",
        "player2_name": "Bob",
        "player1_frames": 4,
        "player2_frames": 2,
        "player1_frame_score": 30,
        "player2_frame_score": 15,
        "frames_to_win": 10,
        "current_player": 2,
        "match_title": "Test Cup",
    }
    state = _state_from_payload(payload)
    assert state == MatchState(**payload)


def test_state_from_payload_fills_in_defaults_for_missing_keys():
    state = _state_from_payload({})
    assert state == MatchState()


def test_name_font_size_is_base_size_for_short_names():
    assert _name_font_size("Judd Trump") == 66


def test_name_font_size_shrinks_for_long_names():
    assert _name_font_size("Rostyslav Bovonenko") == round(66 * 0.75)


def test_name_font_size_shrinks_more_for_very_long_names():
    assert _name_font_size("Mykhailo Kusen Diadora") == round(66 * 0.55)


def test_name_font_size_respects_custom_base_size():
    assert _name_font_size("Judd Trump", base_size=100) == 100
    assert _name_font_size("Mykhailo Kusen Diadora", base_size=100) == 55


def test_long_player_name_shrinks_font_for_both_players_equally():
    state = MatchState(player1_name="Mykhailo Kusen Diadora", player2_name="Qu Rao")
    window = ScoreboardWindow(state)
    try:
        for _ in range(10):
            window.pump()
        window._render()
        assert window.player1_name_font.cget("size") == window.player2_name_font.cget("size")
        assert window.player1_name_font.cget("size") == -max(6, round(_name_font_size("Mykhailo Kusen Diadora") * window._scale))
    finally:
        window.destroy()


def _png(color, size=(64, 36)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_foul_view_shows_and_hides_screenshots():
    pytest.importorskip("PIL")
    window = ScoreboardWindow(MatchState())
    try:
        window.root.geometry("800x450")
        window.pump()
        window.set_foul("reference", _png("red"))
        window.pump()
        assert window.foul_mode == "reference"
        assert window._foul_photo is not None
        window.set_foul("compare", _png("red"), _png("blue"), opacity=50)
        window.pump()
        assert window.foul_mode == "compare"
        assert window._foul_photo is not None
        window.set_foul("off")
        window.pump()
        assert window.foul_mode == "off"
    finally:
        window.destroy()
