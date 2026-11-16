"""Saves/restores the in-progress part of a match so it survives an OBS crash or restart.

Only the *progress* is stored (frames won, frame scores, current break and
its balls, whose turn it is, the announced match status and its note); the match configuration (players, title, race
length) is already persisted by OBS in the script settings. A single small
JSON file is used, written atomically. No OBS dependency.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .match_state import BALL_VALUES, MATCH_STATUSES, STATUS_NOTE_MAX_LENGTH, MatchState


def progress_snapshot(state: MatchState) -> dict[str, Any]:
    return {
        "player1_frames": state.player1_frames,
        "player2_frames": state.player2_frames,
        "player1_frame_score": state.player1_frame_score,
        "player2_frame_score": state.player2_frame_score,
        "current_player": state.current_player,
        "current_break": state.current_break,
        "break_balls": list(state.break_balls),
        "match_status": state.match_status,
        "status_note": state.status_note,
    }


def save_progress(state: MatchState, path: str) -> dict[str, Any]:
    """Writes the progress atomically and returns the snapshot that was written."""
    snapshot = progress_snapshot(state)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f)
    os.replace(tmp_path, path)
    return snapshot


def load_progress(state: MatchState, path: str) -> bool:
    """Restores saved progress into ``state``; returns False if there is nothing usable (missing/corrupt file)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        numbers = {
            key: max(0, int(data[key]))
            for key in (
                "player1_frames", "player2_frames", "player1_frame_score", "player2_frame_score", "current_break",
            )
        }
        current_player = int(data["current_player"])
        balls = [b for b in data.get("break_balls", []) if b in BALL_VALUES]
        # Optional: files saved before the match status existed simply lack these keys.
        status = data.get("match_status", "live")
        note = data.get("status_note", "")
    except (OSError, ValueError, KeyError, TypeError):
        return False
    state.player1_frames = numbers["player1_frames"]
    state.player2_frames = numbers["player2_frames"]
    state.player1_frame_score = numbers["player1_frame_score"]
    state.player2_frame_score = numbers["player2_frame_score"]
    state.current_break = numbers["current_break"]
    state.break_balls = balls
    state.current_player = current_player if current_player in (1, 2) else 1
    state.match_status = status if status in MATCH_STATUSES else "live"
    state.status_note = note.strip()[:STATUS_NOTE_MAX_LENGTH] if isinstance(note, str) else ""
    return True
