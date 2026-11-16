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

from tkinter import font as tkfont

# Same modern dark theme as the scoreboard window (see scoreboard_window.py).
_BG = "#0c1433"
_PANEL_BG = "#080d26"
_EDGE = "#273266"
_BUTTON_BG = "#1c2654"
_BUTTON_HOVER = "#26326a"
_ACCENT = "#97bf26"
_ACCENT_HOVER = "#a8d12f"
_ACCENT_TEXT = "#0c1a02"
_TEXT = "#ffffff"
_MUTED_TEXT = "#8a95c2"
_UI_FONTS = ("Avenir Next", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial")


class PlayerManagerWindow:
    """A small CRUD window: a list of players plus name/country fields and buttons."""

    def __init__(self, db: PlayerDatabase) -> None:
        self.db = db
        self._selected_player_id: str | None = None

        self.root = tk.Tk()
        self.root.title("Snooker scoreboard - manage players")
        self.root.configure(bg=_BG)
        self.root.geometry("520x480")

        installed = set(tkfont.families(self.root))
        family = next((f for f in _UI_FONTS if f in installed), "Helvetica")
        self._font = tkfont.Font(family=family, size=13)
        self._bold_font = tkfont.Font(family=family, size=13, weight="bold")
        self._label_font = tkfont.Font(family=family, size=10, weight="bold")

        self._build_widgets()
        self._refresh_list()

    def _button(self, parent: tk.Widget, text: str, command, primary: bool = False) -> tk.Label:
        """A flat, colorable button.

        Built from a ``Label`` because ``tk.Button`` on macOS (Aqua) ignores
        background colors and always draws the native grey button.
        """
        bg, hover, fg = (_ACCENT, _ACCENT_HOVER, _ACCENT_TEXT) if primary else (_BUTTON_BG, _BUTTON_HOVER, _TEXT)
        button = tk.Label(
            parent, text=text, bg=bg, fg=fg, font=self._bold_font, padx=14, pady=7, cursor="hand2",
            highlightthickness=1, highlightbackground=bg if primary else _EDGE,
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=hover))
        button.bind("<Leave>", lambda _e: button.configure(bg=bg))
        button.bind("<Button-1>", lambda _e: command())
        return button

    def _entry(self, parent: tk.Widget, variable: tk.StringVar, **kwargs: object) -> tk.Entry:
        return tk.Entry(
            parent, textvariable=variable, bg=_PANEL_BG, fg=_TEXT, insertbackground=_TEXT, font=self._font,
            relief=tk.FLAT, highlightthickness=1, highlightbackground=_EDGE, highlightcolor=_ACCENT, **kwargs,
        )

    def _build_widgets(self) -> None:
        container = tk.Frame(self.root, bg=_BG, padx=16, pady=16)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(container, text="P L A Y E R S", bg=_BG, fg=_MUTED_TEXT, font=self._label_font).pack(anchor="w", pady=(0, 6))

        list_frame = tk.Frame(container, bg=_PANEL_BG, highlightthickness=1, highlightbackground=_EDGE)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(
            list_frame, bg=_PANEL_BG, fg=_TEXT, selectbackground=_ACCENT, selectforeground=_ACCENT_TEXT,
            activestyle="none", highlightthickness=0, borderwidth=0, font=self._font,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=6)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        form_frame = tk.Frame(container, bg=_BG, pady=14)
        form_frame.pack(fill=tk.X)

        tk.Label(form_frame, text="NAME", bg=_BG, fg=_MUTED_TEXT, font=self._label_font).grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar()
        self._entry(form_frame, self.name_var).grid(row=0, column=1, sticky="we", padx=(10, 0), pady=4, ipady=5)

        tk.Label(form_frame, text="COUNTRY CODE", bg=_BG, fg=_MUTED_TEXT, font=self._label_font).grid(row=1, column=0, sticky="w")
        self.country_var = tk.StringVar()
        self._entry(form_frame, self.country_var, width=10).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=4, ipady=5)

        form_frame.columnconfigure(1, weight=1)

        button_frame = tk.Frame(container, bg=_BG)
        button_frame.pack(fill=tk.X)

        self._button(button_frame, "Add", self._on_add, primary=True).pack(side=tk.LEFT, padx=(0, 6))
        self._button(button_frame, "Save changes", self._on_update).pack(side=tk.LEFT, padx=6)
        self._button(button_frame, "Delete", self._on_delete).pack(side=tk.LEFT, padx=6)
        self._button(button_frame, "Clear form", self._clear_form).pack(side=tk.LEFT, padx=6)
        self._button(button_frame, "Close", self.root.destroy).pack(side=tk.RIGHT)

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
