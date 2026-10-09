"""Tests for the standalone player-manager window.

Same display/Tkinter requirements as ``test_scoreboard_window.py`` - skipped
automatically when Tkinter isn't installed or no usable display is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from obs_snooker.player_database import PlayerDatabase  # noqa: E402
from obs_snooker.player_manager_window import PlayerManagerWindow  # noqa: E402


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


def test_window_lists_existing_players(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    db.add_player("Judd Trump", country="ENG")
    db.add_player("Ding Junhui", country="CHN")

    window = PlayerManagerWindow(db)
    try:
        items = list(window.listbox.get(0, tk.END))
        assert items == ["Ding Junhui (CHN)", "Judd Trump (ENG)"]
    finally:
        window.root.destroy()


def test_add_player_via_form_updates_database_and_list(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    window = PlayerManagerWindow(db)
    try:
        window.name_var.set("Neil Robertson")
        window.country_var.set("AUS")
        window._on_add()

        assert [p.name for p in db.list_players()] == ["Neil Robertson"]
        assert list(window.listbox.get(0, tk.END)) == ["Neil Robertson (AUS)"]
        # Form is cleared after a successful add.
        assert window.name_var.get() == ""
    finally:
        window.root.destroy()


def test_select_then_update_player(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    player = db.add_player("Mark Selby", country="ENG")

    window = PlayerManagerWindow(db)
    try:
        window.listbox.selection_set(0)
        window._on_select()
        assert window.name_var.get() == "Mark Selby"
        assert window.country_var.get() == "ENG"

        window.name_var.set("Mark Selby MBE")
        window._on_update()

        updated = db.get_player(player.id)
        assert updated is not None
        assert updated.name == "Mark Selby MBE"
    finally:
        window.root.destroy()


def test_select_then_delete_player(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    player = db.add_player("Kyren Wilson", country="ENG")

    window = PlayerManagerWindow(db)
    try:
        window.listbox.selection_set(0)
        window._on_select()
        window._on_delete()

        assert db.get_player(player.id) is None
        assert list(window.listbox.get(0, tk.END)) == []
    finally:
        window.root.destroy()


def test_add_without_name_shows_warning_and_does_not_add(tmp_path: Path, monkeypatch) -> None:
    from obs_snooker import player_manager_window as module

    warnings = []
    monkeypatch.setattr(module.messagebox, "showwarning", lambda title, message: warnings.append((title, message)))

    db = PlayerDatabase(str(tmp_path / "players.json"))
    window = PlayerManagerWindow(db)
    try:
        window.name_var.set("   ")
        window._on_add()

        assert db.list_players() == []
        assert len(warnings) == 1
    finally:
        window.root.destroy()
