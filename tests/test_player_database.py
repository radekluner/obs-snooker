"""Tests for `obs_snooker.player_database` - no OBS or GUI dependency."""

from __future__ import annotations

from pathlib import Path

import pytest

from obs_snooker.player_database import Player, PlayerDatabase, default_players_file_path


def test_default_players_file_path_is_sibling_of_script_dir() -> None:
    assert default_players_file_path("/some/dir") == str(Path("/some/dir") / "players.json")


def test_new_database_with_missing_file_is_empty(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    assert db.list_players() == []


def test_add_player_persists_and_is_retrievable(tmp_path: Path) -> None:
    db_path = tmp_path / "players.json"
    db = PlayerDatabase(str(db_path))

    player = db.add_player("Ronnie O'Sullivan", country="ENG")

    assert db_path.exists()
    assert db.get_player(player.id) == player
    assert player.name == "Ronnie O'Sullivan"
    assert player.country == "ENG"


def test_add_player_without_country_defaults_to_empty_string(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    player = db.add_player("Judd Trump")
    assert player.country == ""


def test_list_players_sorted_case_insensitively_by_name(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    db.add_player("zack")
    db.add_player("Alice")
    db.add_player("bob")

    names = [p.name for p in db.list_players()]
    assert names == ["Alice", "bob", "zack"]


def test_list_players_sorted_by_surname_not_first_name(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    db.add_player("Ronnie O'Sullivan")
    db.add_player("Judd Trump")
    db.add_player("Mark Selby")
    db.add_player("Neil Robertson")

    # Sorted by surname (O'Sullivan, Robertson, Selby, Trump), not by first name.
    names = [p.name for p in db.list_players()]
    assert names == ["Ronnie O'Sullivan", "Neil Robertson", "Mark Selby", "Judd Trump"]


def test_list_players_surname_sort_is_case_insensitive(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    db.add_player("Jan zebra")
    db.add_player("Petr Alfa")

    names = [p.name for p in db.list_players()]
    assert names == ["Petr Alfa", "Jan zebra"]


def test_update_player_changes_name_and_country(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    player = db.add_player("Mark Selby", country="ENG")

    db.update_player(player.id, name="Mark Selby Jr.", country="WAL")

    updated = db.get_player(player.id)
    assert updated is not None
    assert updated.name == "Mark Selby Jr."
    assert updated.country == "WAL"


def test_update_player_with_unknown_id_raises_key_error(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    with pytest.raises(KeyError):
        db.update_player("does-not-exist", name="Someone")


def test_remove_player_deletes_it(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    player = db.add_player("Neil Robertson", country="AUS")

    db.remove_player(player.id)

    assert db.get_player(player.id) is None
    assert db.list_players() == []


def test_remove_player_with_unknown_id_is_a_no_op(tmp_path: Path) -> None:
    db = PlayerDatabase(str(tmp_path / "players.json"))
    db.remove_player("does-not-exist")  # should not raise


def test_changes_persist_across_separate_database_instances(tmp_path: Path) -> None:
    db_path = str(tmp_path / "players.json")
    db1 = PlayerDatabase(db_path)
    db1.add_player("Kyren Wilson", country="ENG")

    db2 = PlayerDatabase(db_path)
    names = [p.name for p in db2.list_players()]
    assert names == ["Kyren Wilson"]


def test_corrupt_file_is_treated_as_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "players.json"
    db_path.write_text("{ not valid json", encoding="utf-8")

    db = PlayerDatabase(str(db_path))

    assert db.list_players() == []


def test_player_display_name_includes_country_when_set() -> None:
    player = Player(id="abc", name="Ding Junhui", country="CHN")
    assert player.display_name() == "Ding Junhui (CHN)"


def test_player_display_name_without_country() -> None:
    player = Player(id="abc", name="Ding Junhui")
    assert player.display_name() == "Ding Junhui"
