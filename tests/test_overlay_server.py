"""Tests for the browser-overlay HTTP server.

These tests use plain ``urllib`` requests against a real server bound to an
OS-assigned free port (``port=0``). They need no display/GUI toolkit, so
they always run, unlike the Tkinter scoreboard-window tests.
"""

import json
import urllib.error
import urllib.request

from obs_snooker.match_state import MatchState
from obs_snooker.overlay_server import OverlayServer


def _get(url: str):
    with urllib.request.urlopen(url, timeout=2) as response:
        return response.status, response.read(), response.headers.get("Content-Type")


def _post(url: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_overlay_serves_html_css_and_js():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body, content_type = _get(f"http://127.0.0.1:{server.port}/overlay")
        assert status == 200
        assert "text/html" in content_type
        assert b"<html" in body.lower()

        status, _, content_type = _get(f"http://127.0.0.1:{server.port}/overlay.css")
        assert status == 200
        assert "text/css" in content_type

        status, _, content_type = _get(f"http://127.0.0.1:{server.port}/overlay.js")
        assert status == 200
        assert "javascript" in content_type
    finally:
        server.stop()


def test_score_endpoint_reflects_state():
    state = MatchState(
        player1_name="Alice",
        player2_name="Bob",
        player1_country="ENG",
        player2_country="WAL",
        player1_frames=4,
        player2_frames=2,
        player1_frame_score=30,
        player2_frame_score=15,
        frames_to_win=10,
        current_player=2,
        match_title="Test Cup",
    )
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body, content_type = _get(f"http://127.0.0.1:{server.port}/api/score")
        assert status == 200
        assert "application/json" in content_type
        assert json.loads(body) == {
            "player1_name": "Alice",
            "player2_name": "Bob",
            "player1_country": "ENG",
            "player2_country": "WAL",
            "player1_frames": 4,
            "player2_frames": 2,
            "player1_frame_score": 30,
            "player2_frame_score": 15,
            "frames_to_win": 10,
            "current_player": 2,
            "current_break": 0,
            "break_balls": [],
            "match_title": "Test Cup",
            "foul": {"mode": "off", "version": 0, "opacity": 50},
        }
    finally:
        server.stop()


def test_score_endpoint_picks_up_live_updates():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        state.add_frame_points(1, 5)
        _, body, _ = _get(f"http://127.0.0.1:{server.port}/api/score")
        assert json.loads(body)["player1_frame_score"] == 5
    finally:
        server.stop()


def test_control_page_serves_html_css_and_js():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body, content_type = _get(f"http://127.0.0.1:{server.port}/control")
        assert status == 200
        assert "text/html" in content_type
        assert b"<html" in body.lower()

        status, _, content_type = _get(f"http://127.0.0.1:{server.port}/control.css")
        assert status == 200
        assert "text/css" in content_type

        status, _, content_type = _get(f"http://127.0.0.1:{server.port}/control.js")
        assert status == 200
        assert "javascript" in content_type
    finally:
        server.stop()


def test_action_add_points_mutates_state():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body = _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "add_points", "player": 1})
        assert status == 200
        assert body["current_break"] == 1
        assert state.current_break == 1
        assert state.player1_frame_score == 1
    finally:
        server.stop()


def test_action_add_points_with_explicit_value():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "add_points", "player": 1, "value": 5})
        assert state.current_break == 5
    finally:
        server.stop()


def test_action_add_points_negative_value_undoes_a_mistake():
    state = MatchState(current_break=5)
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "add_points", "player": 1, "value": -1})
        assert state.current_break == 4
    finally:
        server.stop()


def test_action_pot_ball_adds_correct_ball_value():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "pot_ball", "player": 1, "ball": "black"})
        assert state.current_break == 7
    finally:
        server.stop()


def test_break_is_in_frame_score_immediately_and_cleared_on_switch_turn():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/action"
        _post(url, {"action": "pot_ball", "player": 1, "ball": "red"})
        _post(url, {"action": "pot_ball", "player": 1, "ball": "black"})
        assert state.player1_frame_score == 8
        _, body = _post(url, {"action": "switch_turn"})
        assert body["player1_frame_score"] == 8
        assert body["current_break"] == 0
        assert body["current_player"] == 2
    finally:
        server.stop()


def test_pot_ball_for_player_not_at_the_table_returns_400():
    state = MatchState(current_player=1)
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body = _post(
            f"http://127.0.0.1:{server.port}/api/action", {"action": "pot_ball", "player": 2, "ball": "red"}
        )
        assert status == 400
        assert state.current_break == 0
    finally:
        server.stop()


def test_action_pot_ball_unknown_ball_returns_400():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body = _post(
            f"http://127.0.0.1:{server.port}/api/action", {"action": "pot_ball", "player": 1, "ball": "purple"}
        )
        assert status == 400
        assert "error" in body
    finally:
        server.stop()


def test_action_win_frame():
    state = MatchState(frames_to_win=5)
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "win_frame", "player": 1})
        assert state.player1_frames == 1
    finally:
        server.stop()


def test_action_switch_turn():
    state = MatchState(current_player=1)
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "switch_turn"})
        assert state.current_player == 2
    finally:
        server.stop()


def test_action_reset_frame_scores():
    state = MatchState(player1_frame_score=40, player2_frame_score=12)
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "reset_frame_scores"})
        assert state.player1_frame_score == 0
        assert state.player2_frame_score == 0
    finally:
        server.stop()


def test_action_set_frame_score_correction():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "set_frame_score", "player": 2, "value": 55})
        assert state.player2_frame_score == 55
    finally:
        server.stop()


def test_action_set_frames_correction():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "set_frames", "player": 1, "value": 3})
        assert state.player1_frames == 3
    finally:
        server.stop()


def test_action_unknown_action_returns_400():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body = _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "reset_match"})
        assert status == 400
        assert "error" in body
        # Deliberately unsupported: resetting the whole match is destructive
        # and shouldn't be reachable from a single control-panel button.
        assert state.player1_frames == 0
    finally:
        server.stop()


def test_action_missing_player_returns_400():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, body = _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "add_points"})
        assert status == 400
        assert "error" in body
    finally:
        server.stop()


def test_action_invalid_json_body_returns_400():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/api/action",
            data=b"not json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected a 400 HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        server.stop()


def test_unknown_path_returns_404():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        try:
            _get(f"http://127.0.0.1:{server.port}/does-not-exist")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected a 404 HTTPError")
    finally:
        server.stop()


def test_restart_on_new_port():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    first_port = server.port
    try:
        server.restart(port=0)
        assert server.is_running
        status, _, _ = _get(f"http://127.0.0.1:{server.port}/overlay")
        assert status == 200
    finally:
        server.stop()
    assert not server.is_running


def test_wildcard_bind_host_still_reports_connectable_localhost_urls():
    """Binding to 0.0.0.0 (so other LAN devices can connect) must not break same-machine URLs.

    Connecting a client socket to 0.0.0.0 is unreliable cross-platform (in
    particular on Windows), so `url`/`control_url`/`score_url` must report
    127.0.0.1 instead of echoing back the wildcard bind address, even
    though the server actually listens on every interface.
    """
    state = MatchState()
    server = OverlayServer(state, host="0.0.0.0", port=0)
    server.start()
    try:
        assert server.host == "0.0.0.0"
        assert server.url.startswith("http://127.0.0.1:")
        assert server.control_url.startswith("http://127.0.0.1:")
        assert server.score_url.startswith("http://127.0.0.1:")

        status, _, _ = _get(server.score_url)
        assert status == 200
    finally:
        server.stop()


def test_flag_asset_is_served_and_unknown_flag_404s():
    server = OverlayServer(MatchState(), port=0)
    server.start()
    try:
        status, body, content_type = _get(f"http://127.0.0.1:{server.port}/flags/CZE.svg")
        assert status == 200
        assert "image/svg+xml" in content_type
        assert b"<svg" in body
        try:
            _get(f"http://127.0.0.1:{server.port}/flags/zzz.svg")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as error:
            assert error.code == 404
    finally:
        server.stop()


def test_logo_is_served():
    server = OverlayServer(MatchState(), port=0)
    server.start()
    try:
        status, body, content_type = _get(f"http://127.0.0.1:{server.port}/logo.svg")
        assert status == 200
        assert "image/svg+xml" in content_type
    finally:
        server.stop()


def _admin_server(tmp_path, **kwargs):
    from obs_snooker.player_database import PlayerDatabase

    db = PlayerDatabase(str(tmp_path / "players.json"))
    state = MatchState()
    return OverlayServer(state, port=0, player_db=db, **kwargs), state, db


def test_admin_set_match_applies_roster_player_title_and_race(tmp_path):
    changes = []
    server, state, db = _admin_server(tmp_path, on_config_change=changes.append)
    alice = db.add_player("Alice Smith", country="ENG")
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/admin"
        status, body = _post(
            url,
            {
                "action": "set_match",
                "player1_id": alice.id,
                "player2_name": "Bob",
                "match_title": "Final",
                "frames_to_win": 7,
            },
        )
        assert status == 200
        assert state.player1_name == "Alice Smith"
        assert state.player1_country == "ENG"
        assert state.player2_name == "Bob"
        assert state.match_title == "Final"
        assert state.frames_to_win == 7
        assert body["config"]["player1_id"] == alice.id
        assert changes[-1]["frames_to_win"] == 7
        status, body = _post(url, {"action": "set_match", "frames_to_win": 0})
        assert status == 400
    finally:
        server.stop()


def test_admin_roster_crud_and_selection_cleanup(tmp_path):
    server, state, db = _admin_server(tmp_path)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/admin"
        _, body = _post(url, {"action": "add_player", "name": "Carl Jones", "country": "wal"})
        player = body["players"][0]
        assert player["country"] == "WAL"
        _post(url, {"action": "update_player", "id": player["id"], "name": "Carl Jonas", "country": "WAL"})
        assert db.get_player(player["id"]).name == "Carl Jonas"
        _post(url, {"action": "set_match", "player1_id": player["id"]})
        assert state.player1_name == "Carl Jonas"
        _, body = _post(url, {"action": "remove_player", "id": player["id"]})
        assert body["players"] == []
        assert body["config"]["player1_id"] == ""
        status, _ = _post(url, {"action": "add_player", "name": " "})
        assert status == 400
    finally:
        server.stop()


def test_admin_score_correction_and_reset(tmp_path):
    server, state, _ = _admin_server(tmp_path)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/admin"
        _post(url, {"action": "set_frame_score", "player": 2, "value": 33})
        _post(url, {"action": "set_frames", "player": 1, "value": 4})
        _post(url, {"action": "set_break", "value": 12})
        assert (state.player2_frame_score, state.player1_frames, state.current_break) == (33, 4, 12)
        _post(url, {"action": "reset_match"})
        assert (state.player2_frame_score, state.player1_frames, state.current_break) == (0, 0, 0)
        status, _ = _post(url, {"action": "bogus"})
        assert status == 400
    finally:
        server.stop()


def test_admin_page_and_snapshot_are_served(tmp_path):
    server, _, _ = _admin_server(tmp_path)
    server.start()
    try:
        status, body, content_type = _get(f"http://127.0.0.1:{server.port}/admin")
        assert status == 200 and "text/html" in content_type
        status, body, _ = _get(f"http://127.0.0.1:{server.port}/api/admin")
        data = json.loads(body)
        assert "CZE" in data["flags"] and data["roster_available"] is True
    finally:
        server.stop()


def test_pot_ball_records_balls_and_undo_ball_removes_last():
    state = MatchState()
    server = OverlayServer(state, port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/action"
        _post(url, {"action": "pot_ball", "player": 1, "ball": "red"})
        _, body = _post(url, {"action": "pot_ball", "player": 1, "ball": "blue"})
        assert body["break_balls"] == ["red", "blue"]
        _, body = _post(url, {"action": "undo_ball", "player": 1})
        assert body["break_balls"] == ["red"]
        assert body["current_break"] == 1
    finally:
        server.stop()


def test_admin_foul_flow_and_image_endpoints(tmp_path):
    requests = []
    server, _, _ = _admin_server(tmp_path, on_foul_action=requests.append)
    server.foul_options = {"sources": ["Cam"], "camera_source": "", "opacity": 50}
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        admin = f"{base}/api/admin"
        assert _post(admin, {"action": "foul_reference"})[0] == 400  # no camera selected
        status, body = _post(admin, {"action": "foul_settings", "camera_source": "Cam", "opacity": 30})
        assert status == 200 and body["foul"]["opacity"] == 30
        assert _post(admin, {"action": "foul_settings", "camera_source": "Cam", "opacity": 200})[0] == 400
        assert _post(admin, {"action": "foul_compare"})[0] == 400  # no reference yet
        assert _post(admin, {"action": "foul_reference"})[0] == 200
        assert requests[-1] == {"action": "foul_reference"}

        server.set_foul_image("reference", b"\x89PNGref")
        assert server.foul_public_state()["mode"] == "off"
        assert server.foul_snapshot()["has_reference"] is True
        server.set_foul_image("current", b"\x89PNGcur")
        with urllib.request.urlopen(f"{base}/api/score") as r:
            foul = json.loads(r.read())["foul"]
        assert foul["mode"] == "compare" and foul["opacity"] == 30
        with urllib.request.urlopen(f"{base}/foul/reference") as r:
            assert r.read() == b"\x89PNGref" and r.headers["Content-Type"] == "image/png"
        assert _post(admin, {"action": "foul_hide"})[0] == 200
        assert server.foul_public_state()["mode"] == "off"
        try:
            urllib.request.urlopen(f"{base}/foul/reference")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.stop()


def test_admin_foul_unavailable_outside_obs(tmp_path):
    server, _, _ = _admin_server(tmp_path)
    server.start()
    try:
        status, _ = _post(f"http://127.0.0.1:{server.port}/api/admin", {"action": "foul_reference"})
        assert status == 400
    finally:
        server.stop()


def test_admin_snapshot_has_access_urls_and_qr_asset(tmp_path):
    server, _, _ = _admin_server(tmp_path)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urllib.request.urlopen(f"{base}/api/admin") as r:
            access = json.loads(r.read())["access"]
        assert set(access) == {"by_name", "by_ip"}
        for url in access.values():
            assert url is None or url.endswith(f":{server.port}/control")
        with urllib.request.urlopen(f"{base}/qrcode.js") as r:
            assert b"qrcode" in r.read()
    finally:
        server.stop()


def test_pwa_assets_are_served():
    server = OverlayServer(MatchState(), port=0)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        for path, content_type in (
            ("/manifest.webmanifest", "application/manifest+json"),
            ("/sw.js", "application/javascript"),
            ("/icon-192.png", "image/png"),
            ("/icon-512.png", "image/png"),
        ):
            with urllib.request.urlopen(base + path) as r:
                assert content_type in r.headers["Content-Type"]
                assert r.read()
        with urllib.request.urlopen(base + "/manifest.webmanifest") as r:
            assert json.loads(r.read())["display"] == "fullscreen"
    finally:
        server.stop()


def test_win_frame_after_the_match_is_won_returns_a_clear_error():
    state = MatchState(frames_to_win=1)
    server = OverlayServer(state, port=0)
    server.start()
    try:
        url = f"http://127.0.0.1:{server.port}/api/action"
        assert _post(url, {"action": "win_frame", "player": 1})[0] == 200
        status, body = _post(url, {"action": "win_frame", "player": 2})
        assert status == 400
        assert "already won" in body["error"]
        assert state.player2_frames == 0
    finally:
        server.stop()


def test_win_frame_works_for_the_player_not_at_the_table():
    state = MatchState(frames_to_win=5, current_player=1)
    server = OverlayServer(state, port=0)
    server.start()
    try:
        status, _ = _post(f"http://127.0.0.1:{server.port}/api/action", {"action": "win_frame", "player": 2})
        assert status == 200
        assert state.player2_frames == 1
    finally:
        server.stop()
