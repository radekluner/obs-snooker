from obs_snooker.match_state import MatchState
from obs_snooker.state_store import load_progress, progress_snapshot, save_progress


def test_progress_roundtrip(tmp_path):
    path = str(tmp_path / "match_state.json")
    state = MatchState(frames_to_win=5)
    state.player1_frames, state.player2_frames = 2, 1
    state.add_break_points(1, "red")
    state.add_break_points(7, "black")
    state.switch_turn()
    state.add_break_points(5, "blue")
    save_progress(state, path)

    restored = MatchState(frames_to_win=5)
    assert load_progress(restored, path)
    assert progress_snapshot(restored) == progress_snapshot(state)


def test_missing_or_corrupt_file_is_ignored(tmp_path):
    state = MatchState()
    assert not load_progress(state, str(tmp_path / "nope.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert not load_progress(state, str(bad))
    bad.write_text('{"player1_frames": "x"}')
    assert not load_progress(state, str(bad))
    assert state.player1_frames == 0


def test_invalid_values_are_sanitized(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(
        '{"player1_frames": -3, "player2_frames": 2, "player1_frame_score": 10, "player2_frame_score": 4,'
        ' "current_player": 9, "current_break": 5, "break_balls": ["red", "purple"]}'
    )
    state = MatchState()
    assert load_progress(state, str(path))
    assert state.player1_frames == 0
    assert state.current_player == 1
    assert state.break_balls == ["red"]
