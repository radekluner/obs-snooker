import pytest

from obs_snooker.match_state import MatchState


def test_default_state():
    state = MatchState()
    assert state.player1_name == "Player 1"
    assert state.player2_name == "Player 2"
    assert state.player1_frames == 0
    assert state.player2_frames == 0
    assert state.player1_frame_score == 0
    assert state.player2_frame_score == 0
    assert state.frames_to_win == 1
    assert state.current_player == 1


def test_best_of_is_derived_from_frames_to_win():
    assert MatchState(frames_to_win=10).best_of == 19
    assert MatchState(frames_to_win=1).best_of == 1


def test_invalid_frames_to_win_raises():
    with pytest.raises(ValueError):
        MatchState(frames_to_win=0)


def test_invalid_current_player_raises():
    with pytest.raises(ValueError):
        MatchState(current_player=3)


def test_add_frame_points_increments_score():
    state = MatchState()
    state.add_frame_points(1, 4)
    state.add_frame_points(2, 7)
    assert state.player1_frame_score == 4
    assert state.player2_frame_score == 7


def test_add_frame_points_defaults_to_one():
    state = MatchState()
    state.add_frame_points(1)
    assert state.player1_frame_score == 1


def test_add_frame_points_clamped_at_zero():
    state = MatchState()
    state.add_frame_points(1, -5)
    assert state.player1_frame_score == 0


def test_add_frame_points_invalid_player_raises():
    state = MatchState()
    with pytest.raises(ValueError):
        state.add_frame_points(3, 1)


def test_set_frame_score_overrides_value_directly():
    state = MatchState(player1_frame_score=30)
    state.set_frame_score(1, 55)
    assert state.player1_frame_score == 55


def test_set_frame_score_clamped_at_zero():
    state = MatchState()
    state.set_frame_score(1, -10)
    assert state.player1_frame_score == 0


def test_set_frame_score_invalid_player_raises():
    state = MatchState()
    with pytest.raises(ValueError):
        state.set_frame_score(3, 10)


def test_set_frames_overrides_value_directly():
    state = MatchState(player1_frames=2)
    state.set_frames(1, 5)
    assert state.player1_frames == 5


def test_set_frames_does_not_reset_frame_score_or_turn():
    state = MatchState(player1_frame_score=40, current_player=2)
    state.set_frames(1, 3)
    assert state.player1_frame_score == 40
    assert state.current_player == 2


def test_set_frames_clamped_at_zero():
    state = MatchState()
    state.set_frames(1, -3)
    assert state.player1_frames == 0


def test_set_frames_invalid_player_raises():
    state = MatchState()
    with pytest.raises(ValueError):
        state.set_frames(3, 2)


def test_switch_turn_toggles_current_player():
    state = MatchState(current_player=1)
    state.switch_turn()
    assert state.current_player == 2
    state.switch_turn()
    assert state.current_player == 1


def test_set_current_player():
    state = MatchState(current_player=1)
    state.set_current_player(2)
    assert state.current_player == 2


def test_set_current_player_invalid_raises():
    state = MatchState()
    with pytest.raises(ValueError):
        state.set_current_player(0)


def test_reset_frame_scores_keeps_frames_won():
    state = MatchState(player1_frames=3, player2_frames=2, player1_frame_score=45, player2_frame_score=30)
    state.reset_frame_scores()
    assert state.player1_frame_score == 0
    assert state.player2_frame_score == 0
    assert state.player1_frames == 3
    assert state.player2_frames == 2


def test_win_frame_increments_frames_and_resets_frame_scores():
    state = MatchState(player1_frame_score=68, player2_frame_score=12, current_player=1)
    state.win_frame(1)
    assert state.player1_frames == 1
    assert state.player2_frames == 0
    assert state.player1_frame_score == 0
    assert state.player2_frame_score == 0


def test_win_frame_alternates_current_player():
    state = MatchState(frames_to_win=5, current_player=1)
    state.win_frame(1)
    assert state.current_player == 2

    state.win_frame(2)
    assert state.current_player == 1


def test_win_frame_invalid_player_raises():
    state = MatchState()
    with pytest.raises(ValueError):
        state.win_frame(3)


def test_win_frame_does_nothing_once_match_is_already_won():
    state = MatchState(frames_to_win=2, player1_frames=2, player2_frames=1, current_player=1)
    assert state.winner() == 1

    state.win_frame(1)
    assert state.player1_frames == 2  # unchanged - match was already won
    assert state.current_player == 1  # turn indicator untouched too

    # Awarding the *other* player a frame shouldn't do anything either, once
    # the match is decided.
    state.win_frame(2)
    assert state.player2_frames == 1


def test_reset_match_clears_progress_but_keeps_configuration():
    state = MatchState(
        player1_name="Alice",
        player2_name="Bob",
        player1_frames=5,
        player2_frames=3,
        player1_frame_score=40,
        player2_frame_score=10,
        frames_to_win=10,
        current_player=2,
        match_title="World Championship - Final",
    )
    state.reset_match()
    assert state.player1_frames == 0
    assert state.player2_frames == 0
    assert state.player1_frame_score == 0
    assert state.player2_frame_score == 0
    assert state.current_player == 1
    # Configuration is untouched.
    assert state.player1_name == "Alice"
    assert state.player2_name == "Bob"
    assert state.frames_to_win == 10
    assert state.match_title == "World Championship - Final"


def test_winner_is_none_before_frames_to_win_reached():
    state = MatchState(frames_to_win=10, player1_frames=9, player2_frames=8)
    assert state.winner() is None


def test_winner_returns_player_number_once_reached():
    state = MatchState(frames_to_win=10, player1_frames=10, player2_frames=8)
    assert state.winner() == 1

    state2 = MatchState(frames_to_win=10, player1_frames=7, player2_frames=10)
    assert state2.winner() == 2


def test_as_overlay_text_shows_frames_and_frame_scores():
    state = MatchState(
        player1_name="Alice",
        player2_name="Bob",
        player1_frames=4,
        player2_frames=3,
        player1_frame_score=45,
        player2_frame_score=12,
        current_player=1,
    )
    assert state.as_overlay_text() == ">Alice 4 (45) - (12) 3 Bob"


def test_as_overlay_text_marks_player_two_turn():
    state = MatchState(current_player=2)
    assert state.as_overlay_text().endswith("Player 2<")


def test_as_overlay_text_includes_match_title_when_set():
    state = MatchState(
        player1_name="Alice", player2_name="Bob", player1_frames=2, player2_frames=1, match_title="World Championship"
    )
    assert state.as_overlay_text() == "World Championship | >Alice 2 (0) - (0) 1 Bob"


def test_break_is_committed_to_frame_score_on_switch_turn():
    state = MatchState()
    state.add_break_points(1)
    state.add_break_points(7)
    assert state.current_break == 8
    assert state.player1_frame_score == 0
    state.switch_turn()
    assert state.player1_frame_score == 8
    assert state.current_break == 0
    assert state.current_player == 2


def test_break_is_discarded_when_frame_is_won_or_reset():
    state = MatchState(frames_to_win=5)
    state.add_break_points(5)
    state.win_frame(1)
    assert state.current_break == 0
    assert state.player1_frame_score == 0
    state.add_break_points(3)
    state.reset_frame_scores()
    assert state.current_break == 0


def test_break_records_balls_and_undo_removes_the_last_one():
    state = MatchState()
    state.add_break_points(1, ball="red")
    state.add_break_points(7, ball="black")
    state.add_break_points(1, ball="red")
    assert state.break_balls == ["red", "black", "red"]
    assert state.current_break == 9
    state.add_break_points(-1)
    assert state.break_balls == ["red", "black"]
    assert state.current_break == 8
    state.add_break_points(-1)
    assert state.current_break == 1
    state.switch_turn()
    assert state.break_balls == []
    assert state.player1_frame_score == 1
