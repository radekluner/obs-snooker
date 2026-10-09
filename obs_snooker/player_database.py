"""Local JSON-file database of players (name + country/nationality code).

Lets the OBS script offer a "pick Player 1 / Player 2 from a list" dropdown
instead of typing names by hand every match, while the actual management
(add/rename/delete) happens in a separate window
(``obs_snooker.player_manager_window``).

This module has no dependency on OBS or any GUI toolkit, so it can be
imported and unit-tested in isolation - and deliberately uses only the
``json``/``pathlib`` standard-library modules (no ``dataclasses``,
``sqlite3``, ...), since some OBS-bundled Python distributions have been
observed to ship a reduced standard library missing modules that would
otherwise seem like safe, obvious choices (see ``AGENTS.md``).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path


def _surname(name: str) -> str:
    """Best-effort extraction of a surname from a "First [Middle] Last" name.

    Takes the last whitespace-separated token, e.g. "Ronnie O'Sullivan" ->
    "O'Sullivan", "Jan Matějíček" -> "Matějíček". Falls back to the full
    (stripped) name if it has no spaces, so single-word names still sort
    sensibly instead of raising or sorting as empty.
    """
    parts = name.strip().split()
    return parts[-1] if parts else name


class Player:
    """A single entry in the player database.

    ``id`` is a stable identifier, independent of ``name``, so renaming a
    player doesn't break anything that already referred to them (e.g. a
    previously saved "Player 1" selection in the OBS script's settings).
    """

    def __init__(self, id: str, name: str, country: str = "") -> None:  # noqa: A002
        self.id = id
        self.name = name
        self.country = country

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Player):
            return NotImplemented
        return self.__dict__ == other.__dict__

    def __repr__(self) -> str:
        return f"Player(id={self.id!r}, name={self.name!r}, country={self.country!r})"

    def display_name(self) -> str:
        """Human-readable label for dropdowns/lists, e.g. 'Ronnie O'Sullivan (ENG)'."""
        if self.country:
            return f"{self.name} ({self.country})"
        return self.name

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "country": self.country}

    @classmethod
    def from_dict(cls, data: dict) -> Player:
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            country=data.get("country", ""),
        )


def default_players_file_path(script_dir: str) -> str:
    """The default location of the players database, next to the OBS script."""
    return str(Path(script_dir) / "players.json")


class PlayerDatabase:
    """A small CRUD store for `Player` records, persisted as a JSON file.

    Every mutating method (`add_player`, `update_player`, `remove_player`)
    saves immediately, so callers never need to remember to call `save()`
    themselves.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._players: dict[str, Player] = {}
        self.load()

    def load(self) -> None:
        """(Re-)loads the database from disk.

        Missing or corrupt files are treated as an empty database rather
        than raising, since a brand new install won't have this file yet,
        and a hand-edited/corrupted file shouldn't prevent the rest of the
        script from loading.
        """
        self._players = {}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return

        try:
            data = json.loads(raw)
            entries = data.get("players", [])
            for entry in entries:
                player = Player.from_dict(entry)
                self._players[player.id] = player
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
            print(f"[obs_snooker] Could not parse player database at {self.path}, treating it as empty.")
            self._players = {}

    def save(self) -> None:
        """Writes the current in-memory players to disk as JSON."""
        payload = {"players": [player.to_dict() for player in self._players.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_players(self) -> list[Player]:
        """Returns all players, sorted by surname (case-insensitive), then full name.

        Snooker rankings and most roster UIs are scanned by surname, so e.g.
        "Ronnie O'Sullivan" sorts under "O" rather than "R". The surname is
        taken as the last whitespace-separated token of `name` - a simple
        heuristic that works for the common "First Last" naming convention
        this project otherwise assumes, without needing separate first/last
        name fields.
        """
        return sorted(self._players.values(), key=lambda p: (_surname(p.name).casefold(), p.name.casefold()))

    def get_player(self, player_id: str) -> Player | None:
        return self._players.get(player_id)

    def add_player(self, name: str, country: str = "") -> Player:
        player = Player(id=uuid.uuid4().hex[:8], name=name, country=country)
        self._players[player.id] = player
        self.save()
        return player

    def update_player(self, player_id: str, name: str | None = None, country: str | None = None) -> None:
        player = self._players.get(player_id)
        if player is None:
            raise KeyError(f"No player with id {player_id!r}")
        if name is not None:
            player.name = name
        if country is not None:
            player.country = country
        self.save()

    def remove_player(self, player_id: str) -> None:
        if player_id in self._players:
            del self._players[player_id]
            self.save()
