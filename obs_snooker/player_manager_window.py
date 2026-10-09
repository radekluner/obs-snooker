"""Standalone Tkinter window for managing the player database (add/rename/delete).

Like ``obs_snooker.scoreboard_window``, this is deliberately **not** created
inside OBS's own process - on macOS, embedding any Tk widget directly inside
OBS's interpreter can crash OBS itself (Tk's Cocoa backend conflicts with the
``NSApplication`` instance OBS, a Qt/Cocoa app, already runs). Instead,
``obs-snooker.py`` launches this module as a separate OS process via
``python -m obs_snooker.player_manager_window --db <path>``, exactly like it
does for the scoreboard window. See ``AGENTS.md`` for the full explanation.

Unlike the scoreboard window, this window owns the player database file
directly (there is no "live match in progress" to poll) - it reads it on
startup and writes to it immediately on every add/rename/delete, via
``obs_snooker.player_database.PlayerDatabase``. The OBS script re-reads the
same file (via its own ``PlayerDatabase`` instance) whenever its properties
panel is opened or refreshed, so changes made here show up there without
either process needing to talk to the other directly.
"""

from __future__ import annotations

import argparse
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .player_database import PlayerDatabase, default_players_file_path

_BG = "#05070a"
_PANEL_BG = "#10151c"
_GOLD = "#d4af37"
_TEXT = "#f2f2f2"


class PlayerManagerWindow:
    """A small CRUD window: a list of players plus name/country fields and buttons."""

    def __init__(self, db: PlayerDatabase) -> None:
        self.db = db
        self._selected_player_id: str | None = None

        self.root = tk.Tk()
        self.root.title("Snooker scoreboard - manage players")
        self.root.configure(bg=_BG)
        self.root.geometry("480x420")

        self._build_widgets()
        self._refresh_list()

    def _build_widgets(self) -> None:
        container = tk.Frame(self.root, bg=_BG, padx=12, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        list_frame = tk.Frame(container, bg=_BG)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(
            list_frame, bg=_PANEL_BG, fg=_TEXT, selectbackground=_GOLD, selectforeground=_BG,
            activestyle="none", highlightthickness=0, borderwidth=0,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        form_frame = tk.Frame(container, bg=_BG, pady=10)
        form_frame.pack(fill=tk.X)

        tk.Label(form_frame, text="Name:", bg=_BG, fg=_TEXT).grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        tk.Entry(form_frame, textvariable=self.name_var).grid(row=0, column=1, sticky="we", padx=6)

        tk.Label(form_frame, text="Country code:", bg=_BG, fg=_TEXT).grid(row=1, column=0, sticky="w")
        self.country_var = tk.StringVar()
        tk.Entry(form_frame, textvariable=self.country_var, width=10).grid(
            row=1, column=1, sticky="w", padx=6
        )

        form_frame.columnconfigure(1, weight=1)

        button_frame = tk.Frame(container, bg=_BG, pady=6)
        button_frame.pack(fill=tk.X)

        tk.Button(button_frame, text="Add", command=self._on_add).pack(side=tk.LEFT, padx=4)
        tk.Button(button_frame, text="Save changes", command=self._on_update).pack(side=tk.LEFT, padx=4)
        tk.Button(button_frame, text="Delete", command=self._on_delete).pack(side=tk.LEFT, padx=4)
        tk.Button(button_frame, text="Clear form", command=self._clear_form).pack(side=tk.LEFT, padx=4)
        tk.Button(button_frame, text="Close", command=self.root.destroy).pack(side=tk.RIGHT, padx=4)

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for player in self.db.list_players():
            self.listbox.insert(tk.END, player.display_name())
        self._players_by_row = self.db.list_players()

    def _on_select(self, _event: object = None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        player = self._players_by_row[selection[0]]
        self._selected_player_id = player.id
        self.name_var.set(player.name)
        self.country_var.set(player.country)

    def _clear_form(self) -> None:
        self._selected_player_id = None
        self.name_var.set("")
        self.country_var.set("")
        self.listbox.selection_clear(0, tk.END)

    def _on_add(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Missing name", "Enter a player name first.")
            return
        self.db.add_player(name, country=self.country_var.get().strip())
        self._clear_form()
        self._refresh_list()

    def _on_update(self) -> None:
        if self._selected_player_id is None:
            messagebox.showwarning("No player selected", "Select a player in the list first.")
            return
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Missing name", "Enter a player name first.")
            return
        self.db.update_player(self._selected_player_id, name=name, country=self.country_var.get().strip())
        self._clear_form()
        self._refresh_list()

    def _on_delete(self) -> None:
        if self._selected_player_id is None:
            messagebox.showwarning("No player selected", "Select a player in the list first.")
            return
        self.db.remove_player(self._selected_player_id)
        self._clear_form()
        self._refresh_list()

    def mainloop(self) -> None:
        self.root.mainloop()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Manage the snooker player database.")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to the players.json database file (default: players.json next to obs-snooker.py).",
    )
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        project_root = Path(__file__).resolve().parent.parent
        db_path = default_players_file_path(str(project_root))

    db = PlayerDatabase(db_path)
    window = PlayerManagerWindow(db)
    window.mainloop()


if __name__ == "__main__":
    _main()
