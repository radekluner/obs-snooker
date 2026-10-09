"""OBS Studio script entry point.

Keeps only the glue code that talks to ``obspython`` and ``pynput``. The
match-scoring logic (``obs_snooker.match_state``) lives in a separate,
OBS-independent module so it can be imported and unit-tested outside of OBS
(see ``tests/``).

The scoreboard window (``obs_snooker.scoreboard_window``) is deliberately
**not** created in-process here. On macOS, embedding a Tk window directly
inside OBS's own interpreter can crash OBS itself, because Tk's Cocoa
backend conflicts with the ``NSApplication`` instance OBS (a Qt/Cocoa app)
already runs - this is a real, observed crash (SIGABRT inside
``Tk_Alloc3DBorderFromObj`` / Cocoa color handling), not a bug in this
script. Instead, the scoreboard window is launched as a **separate OS
process** (see ``open_scoreboard_window()``) that polls the browser
overlay's JSON endpoint for live data, exactly like the browser overlay's own
JavaScript does. See ``AGENTS.md`` for the full explanation.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from typing import Any

import obspython as obs
from pynput import keyboard

# OBS does not always put the script's own directory on sys.path, so make the
# sibling `obs_snooker` package importable explicitly.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# OBS's "Reload Scripts" button only re-executes *this* top-level file from
# scratch - it does NOT clear Python's own module cache (`sys.modules`), so
# any already-imported `obs_snooker.*` submodule keeps running its old code
# even after a reload, silently out of sync with this file's fresh code
# (this has caused real, confusing `AttributeError`s during development:
# this file calls a new method/property that only exists in the on-disk
# source, while the cached submodule object in memory still predates it).
# Dropping these submodules from `sys.modules` before importing them again
# below forces a genuinely fresh import every time this file runs, so
# "Reload Scripts" in OBS reliably picks up edits to any file under
# `obs_snooker/` without needing a full OBS restart.
for _cached_module_name in [
    name for name in sys.modules if name == "obs_snooker" or name.startswith("obs_snooker.")
]:
    del sys.modules[_cached_module_name]

from obs_snooker.darwin_keycode_patch import apply_macos_keycode_context_workaround
from obs_snooker.match_state import MatchState
from obs_snooker.overlay_server import OverlayServer
from obs_snooker.player_database import PlayerDatabase, default_players_file_path

# Global variables
listener: keyboard.Listener | None = None
selected_source: str = ""

# Default port for the browser-source overlay's local HTTP server. Also
# where the standalone scoreboard window process polls its live data from.
OVERLAY_DEFAULT_PORT = 8710

scoreboard_process: subprocess.Popen | None = None
player_manager_process: subprocess.Popen | None = None
overlay_server: OverlayServer | None = None
match_state = MatchState()
player_db = PlayerDatabase(default_players_file_path(_SCRIPT_DIR))

# Sentinel value for the "Player 1"/"Player 2" dropdowns meaning "no player
# selected from the database" - keeps whatever name is already set (e.g. the
# built-in "Player 1"/"Player 2" defaults, or a name set by a previous
# session), so a fresh install with an empty player database still works.
_NO_PLAYER_SELECTED = ""

# Cached most-recent settings object, so button callbacks (which only receive
# `(props, prop)`, not `settings`) can still read auxiliary properties such
# as how many points the next "+" button press should add, or a manually
# configured Python interpreter path.
current_settings: Any = None

# Maps each OBS hotkey's internal name to its registered hotkey id (returned
# by `obs_hotkey_register_frontend`), so bindings can be saved/loaded and
# unregistered on unload. Populated by `register_hotkeys()`.
hotkey_ids: dict[str, int] = {}

# Names used for the "freeze reference overlay" referee tool's own scene
# items/filter, so they can be found and reused across toggles instead of
# creating a new one every time.
FREEZE_SOURCE_NAME = "Snooker Freeze Reference"
FREEZE_FILTER_NAME = "Snooker Freeze Opacity"
freeze_overlay_active: bool = False


def _score_url() -> str:
    """URL of the JSON score endpoint the scoreboard window process polls."""
    if overlay_server is not None:
        return overlay_server.score_url
    return f"http://127.0.0.1:{OVERLAY_DEFAULT_PORT}/api/score"


def _find_python_executable() -> str | None:
    """Best-effort search for a real, launchable Python interpreter.

    OBS's embedded interpreter does not reliably set ``sys.executable`` to a
    real Python binary (it may point at the OBS application itself), so this
    is only used as one candidate among a few. Users can override detection
    entirely via the "Python interpreter (for scoreboard window)" script
    property, which takes priority whenever it is set.
    """
    if current_settings is not None:
        override = obs.obs_data_get_string(current_settings, "python_executable")
        if override:
            return override

    candidates = []
    if sys.executable and "python" in os.path.basename(sys.executable).lower():
        candidates.append(sys.executable)
    for prefix in (sys.exec_prefix, sys.base_exec_prefix):
        # Windows installs keep python.exe in the prefix root (pythonw.exe = no console window).
        candidates.append(os.path.join(prefix, "pythonw.exe"))
        candidates.append(os.path.join(prefix, "python.exe"))
        candidates.append(os.path.join(prefix, "bin", f"python{sys.version_info.major}.{sys.version_info.minor}"))
        candidates.append(os.path.join(prefix, "bin", "python3"))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def open_scoreboard_window() -> None:
    """Launches the scoreboard window as a standalone OS process, if not already running."""
    global scoreboard_process
    if scoreboard_process is not None and scoreboard_process.poll() is None:
        return  # already running

    python_executable = _find_python_executable()
    if python_executable is None:
        print(
            "[obs-snooker] Could not find a Python interpreter to launch the scoreboard window. "
            "Set 'Python interpreter (for scoreboard window)' in the script's properties panel."
        )
        return

    scoreboard_process = subprocess.Popen(
        [python_executable, "-m", "obs_snooker.scoreboard_window", "--url", _score_url()],
        cwd=_SCRIPT_DIR,
    )


def close_scoreboard_window() -> None:
    """Terminates the scoreboard window process, if running."""
    global scoreboard_process
    if scoreboard_process is None:
        return
    if scoreboard_process.poll() is None:
        scoreboard_process.terminate()
        try:
            scoreboard_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            scoreboard_process.kill()
    scoreboard_process = None


def open_player_manager_window() -> None:
    """Launches the player-manager window as a standalone OS process, if not already running.

    Like the scoreboard window, this runs as a separate process rather than
    in-process, to avoid the same real Tk-in-OBS crash on macOS (see
    `open_scoreboard_window()` and `AGENTS.md`).
    """
    global player_manager_process
    if player_manager_process is not None and player_manager_process.poll() is None:
        return  # already running

    python_executable = _find_python_executable()
    if python_executable is None:
        print(
            "[obs-snooker] Could not find a Python interpreter to launch the player manager window. "
            "Set 'Python interpreter (for scoreboard window)' in the script's properties panel."
        )
        return

    player_manager_process = subprocess.Popen(
        [python_executable, "-m", "obs_snooker.player_manager_window", "--db", player_db.path.as_posix()],
        cwd=_SCRIPT_DIR,
    )


def close_player_manager_window() -> None:
    """Terminates the player-manager window process, if running."""
    global player_manager_process
    if player_manager_process is None:
        return
    if player_manager_process.poll() is None:
        player_manager_process.terminate()
        try:
            player_manager_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            player_manager_process.kill()
    player_manager_process = None


def _populate_player_list(prop: Any) -> None:
    """(Re-)fills a player-selection dropdown from the on-disk player database."""
    obs.obs_property_list_clear(prop)
    obs.obs_property_list_add_string(prop, "(custom name below)", _NO_PLAYER_SELECTED)
    for player in player_db.list_players():
        obs.obs_property_list_add_string(prop, player.display_name(), player.id)


def _populate_source_list(prop: Any) -> None:
    """Fills a dropdown with the names of every source currently in OBS (any type)."""
    obs.obs_property_list_clear(prop)
    obs.obs_property_list_add_string(prop, "(none selected)", "")
    sources = obs.obs_enum_sources()
    if sources is not None:
        for src in sources:
            name = obs.obs_source_get_name(src)
            obs.obs_property_list_add_string(prop, name, name)
        obs.source_list_release(sources)


def _screenshot_output_dir() -> str | None:
    """Best-effort guess at the folder OBS saves "Take Screenshot" output into.

    OBS does not expose a dedicated "get screenshot folder" API to Python;
    in practice it saves screenshots into the same folder configured for
    recordings, which *is* exposed via `obs_frontend_get_current_record_output_path()`.
    Confirmed correct in practice (matches where OBS actually writes
    screenshots), but kept as a best-effort guess since it isn't a
    documented guarantee across every OBS version/platform.
    """
    try:
        path = obs.obs_frontend_get_current_record_output_path()
    except AttributeError:
        return None
    return path or None


# State for the in-progress "wait for the screenshot file" poll, driven by
# an `obs.timer_add()` callback rather than a blocking `time.sleep()` loop.
# A blocking loop on the thread that triggered the screenshot would prevent
# OBS's own event loop from ever actually finishing writing the file (OBS's
# screenshot capture is asynchronous and needs its main loop to keep
# running) - so polling must yield control back to OBS between checks,
# which `obs.timer_add()` does for free since OBS itself calls it back.
_freeze_poll_dir: str | None = None
_freeze_poll_existing_files: set[str] = set()
_freeze_poll_deadline: float = 0.0
_freeze_poll_on_found: Any = None
# Once a new file is *detected*, its size is tracked across polls and must
# stay unchanged (and non-zero) for `_FREEZE_POLL_STABLE_TICKS` consecutive
# polls before it's considered "fully written" and handed to `on_found()`.
# Without this, the file can be picked up the instant it's *created* but
# before OBS has finished writing its actual pixel data, so it gets loaded
# as a 0-byte/truncated image - which `image_source` then silently fails to
# decode (the scene item's "file" path looks correctly set in OBS's UI, but
# nothing is actually drawn), exactly the bug this guards against.
_freeze_poll_candidate_path: str | None = None
_freeze_poll_candidate_size: int = -1
_freeze_poll_candidate_stable_ticks: int = 0
_FREEZE_POLL_STABLE_TICKS = 3
_FREEZE_POLL_INTERVAL_MS = 100
_FREEZE_POLL_TIMEOUT_S = 8.0
_SCREENSHOT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")


def _freeze_poll_tick() -> None:
    """`obs.timer_add()` callback: checks once for a new, fully-written screenshot file."""
    global _freeze_poll_dir, _freeze_poll_on_found
    global _freeze_poll_candidate_path, _freeze_poll_candidate_size, _freeze_poll_candidate_stable_ticks

    directory = _freeze_poll_dir
    on_found = _freeze_poll_on_found
    if directory is None or on_found is None:
        obs.timer_remove(_freeze_poll_tick)
        return

    def _finish(result: str | None) -> None:
        global _freeze_poll_dir, _freeze_poll_on_found
        global _freeze_poll_candidate_path, _freeze_poll_candidate_size, _freeze_poll_candidate_stable_ticks
        obs.timer_remove(_freeze_poll_tick)
        _freeze_poll_dir = None
        _freeze_poll_on_found = None
        _freeze_poll_candidate_path = None
        _freeze_poll_candidate_size = -1
        _freeze_poll_candidate_stable_ticks = 0
        on_found(result)

    if _freeze_poll_candidate_path is None:
        try:
            current_files = set(os.listdir(directory))
        except OSError:
            current_files = set()

        new_files = [
            f for f in (current_files - _freeze_poll_existing_files) if f.lower().endswith(_SCREENSHOT_EXTENSIONS)
        ]
        if new_files:
            newest = max(new_files, key=lambda f: os.path.getmtime(os.path.join(directory, f)))
            _freeze_poll_candidate_path = os.path.join(directory, newest)
            _freeze_poll_candidate_size = -1
            _freeze_poll_candidate_stable_ticks = 0
        elif time.monotonic() >= _freeze_poll_deadline:
            print(
                f"[obs-snooker] Freeze overlay: timed out waiting for a screenshot file in '{directory}' "
                f"(saw {len(current_files)} existing file(s) there)."
            )
            _finish(None)
        return

    # A candidate file was found on a previous tick - check whether it has
    # finished being written (its size has stopped changing).
    try:
        size = os.path.getsize(_freeze_poll_candidate_path)
    except OSError:
        size = -1  # file briefly missing/locked - treat as "still changing"

    if size > 0 and size == _freeze_poll_candidate_size:
        _freeze_poll_candidate_stable_ticks += 1
    else:
        _freeze_poll_candidate_stable_ticks = 0
    _freeze_poll_candidate_size = size

    if _freeze_poll_candidate_stable_ticks >= _FREEZE_POLL_STABLE_TICKS:
        _finish(_freeze_poll_candidate_path)
        return

    if time.monotonic() >= _freeze_poll_deadline:
        print(
            f"[obs-snooker] Freeze overlay: found '{_freeze_poll_candidate_path}' but its size never "
            "stabilized in time - using it anyway, but it may be incomplete."
        )
        _finish(_freeze_poll_candidate_path)


def _wait_for_new_screenshot_async(directory: str, existing_files: set[str], on_found: Any) -> None:
    """Starts polling ``directory`` for a new, fully-written screenshot file, calling ``on_found(path_or_None)`` once done.

    Uses `obs.timer_add()` instead of a blocking loop, since
    `obs_frontend_take_source_screenshot()` is itself asynchronous and needs
    OBS's own main loop to keep running in order to actually finish writing
    the file - blocking that same thread with `time.sleep()` would prevent
    the screenshot from ever completing, causing a timeout every time
    regardless of whether the directory guess was correct. Also waits for
    the detected file's size to stop changing before considering it ready,
    since the file can briefly exist with partial/zero content while OBS is
    still writing it - see the comments on the module-level `_freeze_poll_*`
    state above for why this matters.
    """
    global _freeze_poll_dir, _freeze_poll_existing_files, _freeze_poll_deadline, _freeze_poll_on_found
    global _freeze_poll_candidate_path, _freeze_poll_candidate_size, _freeze_poll_candidate_stable_ticks
    _freeze_poll_dir = directory
    _freeze_poll_existing_files = existing_files
    _freeze_poll_deadline = time.monotonic() + _FREEZE_POLL_TIMEOUT_S
    _freeze_poll_on_found = on_found
    _freeze_poll_candidate_path = None
    _freeze_poll_candidate_size = -1
    _freeze_poll_candidate_stable_ticks = 0
    obs.timer_add(_freeze_poll_tick, _FREEZE_POLL_INTERVAL_MS)


def _copy_sceneitem_transform(from_item: Any, to_item: Any) -> None:
    """Copies position/scale/rotation/bounds/alignment from one scene item to another.

    Used to make the frozen reference image line up exactly 1:1 with the
    live camera source it was captured from, regardless of how that camera
    source has been positioned/scaled/cropped in the current scene.
    """
    pos = obs.vec2()
    obs.obs_sceneitem_get_pos(from_item, pos)
    obs.obs_sceneitem_set_pos(to_item, pos)

    scale = obs.vec2()
    obs.obs_sceneitem_get_scale(from_item, scale)
    obs.obs_sceneitem_set_scale(to_item, scale)

    obs.obs_sceneitem_set_rot(to_item, obs.obs_sceneitem_get_rot(from_item))

    bounds = obs.vec2()
    obs.obs_sceneitem_get_bounds(from_item, bounds)
    obs.obs_sceneitem_set_bounds(to_item, bounds)

    obs.obs_sceneitem_set_bounds_type(to_item, obs.obs_sceneitem_get_bounds_type(from_item))
    obs.obs_sceneitem_set_bounds_alignment(to_item, obs.obs_sceneitem_get_bounds_alignment(from_item))
    obs.obs_sceneitem_set_alignment(to_item, obs.obs_sceneitem_get_alignment(from_item))


def _ensure_opacity_filter(source: Any, opacity_percent: int) -> None:
    """Adds (or updates) a "Color Correction" filter on ``source`` to control its opacity.

    OBS has no per-source "opacity" property of its own; the built-in
    "Color Correction" filter (internal id ``color_filter``) is the
    standard way to make a source semi-transparent. Its exact availability
    / property name has only been confirmed on recent OBS versions (28+) -
    if this silently has no visible effect, the filter id/property name may
    need updating for an older OBS release.
    """
    existing = obs.obs_source_get_filter_by_name(source, FREEZE_FILTER_NAME)
    filter_settings = obs.obs_data_create()
    obs.obs_data_set_double(filter_settings, "opacity", float(opacity_percent))
    if existing is None:
        new_filter = obs.obs_source_create("color_filter", FREEZE_FILTER_NAME, filter_settings, None)
        obs.obs_data_release(filter_settings)
        if new_filter is not None:
            obs.obs_source_filter_add(source, new_filter)
            obs.obs_source_release(new_filter)
    else:
        obs.obs_source_update(existing, filter_settings)
        obs.obs_data_release(filter_settings)
        obs.obs_source_release(existing)


def _freeze_opacity() -> int:
    if current_settings is None:
        return 50
    return obs.obs_data_get_int(current_settings, "freeze_opacity")


def show_freeze_overlay() -> None:
    """Referee tool: freezes the current camera frame as a semi-transparent overlay.

    Takes a screenshot of the configured camera source and displays it, at
    the same position/size/crop as the live camera, as a translucent image
    on top of the live feed in the current scene - so a referee can see
    exactly where the balls were before they got disturbed, and nudge them
    back into place underneath the "ghost" image. Call `hide_freeze_overlay()`
    (or press the same hotkey again, see `toggle_freeze_overlay()`) once done.

    The screenshot itself is asynchronous (OBS writes the file on its own
    graphics/IO thread), so this function kicks it off and returns
    immediately; `_on_screenshot_found()` below does the actual overlay
    setup once the file shows up (or logs a timeout if it doesn't).
    """
    camera_source_name = obs.obs_data_get_string(current_settings, "freeze_camera_source") if current_settings else ""
    if not camera_source_name:
        print("[obs-snooker] Freeze overlay: no camera source configured (see script properties).")
        return

    scene_source = obs.obs_frontend_get_current_scene()
    if scene_source is None:
        print("[obs-snooker] Freeze overlay: no current scene.")
        return
    scene = obs.obs_scene_from_source(scene_source)

    camera_item = obs.obs_scene_find_source(scene, camera_source_name)
    if camera_item is None:
        print(f"[obs-snooker] Freeze overlay: source '{camera_source_name}' not found in the current scene.")
        obs.obs_source_release(scene_source)
        return

    camera_source = obs.obs_get_source_by_name(camera_source_name)
    if camera_source is None:
        obs.obs_source_release(scene_source)
        return

    screenshot_dir = _screenshot_output_dir()
    if not screenshot_dir or not os.path.isdir(screenshot_dir):
        print("[obs-snooker] Freeze overlay: couldn't determine OBS's screenshot/recording folder.")
        obs.obs_source_release(camera_source)
        obs.obs_source_release(scene_source)
        return

    existing_files = set(os.listdir(screenshot_dir))
    obs.obs_frontend_take_source_screenshot(camera_source)
    obs.obs_source_release(camera_source)

    def _on_screenshot_found(screenshot_path: str | None) -> None:
        global freeze_overlay_active
        try:
            if screenshot_path is None:
                return

            try:
                file_size = os.path.getsize(screenshot_path)
            except OSError:
                file_size = -1
            print(f"[obs-snooker] Freeze overlay: using screenshot '{screenshot_path}' ({file_size} bytes).")

            freeze_item = obs.obs_scene_find_source(scene, FREEZE_SOURCE_NAME)
            if freeze_item is not None:
                freeze_source = obs.obs_sceneitem_get_source(freeze_item)
                settings = obs.obs_data_create()
                obs.obs_data_set_string(settings, "file", screenshot_path)
                obs.obs_source_update(freeze_source, settings)
                obs.obs_data_release(settings)
            else:
                settings = obs.obs_data_create()
                obs.obs_data_set_string(settings, "file", screenshot_path)
                freeze_source = obs.obs_source_create("image_source", FREEZE_SOURCE_NAME, settings, None)
                obs.obs_data_release(settings)
                if freeze_source is None:
                    print("[obs-snooker] Freeze overlay: failed to create the image source.")
                    return
                freeze_item = obs.obs_scene_add(scene, freeze_source)
                obs.obs_source_release(freeze_source)

            _copy_sceneitem_transform(camera_item, freeze_item)
            _ensure_opacity_filter(freeze_source, _freeze_opacity())
            obs.obs_sceneitem_set_visible(freeze_item, True)
            freeze_overlay_active = True
            print("[obs-snooker] Freeze overlay shown.")
        finally:
            obs.obs_source_release(scene_source)

    _wait_for_new_screenshot_async(screenshot_dir, existing_files, _on_screenshot_found)


def hide_freeze_overlay() -> None:
    """Hides the frozen reference overlay (if currently shown), without deleting it.

    Keeping the source around (just hidden) means the next freeze doesn't
    need to recreate it from scratch - it just gets updated in place.
    """
    global freeze_overlay_active

    scene_source = obs.obs_frontend_get_current_scene()
    if scene_source is None:
        return
    scene = obs.obs_scene_from_source(scene_source)
    freeze_item = obs.obs_scene_find_source(scene, FREEZE_SOURCE_NAME)
    if freeze_item is not None:
        obs.obs_sceneitem_set_visible(freeze_item, False)
    obs.obs_source_release(scene_source)

    freeze_overlay_active = False
    print("[obs-snooker] Freeze overlay hidden.")


def toggle_freeze_overlay() -> None:
    if freeze_overlay_active:
        hide_freeze_overlay()
    else:
        show_freeze_overlay()


# Bind the overlay/control HTTP server to all network interfaces, not just
# localhost, so it's reachable from other devices on the same local network
# (e.g. a phone/tablet for the control panel) - not just from this machine.
# Works identically on macOS, Windows, and Linux ("0.0.0.0" is the standard
# cross-platform wildcard bind address). Security note: this means anyone
# on the same local network can reach the control panel's scoring actions
# without authentication - acceptable for a home/venue LAN, but be mindful
# if this machine is ever on a shared/untrusted network.
OVERLAY_BIND_HOST = "0.0.0.0"


def _local_lan_ip() -> str | None:
    """Best-effort cross-platform guess at this machine's LAN IP address.

    Used purely for a friendlier log message (so the overlay/control pages
    can be opened from another device, e.g. a phone, on the same local
    network).

    The trick: opening a UDP "connection" to an arbitrary external address
    doesn't actually send any packets (UDP is connectionless), but makes the
    OS pick which local network interface/IP *would* be used to route
    there, which `getsockname()` then reveals. This works the same way on
    macOS, Windows, and Linux with no extra dependencies - unlike e.g.
    parsing `ifconfig`/`ipconfig` output, which would need separate,
    fragile, platform-specific parsing.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except OSError:
        return None


def start_overlay_server(port: int = OVERLAY_DEFAULT_PORT) -> None:
    """Starts the browser-source overlay's HTTP server, if not already running."""
    global overlay_server
    if overlay_server is not None:
        return
    overlay_server = OverlayServer(
        match_state,
        host=OVERLAY_BIND_HOST,
        port=port,
        player_db=player_db,
        on_config_change=_queue_admin_config,
        on_foul_action=_queue_foul_action,
    )
    _sync_server_config_from_settings()
    try:
        overlay_server.start()
        _log_overlay_urls()
    except OSError as e:
        print(f"[obs-snooker] Failed to start overlay server on port {port}: {e}")
        overlay_server = None


# Match-setup changes made on the /admin page arrive on the HTTP server's
# thread; they are queued here and written into the OBS script settings by a
# timer on OBS's own thread (obspython's data API is not meant for other threads).
_pending_admin_configs: list[dict[str, Any]] = []
_ADMIN_SYNC_INTERVAL_MS = 500


def _queue_admin_config(config: dict[str, Any]) -> None:
    _pending_admin_configs.append(config)


_pending_foul_actions: list[dict[str, Any]] = []
_foul_publish_counter = 0
_foul_capture_busy = False


def _queue_foul_action(request: dict[str, Any]) -> None:
    _pending_foul_actions.append(request)


def _capture_camera_screenshot(on_done: Any) -> None:
    """Takes a screenshot of the configured camera source and calls ``on_done(bytes)`` with its content.

    Reuses the asynchronous screenshot-file polling of the freeze overlay;
    the screenshot file is deleted again once its content has been read.
    """
    global _foul_capture_busy
    camera_source_name = obs.obs_data_get_string(current_settings, "freeze_camera_source") if current_settings else ""
    if not camera_source_name:
        print("[obs-snooker] Foul control: no camera source configured.")
        return
    if _foul_capture_busy or _freeze_poll_on_found is not None:
        print("[obs-snooker] Foul control: another screenshot is still in progress.")
        return
    camera_source = obs.obs_get_source_by_name(camera_source_name)
    if camera_source is None:
        print(f"[obs-snooker] Foul control: source '{camera_source_name}' not found.")
        return
    screenshot_dir = _screenshot_output_dir()
    if not screenshot_dir or not os.path.isdir(screenshot_dir):
        print("[obs-snooker] Foul control: couldn't determine OBS's screenshot/recording folder.")
        obs.obs_source_release(camera_source)
        return

    existing_files = set(os.listdir(screenshot_dir))
    _foul_capture_busy = True
    obs.obs_frontend_take_source_screenshot(camera_source)
    obs.obs_source_release(camera_source)

    def _found(path: str | None) -> None:
        global _foul_capture_busy
        _foul_capture_busy = False
        if path is None:
            print("[obs-snooker] Foul control: screenshot did not appear in time.")
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            os.remove(path)
        except OSError as e:
            print(f"[obs-snooker] Foul control: couldn't read the screenshot: {e}")
            return
        on_done(data)

    _wait_for_new_screenshot_async(screenshot_dir, existing_files, _found)


def _process_foul_actions() -> None:
    """Runs foul & miss control actions requested from the /admin page, on OBS's own thread."""
    while _pending_foul_actions:
        request = _pending_foul_actions.pop(0)
        action = request["action"]
        if action == "foul_settings" and current_settings is not None:
            obs.obs_data_set_string(current_settings, "freeze_camera_source", request["camera_source"])
            obs.obs_data_set_int(current_settings, "freeze_opacity", request["opacity"])
        elif action in ("foul_reference", "foul_compare") and overlay_server is not None:
            kind = "reference" if action == "foul_reference" else "current"
            server = overlay_server
            _capture_camera_screenshot(lambda data, kind=kind, server=server: server.set_foul_image(kind, data))


def _publish_foul_options() -> None:
    """Gives the /admin page the camera sources and settings (sources can only be listed on OBS's thread)."""
    global _foul_publish_counter
    if overlay_server is None or current_settings is None:
        return
    # Re-listing every source twice a second is wasteful, so do it only every few ticks.
    if _foul_publish_counter % 4 == 0 or overlay_server.foul_options is None:
        sources: list[str] = []
        enumerated = obs.obs_enum_sources()
        if enumerated is not None:
            sources = sorted(obs.obs_source_get_name(src) for src in enumerated)
            obs.source_list_release(enumerated)
        overlay_server.foul_options = {
            "sources": sources,
            "camera_source": obs.obs_data_get_string(current_settings, "freeze_camera_source"),
            "opacity": obs.obs_data_get_int(current_settings, "freeze_opacity"),
        }
    _foul_publish_counter += 1


def _apply_pending_admin_configs() -> None:
    """`obs.timer_add()` callback: persists /admin changes into the script settings."""
    _process_foul_actions()
    _publish_foul_options()
    if not _pending_admin_configs or current_settings is None:
        return
    latest = _pending_admin_configs[-1]
    _pending_admin_configs.clear()
    ids = latest["config"]
    for number in (1, 2):
        obs.obs_data_set_string(current_settings, f"player{number}_id", ids[f"player{number}_id"])
        obs.obs_data_set_string(current_settings, f"player{number}_name", ids[f"player{number}_custom_name"])
    obs.obs_data_set_string(current_settings, "match_title", latest["match_title"])
    obs.obs_data_set_int(current_settings, "frames_to_win", latest["frames_to_win"])
    refresh_scoreboard()


def _sync_server_config_from_settings() -> None:
    """Lets the /admin page show the player selection currently stored in the OBS settings."""
    if overlay_server is None or current_settings is None:
        return
    for number in (1, 2):
        overlay_server.config[f"player{number}_id"] = obs.obs_data_get_string(current_settings, f"player{number}_id")
        overlay_server.config[f"player{number}_custom_name"] = obs.obs_data_get_string(
            current_settings, f"player{number}_name"
        )


def _log_overlay_urls() -> None:
    """Logs the overlay/control URLs: a localhost variant (for this machine, e.g. an OBS Browser Source)
    and - when detectable - a LAN IP variant for connecting from another device (e.g. a phone/tablet).
    """
    if overlay_server is None:
        return
    print(f"[obs-snooker] Browser overlay available at {overlay_server.url}")
    print(f"[obs-snooker] Control panel available at {overlay_server.control_url}")
    print(f"[obs-snooker] Admin page available at {overlay_server.admin_url}")
    lan_ip = _local_lan_ip()
    if lan_ip:
        print(
            f"[obs-snooker] On the same local network (e.g. from a phone/tablet), try: "
            f"http://{lan_ip}:{overlay_server.port}/overlay and http://{lan_ip}:{overlay_server.port}/control"
        )


def stop_overlay_server() -> None:
    """Stops the browser-source overlay's HTTP server, if running."""
    global overlay_server
    if overlay_server is None:
        return
    overlay_server.stop()
    overlay_server = None


def _apply_overlay_port(port: int) -> None:
    """Starts the overlay server, or restarts it if the configured port changed."""
    global overlay_server
    if overlay_server is None:
        start_overlay_server(port)
        return
    if overlay_server.port == port:
        return
    try:
        overlay_server.restart(port=port)
        _log_overlay_urls()
    except OSError as e:
        print(f"[obs-snooker] Failed to restart overlay server on port {port}: {e}")


def refresh_scoreboard() -> None:
    """Updates the plain OBS text source. The Tk window and browser overlay read live state on their own."""
    update_obs_text(match_state.as_overlay_text())


def _add_break_points(player: int, delta: int, ball: str | None = None) -> None:
    """Adds points to the pending break of the player at the table.

    The break is added to that player's frame score when the turn is
    switched (see ``MatchState.commit_break``); points for the player who is
    not at the table are ignored.
    """
    if player != match_state.current_player:
        print(f"[obs-snooker] Ignoring points for player {player}: not at the table")
        return
    match_state.add_break_points(delta, ball=ball)
    refresh_scoreboard()


def _add_points(player: int, value: int | None = None, ball: str | None = None) -> None:
    """Adds points to a player's current break (counted into the frame score on switch turn).

    If ``value`` is omitted, uses the match's configured "points per shot"
    setting (for the generic +points button/hotkey). Otherwise adds exactly
    ``value`` points, used by the per-ball-value hotkeys/buttons below -
    potting the black ball is always worth 7 points, regardless of whatever
    the generic "points per shot" field happens to be set to.
    """
    if value is None:
        value = obs.obs_data_get_int(current_settings, "points_per_shot") if current_settings is not None else 1
        value = value or 1
    _add_break_points(player, value, ball)


def _subtract_points(player: int, value: int | None = None) -> None:
    """Subtracts points from a player's current-frame score (e.g. to undo a mis-click)."""
    if value is None:
        value = obs.obs_data_get_int(current_settings, "points_per_shot") if current_settings is not None else 1
        value = value or 1
    _add_break_points(player, -value)


def _win_frame(player: int) -> None:
    match_state.win_frame(player)
    refresh_scoreboard()


def _switch_turn() -> None:
    match_state.switch_turn()
    refresh_scoreboard()


def _reset_frame_scores() -> None:
    match_state.reset_frame_scores()
    refresh_scoreboard()


def _reset_match() -> None:
    match_state.reset_match()
    refresh_scoreboard()


def _correct_frame_score(player: int) -> None:
    """Manually overrides a player's current-frame score from its "correction" input field.

    Intended for fixing a scoring mistake directly (e.g. a referee notices
    the on-screen score drifted from the real one), as an alternative to
    nudging it back into place with repeated +points/-points presses.
    """
    if current_settings is None:
        return
    value = obs.obs_data_get_int(current_settings, f"player{player}_frame_score_correction")
    match_state.set_frame_score(player, value)
    refresh_scoreboard()


def _correct_frames(player: int) -> None:
    """Manually overrides a player's frames-won tally from its "correction" input field."""
    if current_settings is None:
        return
    value = obs.obs_data_get_int(current_settings, f"player{player}_frames_correction")
    match_state.set_frames(player, value)
    refresh_scoreboard()


def _on_player1_plus(props: Any, prop: Any) -> bool:
    _add_points(1)
    return True


def _on_player2_plus(props: Any, prop: Any) -> bool:
    _add_points(2)
    return True


def _on_player1_minus(props: Any, prop: Any) -> bool:
    _subtract_points(1)
    return True


def _on_player2_minus(props: Any, prop: Any) -> bool:
    _subtract_points(2)
    return True


def _on_player1_win_frame(props: Any, prop: Any) -> bool:
    _win_frame(1)
    return True


def _on_player2_win_frame(props: Any, prop: Any) -> bool:
    _win_frame(2)
    return True


def _on_switch_turn(props: Any, prop: Any) -> bool:
    _switch_turn()
    return True


def _on_reset_frame_scores(props: Any, prop: Any) -> bool:
    _reset_frame_scores()
    return True


def _on_reset_match(props: Any, prop: Any) -> bool:
    _reset_match()
    return True


def _on_correct_player1_frame_score(props: Any, prop: Any) -> bool:
    _correct_frame_score(1)
    return True


def _on_correct_player2_frame_score(props: Any, prop: Any) -> bool:
    _correct_frame_score(2)
    return True


def _on_correct_player1_frames(props: Any, prop: Any) -> bool:
    _correct_frames(1)
    return True


def _on_correct_player2_frames(props: Any, prop: Any) -> bool:
    _correct_frames(2)
    return True


def _make_pot_button_callback(player: int, value: int, ball: str) -> Any:
    """Builds a button callback that adds a fixed ball value to one player's frame score."""

    def callback(props: Any, prop: Any) -> bool:
        _add_points(player, value, ball)
        return True

    return callback


# Point value of each ball, for one-press-per-pot scoring - the standard way
# to operate a live snooker scoreboard (press the button for whichever ball
# was just potted, instead of repeatedly pressing a generic "+1" button).
_BALL_VALUES: list[tuple[str, int]] = [
    ("red", 1),
    ("yellow", 2),
    ("green", 3),
    ("brown", 4),
    ("blue", 5),
    ("pink", 6),
    ("black", 7),
]


# Actions exposed as native OBS hotkeys (bindable in OBS's own
# Settings -> Hotkeys panel - completely separate from this script's
# properties panel). Each tuple is (internal hotkey name, description shown
# in the Hotkeys list, the zero-argument action to run when pressed).
_HOTKEY_ACTIONS: list[tuple[str, str, Any]] = [
    ("player1_plus_points", "Snooker: Player 1 +points (configured amount)", lambda: _add_points(1)),
    ("player2_plus_points", "Snooker: Player 2 +points (configured amount)", lambda: _add_points(2)),
    ("player1_minus_points", "Snooker: Player 1 -points (configured amount)", lambda: _subtract_points(1)),
    ("player2_minus_points", "Snooker: Player 2 -points (configured amount)", lambda: _subtract_points(2)),
    ("player1_win_frame", "Snooker: Player 1 wins frame", lambda: _win_frame(1)),
    ("player2_win_frame", "Snooker: Player 2 wins frame", lambda: _win_frame(2)),
    ("switch_turn", "Snooker: Switch turn (who is at the table)", _switch_turn),
    ("reset_frame_scores", "Snooker: Reset current frame score (re-rack)", _reset_frame_scores),
    ("toggle_freeze_overlay", "Snooker: Toggle freeze reference overlay (referee tool)", toggle_freeze_overlay),
    ("freeze_now", "Snooker: Freeze now (take a new screenshot, referee tool)", show_freeze_overlay),
    ("hide_freeze_overlay", "Snooker: Hide freeze reference overlay (referee tool)", hide_freeze_overlay),
]
for _player in (1, 2):
    for _ball_name, _value in _BALL_VALUES:
        _HOTKEY_ACTIONS.append(
            (
                f"player{_player}_pot_{_ball_name}",
                f"Snooker: Player {_player} potted {_ball_name} (+{_value})",
                lambda p=_player, v=_value, b=_ball_name: _add_points(p, v, b),
            )
        )
del _player, _ball_name, _value


def _make_hotkey_callback(action: Any) -> Any:
    """Wraps a zero-argument action as an OBS hotkey callback.

    OBS calls hotkey callbacks on both key-down (`pressed=True`) and
    key-up (`pressed=False`); we only want to act once, on key-down.
    """

    def callback(pressed: bool) -> None:
        if pressed:
            action()

    return callback


def register_hotkeys() -> None:
    """Registers this script's actions as native OBS hotkeys.

    Unlike the properties-panel buttons (which only work while this
    script's Scripts dialog is open), hotkeys registered this way show up in
    OBS's own Settings -> Hotkeys panel, where the user can bind any
    key/combo (including keys on a Bluetooth keyboard) to trigger them
    directly - no need to reopen this script's panel mid-match.
    """
    for name, description, action in _HOTKEY_ACTIONS:
        hotkey_ids[name] = obs.obs_hotkey_register_frontend(
            f"obs_snooker.{name}", description, _make_hotkey_callback(action)
        )


def unregister_hotkeys() -> None:
    for hotkey_id in hotkey_ids.values():
        obs.obs_hotkey_unregister(hotkey_id)
    hotkey_ids.clear()


def _on_show_scoreboard_window(props: Any, prop: Any) -> bool:
    """Reopens the scoreboard window if it was previously closed (e.g. by accident)."""
    open_scoreboard_window()
    return True


def _on_toggle_freeze_overlay(props: Any, prop: Any) -> bool:
    toggle_freeze_overlay()
    return True


def _on_freeze_now(props: Any, prop: Any) -> bool:
    """Always takes a fresh screenshot and shows it, even if the overlay is already visible.

    Unlike the toggle action, this doesn't hide the overlay if it's already
    shown - it's the dedicated "I want a brand new freeze right now" action,
    e.g. if the first freeze was taken at a bad moment (camera lag, wrong
    instant) and needs retaking without first hiding it.
    """
    show_freeze_overlay()
    return True


def _on_hide_freeze_overlay(props: Any, prop: Any) -> bool:
    hide_freeze_overlay()
    return True


def _on_refresh_source_list(props: Any, prop: Any) -> bool:
    """Re-reads the list of OBS sources, in case a camera source was added/renamed after this panel opened."""
    prop_obj = obs.obs_properties_get(props, "freeze_camera_source")
    if prop_obj is not None:
        _populate_source_list(prop_obj)
    return True


def _on_manage_players(props: Any, prop: Any) -> bool:
    """Opens the standalone player-manager window (add/rename/delete players)."""
    open_player_manager_window()
    return True


def _on_open_control_panel(props: Any, prop: Any) -> bool:
    """Opens the browser control panel (/control) in the default browser, no OBS Interact needed."""
    if overlay_server is not None:
        webbrowser.open(overlay_server.control_url)
    return True


def _on_open_admin_page(props: Any, prop: Any) -> bool:
    """Opens the browser admin page (/admin) in the default browser."""
    if overlay_server is not None:
        webbrowser.open(overlay_server.admin_url)
    return True


def _on_refresh_player_list(props: Any, prop: Any) -> bool:
    """Re-reads the player database from disk and refreshes both dropdowns.

    Needed because OBS's properties panel does not automatically notice
    changes made to the database file by the separate player-manager window
    process while the panel is already open.
    """
    player_db.load()
    player1_prop = obs.obs_properties_get(props, "player1_id")
    player2_prop = obs.obs_properties_get(props, "player2_id")
    if player1_prop is not None:
        _populate_player_list(player1_prop)
    if player2_prop is not None:
        _populate_player_list(player2_prop)
    return True


def script_description() -> str:
    return (
        "Snooker scoreboard: text source, browser overlay, and a second-monitor scoreboard window, "
        "all kept in sync. Tip: bind keys to the scoring actions in OBS's own "
        "Settings -> Hotkeys panel (search for 'Snooker') so you don't need to reopen this panel mid-match."
    )


def script_properties() -> Any:
    props = obs.obs_properties_create()

    # --- Match setup ---------------------------------------------------
    match_group = obs.obs_properties_create()

    # Selection of the OBS text source to be updated
    p = obs.obs_properties_add_list(
        match_group, "source_name", "Text source:",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )

    sources = obs.obs_enum_sources()
    if sources is not None:
        for src in sources:
            source_id = obs.obs_source_get_unversioned_id(src)
            if source_id in ["text_gdiplus", "text_ft2_source"]:  # Text sources
                name = obs.obs_source_get_name(src)
                obs.obs_property_list_add_string(p, name, name)
        obs.source_list_release(sources)

    # Player selection: pick an existing player from the database, or leave
    # on "(custom name below)" and type a one-off name in the text field
    # below it (e.g. for a casual match that doesn't need to be saved).
    player1_list = obs.obs_properties_add_list(
        match_group, "player1_id", "Player 1:", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    _populate_player_list(player1_list)
    obs.obs_properties_add_text(match_group, "player1_name", "Player 1 custom name:", obs.OBS_TEXT_DEFAULT)

    player2_list = obs.obs_properties_add_list(
        match_group, "player2_id", "Player 2:", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    _populate_player_list(player2_list)
    obs.obs_properties_add_text(match_group, "player2_name", "Player 2 custom name:", obs.OBS_TEXT_DEFAULT)

    obs.obs_properties_add_button(match_group, "open_admin_page", "Open admin page in browser", _on_open_admin_page)
    obs.obs_properties_add_button(match_group, "manage_players", "Manage players...", _on_manage_players)
    obs.obs_properties_add_button(
        match_group, "refresh_player_list", "Refresh player list (after editing players)", _on_refresh_player_list
    )
    obs.obs_properties_add_text(
        match_group, "match_title", "Match title (browser overlay only, optional):", obs.OBS_TEXT_DEFAULT
    )

    # Race format: how many frame wins are needed to win the match (e.g. 10
    # for a "best of 19 frames" final).
    obs.obs_properties_add_int(match_group, "frames_to_win", "Frames to win (race to):", 1, 35, 1)

    obs.obs_properties_add_group(props, "match_group", "Match setup", obs.OBS_GROUP_NORMAL, match_group)

    # --- Scoring controls ------------------------------------------------
    scoring_group = obs.obs_properties_create()

    # How many points the "+points" buttons below add to the current frame
    # score per click (e.g. set to 4 before pressing "+points" for a break of
    # 4 reds, or leave at 1 and click repeatedly).
    obs.obs_properties_add_int(scoring_group, "points_per_shot", "Points per shot (for +points buttons):", 1, 147, 1)

    obs.obs_properties_add_button(
        scoring_group, "open_control_panel", "Open control panel in browser", _on_open_control_panel
    )
    obs.obs_properties_add_button(scoring_group, "player1_plus", "Player 1 +points", _on_player1_plus)
    obs.obs_properties_add_button(scoring_group, "player2_plus", "Player 2 +points", _on_player2_plus)
    obs.obs_properties_add_button(
        scoring_group, "player1_minus", "Player 1 -points (undo a mistake)", _on_player1_minus
    )
    obs.obs_properties_add_button(
        scoring_group, "player2_minus", "Player 2 -points (undo a mistake)", _on_player2_minus
    )
    obs.obs_properties_add_button(scoring_group, "switch_turn", "Switch turn (who is at the table)", _on_switch_turn)
    obs.obs_properties_add_button(scoring_group, "player1_win_frame", "Player 1 wins frame", _on_player1_win_frame)
    obs.obs_properties_add_button(scoring_group, "player2_win_frame", "Player 2 wins frame", _on_player2_win_frame)
    obs.obs_properties_add_button(
        scoring_group, "reset_frame_scores", "Reset current frame score (re-rack)", _on_reset_frame_scores
    )
    obs.obs_properties_add_button(scoring_group, "reset_match", "Reset whole match", _on_reset_match)
    obs.obs_properties_add_text(
        scoring_group,
        "hotkeys_info",
        "Tip: the +points/-points, ball-value, wins-frame, switch-turn, and reset-frame buttons here are "
        "also available as OBS Hotkeys, so you don't have to reopen this panel during a match - go to "
        "Settings -> Hotkeys and search for \"Snooker\" to bind keys (e.g. from a Bluetooth keyboard or a "
        "Stream Deck) to them.",
        obs.OBS_TEXT_INFO,
    )

    # One-press-per-pot scoring: a button per ball value (red=1 .. black=7)
    # for each player, so potting a ball only takes a single click/hotkey
    # instead of repeatedly pressing a generic "+1" button. Nested one level
    # deeper (sub-group per player) since this is a dense block of buttons
    # that belongs together but is secondary to the main scoring controls.
    for player in (1, 2):
        ball_group = obs.obs_properties_create()
        for ball_name, value in _BALL_VALUES:
            obs.obs_properties_add_button(
                ball_group,
                f"player{player}_pot_{ball_name}",
                f"+{value} ({ball_name})",
                _make_pot_button_callback(player, value, ball_name),
            )
        obs.obs_properties_add_group(
            scoring_group, f"player{player}_balls_group", f"Player {player}: potted ball",
            obs.OBS_GROUP_NORMAL, ball_group,
        )

    obs.obs_properties_add_group(props, "scoring_group", "Scoring controls", obs.OBS_GROUP_NORMAL, scoring_group)

    # --- Manual score correction ------------------------------------------
    # In case a mistake needs fixing directly, rather than being nudged back
    # into place with +points/-points presses.
    correction_group = obs.obs_properties_create()
    obs.obs_properties_add_int(
        correction_group, "player1_frame_score_correction", "Player 1 current frame score:", 0, 147, 1
    )
    obs.obs_properties_add_button(
        correction_group, "apply_player1_frame_score_correction", "Set player 1 frame score",
        _on_correct_player1_frame_score,
    )
    obs.obs_properties_add_int(
        correction_group, "player2_frame_score_correction", "Player 2 current frame score:", 0, 147, 1
    )
    obs.obs_properties_add_button(
        correction_group, "apply_player2_frame_score_correction", "Set player 2 frame score",
        _on_correct_player2_frame_score,
    )
    obs.obs_properties_add_int(correction_group, "player1_frames_correction", "Player 1 frames won:", 0, 35, 1)
    obs.obs_properties_add_button(
        correction_group, "apply_player1_frames_correction", "Set player 1 frames won", _on_correct_player1_frames
    )
    obs.obs_properties_add_int(correction_group, "player2_frames_correction", "Player 2 frames won:", 0, 35, 1)
    obs.obs_properties_add_button(
        correction_group, "apply_player2_frames_correction", "Set player 2 frames won", _on_correct_player2_frames
    )
    obs.obs_properties_add_group(
        props, "correction_group", "Manual score correction (fix a mistake)", obs.OBS_GROUP_NORMAL, correction_group
    )

    # --- Freeze reference overlay (referee tool) --------------------------
    # Freeze a screenshot of the camera as a semi-transparent overlay on top
    # of the live feed, so the referee can see exactly where the balls were
    # before they got disturbed and nudge them back into place. See
    # AGENTS.md for how this works and its caveats.
    freeze_group = obs.obs_properties_create()
    freeze_source_prop = obs.obs_properties_add_list(
        freeze_group, "freeze_camera_source", "Camera source:", obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    _populate_source_list(freeze_source_prop)
    obs.obs_properties_add_button(
        freeze_group, "refresh_source_list", "Refresh source list", _on_refresh_source_list
    )
    obs.obs_properties_add_int(freeze_group, "freeze_opacity", "Overlay opacity (%):", 0, 100, 5)
    obs.obs_properties_add_button(
        freeze_group, "freeze_now", "Freeze now (take a new screenshot)", _on_freeze_now
    )
    obs.obs_properties_add_button(
        freeze_group, "hide_freeze_overlay", "Hide freeze overlay", _on_hide_freeze_overlay
    )
    obs.obs_properties_add_button(
        freeze_group, "toggle_freeze_overlay", "Toggle freeze reference overlay", _on_toggle_freeze_overlay
    )
    obs.obs_properties_add_text(
        freeze_group,
        "freeze_info",
        "Takes a screenshot of the camera source above and shows it semi-transparently on top of the live "
        "feed in the current scene, so a referee can see exactly where the balls were and put them back. "
        "\"Freeze now\" always retakes the screenshot (even if one is already showing); \"Hide\" just hides "
        "it; \"Toggle\" does whichever of the two makes sense for a single button/hotkey. Requires OBS 28+ "
        "for the transparency effect; relies on OBS's configured recording folder to find the screenshot "
        "file.",
        obs.OBS_TEXT_INFO,
    )
    obs.obs_properties_add_group(
        props, "freeze_group", "Freeze reference overlay (referee tool)", obs.OBS_GROUP_NORMAL, freeze_group
    )

    # --- Scoreboard window (second monitor) -------------------------------
    scoreboard_group = obs.obs_properties_create()
    obs.obs_properties_add_button(
        scoreboard_group, "show_scoreboard_window", "Show scoreboard window", _on_show_scoreboard_window
    )
    obs.obs_properties_add_text(
        scoreboard_group,
        "python_executable",
        "Python interpreter (for scoreboard window), optional:",
        obs.OBS_TEXT_DEFAULT,
    )
    obs.obs_properties_add_text(
        scoreboard_group,
        "python_executable_info",
        "The scoreboard window runs as a separate process (avoids a real OBS/Tk crash on macOS). "
        "Leave blank to auto-detect; set this to a real 'python3' binary path if auto-detection fails.",
        obs.OBS_TEXT_INFO,
    )
    obs.obs_properties_add_group(
        props, "scoreboard_group", "Scoreboard window (second monitor)", obs.OBS_GROUP_NORMAL, scoreboard_group
    )

    # --- Browser-source overlay --------------------------------------------
    # A small local HTTP server serves a styled, transparent-background
    # scoreboard page for use as an OBS Browser Source.
    overlay_group = obs.obs_properties_create()
    obs.obs_properties_add_int(overlay_group, "overlay_port", "Browser overlay port:", 1024, 65535, 1)
    obs.obs_properties_add_text(
        overlay_group,
        "overlay_info",
        "Add a Browser Source in OBS pointing at the URL above (e.g. http://127.0.0.1:8710/overlay). "
        "Keep 'Shutdown source when not visible' unchecked so it keeps updating live.\n\n"
        "Tip: there's also a touch-friendly control panel at http://127.0.0.1:8710/control with clickable "
        "scoring buttons (points per ball, wins frame, switch turn...), reading/writing the exact same "
        "live match state as everything else. Add it as a *second*, separate Browser Source (it doesn't "
        "need to be visible on stream - keep it on an unused scene, or hide it), then right-click it in "
        "OBS and choose \"Interact\" to click its buttons directly, without ever opening this Scripts "
        "panel or using a keyboard hotkey.",
        obs.OBS_TEXT_INFO,
    )
    obs.obs_properties_add_group(
        props, "overlay_group", "Browser overlay", obs.OBS_GROUP_NORMAL, overlay_group
    )

    return props


def script_defaults(settings: Any) -> None:
    obs.obs_data_set_default_string(settings, "player1_id", _NO_PLAYER_SELECTED)
    obs.obs_data_set_default_string(settings, "player2_id", _NO_PLAYER_SELECTED)
    obs.obs_data_set_default_string(settings, "player1_name", match_state.player1_name)
    obs.obs_data_set_default_string(settings, "player2_name", match_state.player2_name)
    obs.obs_data_set_default_string(settings, "match_title", match_state.match_title)
    obs.obs_data_set_default_int(settings, "frames_to_win", match_state.frames_to_win)
    obs.obs_data_set_default_int(settings, "points_per_shot", 1)
    obs.obs_data_set_default_int(settings, "overlay_port", OVERLAY_DEFAULT_PORT)
    obs.obs_data_set_default_string(settings, "python_executable", "")
    obs.obs_data_set_default_int(settings, "player1_frame_score_correction", 0)
    obs.obs_data_set_default_int(settings, "player2_frame_score_correction", 0)
    obs.obs_data_set_default_int(settings, "player1_frames_correction", 0)
    obs.obs_data_set_default_int(settings, "player2_frames_correction", 0)
    obs.obs_data_set_default_string(settings, "freeze_camera_source", "")
    obs.obs_data_set_default_int(settings, "freeze_opacity", 50)


def _apply_player_selection(settings: Any, player: int) -> None:
    """Applies the "Player N" dropdown selection, falling back to the custom name field.

    If a database player is selected (`player{N}_id` is not the "custom
    name" sentinel), that player's name and country win. Otherwise, the
    free-text `player{N}_name` field is used as-is (and the country is left
    unset), so one-off matches don't require adding someone to the database
    first.
    """
    player_id = obs.obs_data_get_string(settings, f"player{player}_id")
    custom_name = obs.obs_data_get_string(settings, f"player{player}_name")

    name_attr = f"player{player}_name"
    country_attr = f"player{player}_country"

    db_player = player_db.get_player(player_id) if player_id else None
    if db_player is not None:
        setattr(match_state, name_attr, db_player.name)
        setattr(match_state, country_attr, db_player.country)
    elif custom_name:
        setattr(match_state, name_attr, custom_name)
        setattr(match_state, country_attr, "")


def script_update(settings: Any) -> None:
    global selected_source, current_settings
    current_settings = settings
    selected_source = obs.obs_data_get_string(settings, "source_name")
    _apply_player_selection(settings, 1)
    _apply_player_selection(settings, 2)
    match_state.match_title = obs.obs_data_get_string(settings, "match_title")
    match_state.frames_to_win = obs.obs_data_get_int(settings, "frames_to_win") or 1

    overlay_port = obs.obs_data_get_int(settings, "overlay_port") or OVERLAY_DEFAULT_PORT
    _apply_overlay_port(overlay_port)
    _sync_server_config_from_settings()

    refresh_scoreboard()


def update_obs_text(text: str) -> None:
    """Updates the text of the selected OBS source."""
    if not selected_source:
        return

    source = obs.obs_get_source_by_name(selected_source)
    if source is not None:
        settings = obs.obs_data_create()
        obs.obs_data_set_string(settings, "text", text)
        obs.obs_source_update(source, settings)
        obs.obs_data_release(settings)
        obs.obs_source_release(source)


def on_press(key: keyboard.Key | keyboard.KeyCode | None) -> None:
    """Handles a key press from the BT keyboard."""
    try:
        if key == keyboard.Key.f1:
            update_obs_text("PRESSED: F1")
        elif key == keyboard.Key.f2:
            update_obs_text("PRESSED: F2")
        elif isinstance(key, keyboard.KeyCode) and key.char == "1":
            update_obs_text("Number 1 displayed")
    except Exception as e:
        print(f"Error while handling key press: {e}")


def start_keyboard_listener() -> None:
    """Starts capturing key presses.

    Must be called from the main thread. `pynput.keyboard.Listener` is
    itself a `threading.Thread` subclass, so `.start()` already spawns its
    own background thread for the actual event loop - do not wrap this call
    in another `threading.Thread`.

    On macOS, `Listener._run()` (which always executes on the listener's own
    background thread, regardless of which thread called `.start()`) also
    performs Carbon/Text-Services-Manager calls to read the current keyboard
    input source. Inside a real Cocoa host application such as OBS, doing so
    off the main thread causes a real crash (SIGTRAP /
    `dispatch_assert_queue_fail` inside `TSMGetInputSourceProperty`). See
    `obs_snooker.darwin_keycode_patch` and `AGENTS.md` for the full
    explanation and the workaround applied here: pre-computing that data on
    *this* (main) thread before the listener starts, so its background
    thread never has to touch Carbon/TSM itself.
    """
    apply_macos_keycode_context_workaround()

    global listener
    listener = keyboard.Listener(on_press=on_press)
    listener.start()


def script_load(settings: Any) -> None:
    """Called when the script is loaded in OBS."""
    script_update(settings)
    start_keyboard_listener()
    start_overlay_server()
    open_scoreboard_window()
    register_hotkeys()
    obs.timer_add(_apply_pending_admin_configs, _ADMIN_SYNC_INTERVAL_MS)
    for name, hotkey_id in hotkey_ids.items():
        saved_bindings = obs.obs_data_get_array(settings, f"hotkey_{name}")
        obs.obs_hotkey_load(hotkey_id, saved_bindings)
        obs.obs_data_array_release(saved_bindings)


def script_save(settings: Any) -> None:
    """Called by OBS whenever it needs to persist this script's settings (incl. on save/exit).

    Hotkey key bindings are stored separately from `obs_data_set_default_*`
    settings, via `obs_hotkey_save`/`obs_hotkey_load`, so the user's chosen
    key bindings (set in Settings -> Hotkeys) survive an OBS restart.
    """
    for name, hotkey_id in hotkey_ids.items():
        saved_bindings = obs.obs_hotkey_save(hotkey_id)
        obs.obs_data_set_array(settings, f"hotkey_{name}", saved_bindings)
        obs.obs_data_array_release(saved_bindings)


def script_unload() -> None:
    """Called when the script is unloaded from OBS."""
    global listener
    if listener is not None:
        listener.stop()
    close_scoreboard_window()
    close_player_manager_window()
    stop_overlay_server()
    unregister_hotkeys()
    obs.timer_remove(_freeze_poll_tick)
    obs.timer_remove(_apply_pending_admin_configs)
