"""Match state and snooker scoring logic.

Models the pieces of information a real snooker scoreboard needs to track:

- The **match score**: how many frames each player has won.
- The **frame score**: points scored by each player within the frame that is
  currently being played (reset to 0-0 at the start of every new frame).
- How many frame wins are needed to win the match (``frames_to_win``, e.g.
  ``10`` for a "best of 19 frames" final).
- Which player currently has the "current_player" indicator (Trump/the
  scorer typically presses a hotkey/button when the players' turn changes,
  and after a frame ends the break is assumed to alternate).

This module has no dependency on OBS or any GUI toolkit, so it can be
imported and unit-tested in isolation.
"""

from __future__ import annotations


BALL_VALUES: dict[str, int] = {
    "red": 1,
    "yellow": 2,
    "green": 3,
    "brown": 4,
    "blue": 5,
    "pink": 6,
    "black": 7,
}


def _other_player(player: int) -> int:
    return 2 if player == 1 else 1


class MatchState:
    """Holds all the data displayed by the OBS overlays and the scoreboard window.

    Implemented as a plain class (not ``dataclasses.dataclass``) because some
    OBS-bundled Python distributions ship a reduced standard library that is
    missing the ``dataclasses`` module even though the interpreter itself is
    new enough to normally include it.
    """

    def __init__(
        self,
        player1_name: str = "Player 1",
        player2_name: str = "Player 2",
        player1_country: str = "",
        player2_country: str = "",
        player1_frames: int = 0,
        player2_frames: int = 0,
        player1_frame_score: int = 0,
        player2_frame_score: int = 0,
        frames_to_win: int = 1,
        current_player: int = 1,
        match_title: str = "",
        current_break: int = 0,
        break_balls: list[str] | None = None,
    ) -> None:
        self.player1_name = player1_name
        self.player2_name = player2_name

        # Optional short country/nationality code (e.g. "ENG", "CHN"), looked
        # up from the player database (`obs_snooker.player_database`).
        # Currently only carried through for future use (e.g. a flag icon in
        # the overlay); not yet rendered anywhere.
        self.player1_country = player1_country
        self.player2_country = player2_country

        # Frames won so far - this is the actual match score.
        self.player1_frames = player1_frames
        self.player2_frames = player2_frames

        # Points scored by each player within the frame currently in progress.
        self.player1_frame_score = player1_frame_score
        self.player2_frame_score = player2_frame_score

        # Number of frame wins needed to win the match (e.g. 10 for a
        # "best of 19" World Championship final). Must be >= 1.
        self.frames_to_win = frames_to_win

        # Which player (1 or 2) currently has the "on the table" / turn indicator.
        self.current_player = current_player

        # Points potted by the current player in their current visit to the
        # table (the "break") that are not yet part of their frame score.
        # They are added to the frame score when the turn changes
        # (see :meth:`commit_break`) and discarded when a frame ends.
        self.current_break = current_break

        # Balls potted in the pending break, in order (names from BALL_VALUES);
        # only used for showing what the break consists of.
        self.break_balls: list[str] = list(break_balls or [])

        # Optional label shown above the score in the browser overlay, e.g.
        # "World Championship - Final". Left blank by default (hidden).
        self.match_title = match_title

        if self.frames_to_win < 1:
            raise ValueError(f"frames_to_win must be >= 1, got {self.frames_to_win!r}")
        if self.current_player not in (1, 2):
            raise ValueError(f"current_player must be 1 or 2, got {self.current_player!r}")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MatchState):
            return NotImplemented
        return self.__dict__ == other.__dict__

    def __repr__(self) -> str:
        fields = ", ".join(f"{key}={value!r}" for key, value in self.__dict__.items())
        return f"MatchState({fields})"

    @property
    def best_of(self) -> int:
        """The maximum number of frames the match could take (e.g. 19 for a race to 10)."""
        return 2 * self.frames_to_win - 1

    def add_frame_points(self, player: int, delta: int = 1) -> None:
        """Adds (or subtracts) points to a player's current-frame score, clamped at 0."""
        if player not in (1, 2):
            raise ValueError(f"player must be 1 or 2, got {player!r}")
        attr = f"player{player}_frame_score"
        setattr(self, attr, max(0, getattr(self, attr) + delta))

    def add_break_points(self, delta: int, ball: str | None = None) -> None:
        """Adds (or subtracts) points to the current player's pending break, clamped at 0.

        Pass ``ball`` (a name from ``BALL_VALUES``) to record which ball was
        potted. A negative ``delta`` is an undo: it removes the last recorded
        ball (and its value) if there is one, otherwise it only subtracts points.
        """
        if ball is not None:
            self.break_balls.append(ball)
        elif delta < 0 and self.break_balls:
            delta = -BALL_VALUES.get(self.break_balls.pop(), -delta)
        self.current_break = max(0, self.current_break + delta)

    def clear_break(self) -> None:
        """Discards the pending break and its recorded balls."""
        self.current_break = 0
        self.break_balls = []

    def commit_break(self) -> None:
        """Adds the pending break to the current player's frame score and clears it."""
        if self.current_break:
            self.add_frame_points(self.current_player, self.current_break)
        self.clear_break()

    def set_frame_score(self, player: int, value: int) -> None:
        """Directly sets a player's current-frame score (clamped at 0).

        Intended for manually correcting a scoring mistake (e.g. a
        mis-clicked "+points" button during a fast-moving break), as an
        alternative to nudging the score with repeated :meth:`add_frame_points`
        calls.
        """
        if player not in (1, 2):
            raise ValueError(f"player must be 1 or 2, got {player!r}")
        setattr(self, f"player{player}_frame_score", max(0, value))

    def set_frames(self, player: int, value: int) -> None:
        """Directly sets a player's frames-won tally (clamped at 0).

        Intended for manually correcting a mistake (e.g. an accidental
        "wins frame" click, or fixing the score after resuming a match that
        was tracked on paper). Unlike :meth:`win_frame`, this does not reset
        the in-progress frame score or alternate the turn indicator, since a
        manual correction shouldn't have those side effects.
        """
        if player not in (1, 2):
            raise ValueError(f"player must be 1 or 2, got {player!r}")
        setattr(self, f"player{player}_frames", max(0, value))

    def set_current_player(self, player: int) -> None:
        """Explicitly sets who is currently at the table."""
        if player not in (1, 2):
            raise ValueError(f"player must be 1 or 2, got {player!r}")
        if player != self.current_player:
            self.commit_break()
        self.current_player = player

    def switch_turn(self) -> None:
        """Toggles which player currently has the "on the table" indicator."""
        self.commit_break()
        self.current_player = _other_player(self.current_player)

    def reset_frame_scores(self) -> None:
        """Resets both players' current-frame points to zero (e.g. a re-rack), keeping frames won."""
        self.player1_frame_score = 0
        self.player2_frame_score = 0
        self.clear_break()

    def win_frame(self, player: int) -> None:
        """Awards the current frame to ``player`` (1 or 2).

        Increments that player's frame tally, resets both players' in-frame
        points to 0-0 for the next frame, and alternates the "on the table"
        indicator, since the break alternates between frames in snooker.

        Does nothing once the match is already won (see :meth:`winner`) -
        otherwise repeatedly pressing the "wins frame" button (e.g. by
        accident) would keep incrementing a player's frame tally past
        ``frames_to_win``, which isn't a valid snooker score.
        """
        if player not in (1, 2):
            raise ValueError(f"player must be 1 or 2, got {player!r}")
        if self.winner() is not None:
            return
        attr = f"player{player}_frames"
        setattr(self, attr, getattr(self, attr) + 1)
        self.reset_frame_scores()
        self.switch_turn()

    def reset_match(self) -> None:
        """Resets frames won, in-frame points, and the turn indicator for a brand new match.

        Player names, ``frames_to_win`` and ``match_title`` are left
        untouched, since those are match *configuration* rather than
        in-progress state.
        """
        self.player1_frames = 0
        self.player2_frames = 0
        self.reset_frame_scores()
        self.current_player = 1

    def winner(self) -> int | None:
        """Returns the winning player number (1 or 2) once someone has reached ``frames_to_win``."""
        if self.player1_frames >= self.frames_to_win:
            return 1
        if self.player2_frames >= self.frames_to_win:
            return 2
        return None

    def as_overlay_text(self) -> str:
        """Renders a single-line summary suitable for the plain OBS text overlay."""
        turn1 = ">" if self.current_player == 1 else ""
        turn2 = "<" if self.current_player == 2 else ""
        summary = (
            f"{turn1}{self.player1_name} {self.player1_frames} "
            f"({self.player1_frame_score}) - ({self.player2_frame_score}) "
            f"{self.player2_frames} {self.player2_name}{turn2}"
        )
        if self.match_title:
            return f"{self.match_title} | {summary}"
        return summary
