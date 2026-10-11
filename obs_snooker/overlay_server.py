"""Local HTTP server that exposes a browser-based scoreboard overlay.

This module has no dependency on ``obspython`` so the HTTP server and its
JSON API can be started, queried, and unit-tested with plain ``urllib``
calls, completely outside of OBS.

Point an OBS **Browser Source** at ``http://<host>:<port>/overlay`` to show
a styled, transparent-background scoreboard as a lower-third style overlay
on top of the camera feed.

A second page, ``http://<host>:<port>/control``, exposes clickable scoring
buttons that POST to ``/api/action`` to mutate the shared :class:`MatchState`
directly. Point another Browser Source at it and use OBS's built-in
"Interact" mode (right-click the source -> Interact) to click those buttons
without ever opening this script's own Scripts-panel properties dialog.
"""

from __future__ import annotations

import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .match_state import MatchState

_ASSETS_DIR = Path(__file__).parent / "overlay_assets"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
}

# Maps request paths to the static asset file that should be served for them.
_ROUTES = {
    "/overlay": "overlay.html",
    "/overlay.css": "overlay.css",
    "/overlay.js": "overlay.js",
    "/control": "control.html",
    "/control.css": "control.css",
    "/control.js": "control.js",
    "/logo.svg": "logo.svg",
    "/admin": "admin.html",
    "/admin.css": "admin.css",
    "/admin.js": "admin.js",
    "/qrcode.js": "qrcode.js",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/sw.js": "sw.js",
    "/icon-192.png": "icon-192.png",
    "/icon-512.png": "icon-512.png",
}


def local_lan_ip() -> str | None:
    """Best-effort guess at this machine's LAN IP (cross-platform, no extra dependencies).

    Opening a UDP "connection" sends no packets but makes the OS pick the
    interface it would route through, which ``getsockname()`` then reveals.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        return None


def mdns_hostname() -> str | None:
    """The computer's ``name.local`` address (resolved by mDNS/Bonjour on phones, tablets and modern PCs)."""
    try:
        name = socket.gethostname().split(".")[0].strip()
    except OSError:
        return None
    return f"{name}.local" if name else None

# Ball point values, shared with obs-snooker.py's own OBS-hotkey/button
# definitions (kept as a separate copy here, rather than imported, since
# this module must stay importable/testable without `obspython` - see the
# module docstring - and obs-snooker.py is the one file allowed to depend on
# it).
_BALL_VALUES: dict[str, int] = {
    "red": 1,
    "yellow": 2,
    "green": 3,
    "brown": 4,
    "blue": 5,
    "pink": 6,
    "black": 7,
}


def _make_handler(
    state: MatchState, action_lock: threading.Lock, server: OverlayServer
) -> type[BaseHTTPRequestHandler]:
    """Builds a request handler class bound to the given (mutable) match state.

    The handler reads ``state`` at request time, so score updates made
    elsewhere in the process are picked up immediately without restarting
    the server. ``action_lock`` serializes ``/api/action`` mutations against
    each other (e.g. two quick clicks on the control page in different
    browser requests/threads); it does **not** synchronize against
    ``obs-snooker.py``'s own OBS-hotkey/button callbacks, which run on a
    different thread entirely - seebelow's `OverlayServer` docstring for why
    that's an accepted tradeoff here.
    """

    class OverlayRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Silence per-request access logs; OBS's script log is not a
            # great place for HTTP request noise.
            pass

        def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
            if self.path in ("/api/score", "/score.json"):
                self._send_score()
                return
            if self.path == "/api/admin":
                self._send_json(self._admin_payload())
                return

            foul_match = re.fullmatch(r"/foul/(reference|current)", self.path)
            if foul_match is not None:
                self._send_foul_image(foul_match.group(1))
                return

            flag_match = re.fullmatch(r"/flags/([A-Za-z]{2,3})\.svg", self.path)
            if flag_match is not None:
                self._send_asset(f"flags/{flag_match.group(1).lower()}.svg")
                return

            asset_name = _ROUTES.get(self.path)
            if asset_name is not None:
                self._send_asset(asset_name)
                return

            self.send_error(404, "Not found")

        def do_POST(self) -> None:  # noqa: N802 (stdlib method name)
            if self.path == "/api/action":
                self._handle_action()
                return
            if self.path == "/api/admin":
                self._handle_admin()
                return
            self.send_error(404, "Not found")

        def _read_json_body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw_body or b"{}")
            except ValueError:
                payload = None
            if not isinstance(payload, dict):
                self._send_json_error(400, "Invalid JSON body")
                return None
            return payload

        def _handle_admin(self) -> None:
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                with action_lock:
                    server.apply_admin_action(payload)
            except _ActionError as e:
                self._send_json_error(400, str(e))
                return
            self._send_json(self._admin_payload())

        def _admin_payload(self) -> dict[str, Any]:
            return server.admin_snapshot()

        def _send_json(self, data: dict[str, Any]) -> None:
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _handle_action(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw_body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw_body or b"{}")
            except ValueError:
                self._send_json_error(400, "Invalid JSON body")
                return

            action = payload.get("action")
            try:
                with action_lock:
                    self._apply_action(action, payload)
            except _ActionError as e:
                self._send_json_error(400, str(e))
                return

            self._send_score()

        def _apply_action(self, action: str | None, payload: dict[str, Any]) -> None:
            """Dispatches one control-panel action onto ``state``.

            Deliberately does **not** support a "reset match" action - that
            is a destructive, rarely-needed action best kept behind an
            explicit confirmation in the Scripts panel, not a single
            mis-clickable button on a touch control panel.
            """
            player = payload.get("player")
            value = payload.get("value")

            if action == "add_points":
                self._require_current_player(player)
                state.add_break_points(self._coerce_int(value, default=1))
            elif action == "undo_ball":
                self._require_current_player(player)
                state.add_break_points(-1)
            elif action == "pot_ball":
                self._require_current_player(player)
                ball = payload.get("ball")
                if ball not in _BALL_VALUES:
                    raise _ActionError(f"Unknown ball '{ball}'")
                state.add_break_points(_BALL_VALUES[ball], ball=ball)
            elif action == "win_frame":
                self._require_player(player)
                state.win_frame(player)
            elif action == "switch_turn":
                state.switch_turn()
            elif action == "reset_frame_scores":
                state.reset_frame_scores()
            elif action == "set_frame_score":
                self._require_player(player)
                state.set_frame_score(player, self._coerce_int(value, default=0))
            elif action == "set_frames":
                self._require_player(player)
                state.set_frames(player, self._coerce_int(value, default=0))
            else:
                raise _ActionError(f"Unknown action '{action}'")

        def _require_current_player(self, player: Any) -> None:
            """Points only go to the break of the player who is at the table."""
            self._require_player(player)
            if player != state.current_player:
                raise _ActionError(f"Player {player} is not at the table")

        @staticmethod
        def _require_player(player: Any) -> None:
            if player not in (1, 2):
                raise _ActionError(f"'player' must be 1 or 2, got {player!r}")

        @staticmethod
        def _coerce_int(value: Any, default: int) -> int:
            if value is None:
                return default
            try:
                return int(value)
            except (TypeError, ValueError):
                raise _ActionError(f"Expected an integer value, got {value!r}") from None

        def _send_json_error(self, status: int, message: str) -> None:
            payload = json.dumps({"error": message}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_score(self) -> None:
            payload = json.dumps(
                {
                    "player1_name": state.player1_name,
                    "player2_name": state.player2_name,
                    "player1_country": state.player1_country,
                    "player2_country": state.player2_country,
                    "player1_frames": state.player1_frames,
                    "player2_frames": state.player2_frames,
                    "player1_frame_score": state.player1_frame_score,
                    "player2_frame_score": state.player2_frame_score,
                    "frames_to_win": state.frames_to_win,
                    "current_player": state.current_player,
                    "current_break": state.current_break,
                    "break_balls": list(state.break_balls),
                    "match_title": state.match_title,
                    "foul": server.foul_public_state(),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_foul_image(self, kind: str) -> None:
            body = server.foul_image(kind)
            if body is None:
                self.send_error(404, "No image")
                return
            if body.startswith(b"\x89PNG"):
                content_type = "image/png"
            elif body.startswith(b"\xff\xd8"):
                content_type = "image/jpeg"
            else:
                content_type = "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_asset(self, asset_name: str) -> None:
            asset_path = _ASSETS_DIR / asset_name
            try:
                body = asset_path.read_bytes()
            except OSError:
                self.send_error(404, "Asset not found")
                return

            content_type = _CONTENT_TYPES.get(asset_path.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return OverlayRequestHandler


class _ActionError(ValueError):
    """Raised for a malformed/unsupported ``/api/action`` request; caught and turned into a 400 response."""


class OverlayServer:
    """Runs the scoreboard overlay's HTTP server in a background thread.

    Unlike the Tkinter scoreboard window, a plain HTTP server has no
    main-thread requirement on macOS, so it can safely run in a background
    thread on both macOS and Windows.

    Safe to start/stop/restart repeatedly, e.g. when the user changes the
    port in OBS's script properties panel.

    Note on thread-safety: ``/api/action`` requests (from the ``/control``
    page) run on this server's own background HTTP thread(s), which is a
    *different* thread than the one OBS uses to invoke this script's own
    hotkey/button callbacks (`obs-snooker.py`). Both ultimately mutate the
    same shared `MatchState` instance. `MatchState`'s own methods only do
    simple attribute reads/writes, which are individually atomic under
    Python's GIL, so this doesn't corrupt data - but two *different* actions
    arriving at almost the same instant from both input paths could still
    interleave in an unexpected order. This is treated as an acceptable risk
    for a hobby scoreboard tool (same category of risk as a referee and an
    operator both trying to score the same shot at the exact same
    millisecond), rather than adding a full cross-thread locking scheme.
    """

    def __init__(
        self,
        state: MatchState,
        host: str = "127.0.0.1",
        port: int = 8710,
        player_db: Any = None,
        on_config_change: Callable[[dict[str, Any]], None] | None = None,
        on_foul_action: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.state = state
        # Optional roster used by the /admin page; `on_config_change` is told
        # about every match-setup change made there (called on an HTTP thread,
        # so it must only hand the data over, not touch non-thread-safe APIs).
        self.player_db = player_db
        self.on_config_change = on_config_change
        # Foul & miss control (referee tool): camera screenshots shown on the
        # scoreboard window. `on_foul_action` asks the OBS glue to capture a
        # screenshot (on an HTTP thread, so it must only queue the request);
        # the glue then hands the image back via `set_foul_image()`.
        # `foul_options` is a snapshot published by the glue (camera sources,
        # selected source, opacity); None means "not running inside OBS".
        self.on_foul_action = on_foul_action
        self.foul_options: dict[str, Any] | None = None
        self._foul_lock = threading.Lock()
        self._foul_mode = "off"
        self._foul_version = 0
        self._foul_images: dict[str, bytes | None] = {"reference": None, "current": None}
        self.config: dict[str, str] = {
            "player1_id": "",
            "player2_id": "",
            "player1_custom_name": state.player1_name,
            "player2_custom_name": state.player2_name,
        }
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._action_lock = threading.Lock()

    @property
    def admin_url(self) -> str:
        """URL of the admin page (match setup, player roster, score corrections)."""
        return f"http://{self._connectable_host}:{self.port}/admin"

    def access_urls(self) -> dict[str, str | None]:
        """Control-panel addresses for other devices on the LAN: by computer name (mDNS) and by IP."""
        host = mdns_hostname()
        ip = local_lan_ip()
        return {
            "by_name": f"http://{host}:{self.port}/control" if host else None,
            "by_ip": f"http://{ip}:{self.port}/control" if ip else None,
        }

    def apply_selection(self) -> None:
        """Applies the configured player selection (roster players or custom names) to the match state."""
        for number in (1, 2):
            player = None
            if self.player_db is not None and self.config[f"player{number}_id"]:
                player = self.player_db.get_player(self.config[f"player{number}_id"])
            custom_name = self.config[f"player{number}_custom_name"]
            if player is not None:
                setattr(self.state, f"player{number}_name", player.name)
                setattr(self.state, f"player{number}_country", player.country)
            elif custom_name:
                setattr(self.state, f"player{number}_name", custom_name)
                setattr(self.state, f"player{number}_country", "")

    def admin_snapshot(self) -> dict[str, Any]:
        """Everything the /admin page shows: match setup, roster, available flags and the live score."""
        state = self.state
        players = []
        if self.player_db is not None:
            players = [p.to_dict() for p in self.player_db.list_players()]
        flags_dir = _ASSETS_DIR / "flags"
        flags = sorted(f.stem.upper() for f in flags_dir.glob("*.svg")) if flags_dir.is_dir() else []
        return {
            "config": dict(self.config),
            "match_title": state.match_title,
            "frames_to_win": state.frames_to_win,
            "player1_name": state.player1_name,
            "player2_name": state.player2_name,
            "player1_country": state.player1_country,
            "player2_country": state.player2_country,
            "player1_frames": state.player1_frames,
            "player2_frames": state.player2_frames,
            "player1_frame_score": state.player1_frame_score,
            "player2_frame_score": state.player2_frame_score,
            "current_break": state.current_break,
            "break_balls": list(state.break_balls),
            "current_player": state.current_player,
            "players": players,
            "flags": flags,
            "roster_available": self.player_db is not None,
            "foul": self.foul_snapshot(),
            "access": self.access_urls(),
        }

    def apply_admin_action(self, payload: dict[str, Any]) -> None:
        """Handles one /api/admin POST: match setup, roster edits, score corrections or a match reset."""
        action = payload.get("action")
        state = self.state
        if action == "set_match":
            for number in (1, 2):
                if f"player{number}_id" in payload:
                    self.config[f"player{number}_id"] = str(payload[f"player{number}_id"] or "")
                if f"player{number}_name" in payload:
                    self.config[f"player{number}_custom_name"] = str(payload[f"player{number}_name"] or "").strip()
            if "match_title" in payload:
                state.match_title = str(payload["match_title"] or "").strip()
            if "frames_to_win" in payload:
                try:
                    frames_to_win = int(payload["frames_to_win"])
                except (TypeError, ValueError):
                    raise _ActionError("frames_to_win must be an integer") from None
                if not 1 <= frames_to_win <= 35:
                    raise _ActionError("frames_to_win must be between 1 and 35")
                state.frames_to_win = frames_to_win
            self.apply_selection()
        elif action in ("add_player", "update_player", "remove_player"):
            if self.player_db is None:
                raise _ActionError("Player roster is not available")
            name = str(payload.get("name") or "").strip()
            country = str(payload.get("country") or "").strip().upper()
            if action == "add_player":
                if not name:
                    raise _ActionError("Player name is required")
                self.player_db.add_player(name, country=country)
            elif action == "update_player":
                if not name:
                    raise _ActionError("Player name is required")
                try:
                    self.player_db.update_player(str(payload.get("id")), name=name, country=country)
                except KeyError as e:
                    raise _ActionError(str(e)) from None
            else:
                removed_id = str(payload.get("id"))
                self.player_db.remove_player(removed_id)
                for number in (1, 2):
                    if self.config[f"player{number}_id"] == removed_id:
                        self.config[f"player{number}_id"] = ""
            self.apply_selection()
        elif action == "set_frame_score":
            self._admin_player(payload)
            state.set_frame_score(payload["player"], self._admin_int(payload.get("value")))
        elif action == "set_frames":
            self._admin_player(payload)
            state.set_frames(payload["player"], self._admin_int(payload.get("value")))
        elif action == "set_break":
            state.clear_break()
            state.current_break = max(0, self._admin_int(payload.get("value")))
        elif action in ("foul_reference", "foul_compare", "foul_hide", "foul_settings"):
            self._apply_foul_action(action, payload)
        elif action == "reset_frame_scores":
            state.reset_frame_scores()
        elif action == "reset_match":
            state.reset_match()
        else:
            raise _ActionError(f"Unknown admin action '{action}'")
        if self.on_config_change is not None and action in (
            "set_match", "add_player", "update_player", "remove_player"
        ):
            self.on_config_change({"config": dict(self.config), "match_title": state.match_title, "frames_to_win": state.frames_to_win})

    def foul_snapshot(self) -> dict[str, Any] | None:
        """State of the foul & miss control for the admin page; None outside OBS."""
        if self.foul_options is None:
            return None
        with self._foul_lock:
            return {
                **self.foul_options,
                "mode": self._foul_mode,
                "has_reference": self._foul_images["reference"] is not None,
            }

    def foul_public_state(self) -> dict[str, Any]:
        """The part of the foul state the scoreboard window polls via /api/score."""
        opacity = int(self.foul_options["opacity"]) if self.foul_options else 50
        with self._foul_lock:
            return {"mode": self._foul_mode, "version": self._foul_version, "opacity": opacity}

    def foul_image(self, kind: str) -> bytes | None:
        with self._foul_lock:
            return self._foul_images.get(kind)

    def set_foul_image(self, kind: str, data: bytes) -> None:
        """Stores a captured camera screenshot; a "current" one also switches the scoreboard window to the comparison view."""
        with self._foul_lock:
            if kind == "reference":
                # The reference is only stored; nothing is shown until "compare".
                self._foul_images = {"reference": data, "current": None}
                self._foul_mode = "off"
            else:
                self._foul_images["current"] = data
                self._foul_mode = "compare"
            self._foul_version += 1

    def hide_foul(self) -> None:
        with self._foul_lock:
            self._foul_images = {"reference": None, "current": None}
            self._foul_mode = "off"
            self._foul_version += 1

    def _apply_foul_action(self, action: str, payload: dict[str, Any]) -> None:
        if self.foul_options is None:
            raise _ActionError("Foul & miss control is only available when running inside OBS")
        if action == "foul_hide":
            self.hide_foul()
            return
        if action == "foul_settings":
            opacity = self._admin_int(payload.get("opacity"))
            if not 0 <= opacity <= 100:
                raise _ActionError("opacity must be between 0 and 100")
            camera_source = str(payload.get("camera_source") or "")
            self.foul_options["opacity"] = opacity
            self.foul_options["camera_source"] = camera_source
            if self.on_foul_action is not None:
                self.on_foul_action({"action": action, "opacity": opacity, "camera_source": camera_source})
            with self._foul_lock:
                self._foul_version += 1
            return
        if not self.foul_options.get("camera_source"):
            raise _ActionError("Select a camera source first")
        if action == "foul_compare" and self.foul_image("reference") is None:
            raise _ActionError("Take the reference screenshot first")
        if self.on_foul_action is not None:
            self.on_foul_action({"action": action})

    @staticmethod
    def _admin_player(payload: dict[str, Any]) -> None:
        if payload.get("player") not in (1, 2):
            raise _ActionError("'player' must be 1 or 2")

    @staticmethod
    def _admin_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise _ActionError(f"Expected an integer value, got {value!r}") from None

    @property
    def _connectable_host(self) -> str:
        """The host to use when *connecting to* this server from the same machine.

        ``self.host`` is the *bind* address, which may be a wildcard like
        ``"0.0.0.0"`` (listen on all network interfaces, so other devices on
        the LAN can connect too) - but a wildcard bind address is not
        reliably usable as the *client-side* connect target on every
        platform (e.g. connecting to ``0.0.0.0`` from a client is
        inconsistent on Windows). A server bound to ``0.0.0.0`` still always
        accepts connections to ``127.0.0.1`` too, so that's what same-machine
        URLs (used by the scoreboard window subprocess, and the "open this
        in a browser on this machine" log message) should use instead.
        """
        if self.host in ("0.0.0.0", "::", ""):
            return "127.0.0.1"
        return self.host

    @property
    def url(self) -> str:
        return f"http://{self._connectable_host}:{self.port}/overlay"

    @property
    def control_url(self) -> str:
        """URL of the interactive control panel (for a second Browser Source, used via OBS's "Interact" mode)."""
        return f"http://{self._connectable_host}:{self.port}/control"

    @property
    def score_url(self) -> str:
        """URL of the JSON score endpoint, used by the standalone scoreboard window process."""
        return f"http://{self._connectable_host}:{self.port}/api/score"

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self._httpd is not None:
            return
        handler = _make_handler(self.state, self._action_lock, self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        # If port was 0, the OS picked a free one; reflect that back.
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None

    def restart(self, host: str | None = None, port: int | None = None) -> None:
        """Stops the server (if running) and starts it again, optionally on a new host/port."""
        self.stop()
        if host is not None:
            self.host = host
        if port is not None:
            self.port = port
        self.start()


def _run_preview() -> None:
    """Starts the overlay with demo data so it can be opened in a normal browser.

    Run this file as a module (not directly), from the project root, with:

        python -m obs_snooker.overlay_server

    Running it directly as a script (e.g. ``python overlay_server.py`` or an
    IDE "Run" button on this file) breaks the package's relative imports
    (``from .match_state import ...``) because Python then has no notion of
    the ``obs_snooker`` package the file belongs to - hence the
    "attempted relative import with no known parent package" error.
    """
    import time

    demo_state = MatchState(
        player1_name="Trump",
        player2_name="O'Sullivan",
        player1_country="ENG",
        player2_country="CZE",
        player1_frames=3,
        player2_frames=2,
        player1_frame_score=45,
        player2_frame_score=12,
        frames_to_win=10,
        current_player=1,
        match_title="World Championship - Final",
    )
    import tempfile

    from .player_database import PlayerDatabase

    # A throwaway roster, so experimenting on /admin never touches the real players.json.
    demo_db = PlayerDatabase(str(Path(tempfile.mkdtemp()) / "players.json"))
    demo_db.add_player("Judd Trump", country="ENG")
    demo_db.add_player("Ronnie O'Sullivan", country="ENG")
    demo_db.add_player("Aleš Herout", country="CZE")
    server = OverlayServer(demo_state, player_db=demo_db)
    server.start()
    print("Preview running (demo data) - open in a browser. Press Ctrl+C to stop.")
    print(f"  Overlay:       {server.url}")
    print(f"  Control panel: {server.control_url}")
    print(f"  Admin:         {server.admin_url}")
    print(f"  Score JSON:    {server.score_url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    _run_preview()
