"""Standalone Tkinter scoreboard window, sized for a dedicated 1920x1080 monitor.

This module intentionally has no dependency on ``obspython`` so it can be
imported and tested completely outside of OBS (e.g. with plain ``python`` or
``pytest``), as long as Tkinter/Tcl-Tk is available on the system.

The whole scoreboard is drawn on a single ``Canvas`` from a 1920x1080 design
grid that is scaled to the window size. Tk has no native gradients/shadows,
so gradients are generated as ``PhotoImage`` strips and "3D" text is drawn as
several offset copies of the text (extrusion + drop shadow).
"""

from __future__ import annotations

from pathlib import Path
import base64
import io
import tkinter as tk
from tkinter import font as tkfont

from .match_state import MatchState

# Palette taken from the club website (snooker-morava.cz): navy, club blue,
# green and lime accent, with a white header strip for the logo.
_BG_TOP = "#1d2f9e"
_BG_BOTTOM = "#060b36"
_PANEL_TOP = "#2a47bd"
_PANEL_BOTTOM = "#11206d"
_PANEL_EDGE = "#4c68dc"
_ACCENT = "#97bf26"  # club lime
_ACCENT_DARK = "#5f7a14"
_SCORE_STOPS = ((0.0, "#12a85a"), (0.5, "#007a3a"), (0.5001, "#006a31"), (1.0, "#003a1d"))
_TEXT = "#ffffff"
_TEXT_DEPTH = "#0a1148"
_MUTED_TEXT = "#b4c0f0"
_INACTIVE_PILL_TOP = "#3b57cc"
_INACTIVE_PILL_BOTTOM = "#26399a"
_FRAMES_PILL_STOPS = ((0.0, "#1a2b86"), (1.0, "#0a1348"))
_BALL_ORDER = ("red", "yellow", "green", "brown", "blue", "pink", "black")
_BALL_COLORS = {
    "red": "#d91f1f", "yellow": "#f2d21b", "green": "#1f9a44", "brown": "#8a5226",
    "blue": "#2f6fe0", "pink": "#f27ab8", "black": "#1a1a1a",
}
_BALL_HIGHLIGHT = "#ffffff"
_HEADER_TEXT = "#020939"
_CLUB_BLUE = "#0396c4"
_LOGO_WIDTHS = (140, 220, 300, 450, 600)
_LOGO_DESIGN_W = 300
_ASSETS_DIR = Path(__file__).parent / "assets"

_NAME_BASE_SIZE = 66
# Character-count thresholds at which the player-name font shrinks, so long
# names don't force the panel wider than its allotted half of the window
# (mirrors the equivalent `.long`/`.very-long` CSS classes in the browser
# overlay's `overlay.css`/`overlay.js`).
_NAME_LONG_THRESHOLD = 14
_NAME_VERY_LONG_THRESHOLD = 20
_DESIGN_W = 1920
_DESIGN_H = 1080

_HEADER_H = 150
_PANEL_Y1, _PANEL_Y2 = 190, 960
_PANEL_X = ((90, 890), (1030, 1830))


def _name_font_size(name: str, base_size: int = _NAME_BASE_SIZE) -> int:
    """Picks a smaller font size for longer player names.

    Pure function (no Tk dependency) so it can be unit-tested without a
    display.
    """
    length = len(name)
    if length > _NAME_VERY_LONG_THRESHOLD:
        return round(base_size * 0.55)
    if length > _NAME_LONG_THRESHOLD:
        return round(base_size * 0.75)
    return base_size


def _rounded_rect_points(x1: float, y1: float, x2: float, y2: float, radius: float) -> list[float]:
    """Returns the point list for a rounded-rectangle ``Canvas.create_polygon(..., smooth=True)`` call."""
    return [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _gradient_color(stops: tuple, t: float) -> str:
    """Linearly interpolates a color along ``stops`` = ((position, "#rrggbb"), ...) at ``t`` in 0..1."""
    t = min(1.0, max(0.0, t))
    for (p1, c1), (p2, c2) in zip(stops, stops[1:]):
        if t <= p2:
            f = 0.0 if p2 == p1 else (t - p1) / (p2 - p1)
            r1, g1, b1 = _hex_to_rgb(c1)
            r2, g2, b2 = _hex_to_rgb(c2)
            return "#%02x%02x%02x" % (round(r1 + (r2 - r1) * f), round(g1 + (g2 - g1) * f), round(b1 + (b2 - b1) * f))
    return stops[-1][1]


class ScoreboardWindow:
    """Window showing the current match state, drawn entirely on one canvas.

    Intended to be moved to a second, dedicated monitor so a referee/operator
    (and anyone else in the room) can see both players' names, frames won,
    the points in the frame in progress, the current break, who is at the
    table, and the race format. The layout scales with the window size.

    The window is not driven by ``Tk.mainloop()`` in tests; instead call
    :meth:`pump` to process pending Tk events without blocking.
    """

    def __init__(self, state: MatchState) -> None:
        self.root = tk.Tk()
        self.root.title("Snooker Scoreboard")
        # Design size is 1920x1080; on smaller screens start smaller (same
        # aspect ratio) and let the layout scale with the window.
        fit = min(1.0, self.root.winfo_screenwidth() * 0.9 / _DESIGN_W, self.root.winfo_screenheight() * 0.85 / _DESIGN_H)
        self.root.geometry(f"{round(_DESIGN_W * fit)}x{round(_DESIGN_H * fit)}")
        self.root.minsize(480, 270)

        # F11 / Escape let the operator toggle true OS fullscreen once the
        # window has been dragged onto the second monitor.
        self._fullscreen = False
        self.root.bind("<F11>", self._toggle_fullscreen)
        self.root.bind("<Escape>", self._exit_fullscreen)

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bg=_BG_BOTTOM)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self._scale = fit
        self._offset = (0.0, 0.0)
        self._render_job = None
        self._images: dict = {}  # cache of generated gradient/logo images
        self._logo_images: dict[int, tk.PhotoImage] = {}
        self._fonts: dict = {}
        self.player1_name_font = tkfont.Font(family="Helvetica", size=-_NAME_BASE_SIZE, weight="bold")
        self.player2_name_font = tkfont.Font(family="Helvetica", size=-_NAME_BASE_SIZE, weight="bold")
        self._name_size = _NAME_BASE_SIZE
        self._last_key = None

        self.match_title_var = tk.StringVar()
        self.player1_name_var = tk.StringVar()
        self.player2_name_var = tk.StringVar()
        self.player1_frames_var = tk.StringVar()
        self.player2_frames_var = tk.StringVar()
        self.player1_frame_score_var = tk.StringVar()
        self.player2_frame_score_var = tk.StringVar()
        self.player1_break_var = tk.StringVar()
        self.player2_break_var = tk.StringVar()
        self.race_label_var = tk.StringVar()
        self.current_player = 1
        self.break_balls: list[str] = []

        # Foul & miss control: camera screenshots shown instead of the scoreboard.
        self.foul_mode = "off"
        self.foul_opacity = 50
        self._foul_reference: bytes | None = None
        self._foul_current: bytes | None = None
        self._foul_photo: object | None = None
        self._foul_photo_key: tuple | None = None

        self.update_from_state(state)

    # ---- foul & miss control ---------------------------------------------

    def set_foul(self, mode: str, reference: bytes | None = None, current: bytes | None = None, opacity: int = 50) -> None:
        """Shows the reference screenshot ("reference"), the current one blended over it ("compare"), or the scoreboard ("off")."""
        self.foul_mode = mode if mode in ("reference", "compare") else "off"
        self.foul_opacity = max(0, min(100, opacity))
        self._foul_reference = reference
        self._foul_current = current
        self._foul_photo_key = None
        self._render()

    def _foul_image(self, width: int, height: int) -> object | None:
        """Builds the picture to show (fitted into width x height), blending current over reference when possible."""
        key = (width, height, self.foul_mode, self.foul_opacity, id(self._foul_reference), id(self._foul_current))
        if key == self._foul_photo_key:
            return self._foul_photo
        photo = None
        try:
            from PIL import Image, ImageTk

            def load(data: bytes) -> "Image.Image":
                return Image.open(io.BytesIO(data)).convert("RGB")

            base = load(self._foul_reference) if self._foul_reference else None
            top = load(self._foul_current) if self._foul_current else None
            if base is not None and top is not None and self.foul_mode == "compare":
                if top.size != base.size:
                    top = top.resize(base.size)
                base = Image.blend(base, top, self.foul_opacity / 100)
            elif base is None:
                base = top
            if base is not None:
                scale = min(width / base.width, height / base.height)
                size = (max(1, round(base.width * scale)), max(1, round(base.height * scale)))
                photo = ImageTk.PhotoImage(base.resize(size))
        except ImportError:
            # Without Pillow only PNG without blending is possible: show the newest image.
            data = self._foul_current if self.foul_mode == "compare" and self._foul_current else self._foul_reference
            if data:
                try:
                    photo = tk.PhotoImage(data=base64.b64encode(data))
                except tk.TclError:
                    photo = None
        except Exception:
            photo = None
        self._foul_photo, self._foul_photo_key = photo, key
        return photo

    def _render_foul(self, width: int, height: int) -> None:
        c = self.canvas
        c.create_rectangle(0, 0, width, height, fill="#000000", outline="")
        photo = self._foul_image(width, height)
        if photo is not None:
            c.create_image(width / 2, height / 2, image=photo, anchor="center")
        if self.foul_mode == "compare":
            caption = f"CURRENT over REFERENCE ({self.foul_opacity}%)"
        else:
            caption = "REFERENCE"
        font = self._font(34)
        c.create_text(width / 2 + 2, 42, text=caption, font=font, fill="#000000")
        c.create_text(width / 2, 40, text=caption, font=font, fill=_ACCENT)

    # ---- drawing helpers -------------------------------------------------

    def _font(self, px: float, weight: str = "bold") -> tkfont.Font:
        size = max(6, round(px * self._scale))
        key = (size, weight)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(family="Helvetica", size=-size, weight=weight)
        return self._fonts[key]

    def _gradient(self, w: int, h: int, stops: tuple) -> tk.PhotoImage:
        w, h = max(1, w), max(1, h)
        key = ("g", w, h, stops)
        if key not in self._images:
            image = tk.PhotoImage(width=w, height=h)
            for y in range(h):
                image.put(_gradient_color(stops, y / max(1, h - 1)), to=(0, y, w, y + 1))
            self._images[key] = image
        return self._images[key]

    def _x(self, value: float) -> float:
        return self._offset[0] + value * self._scale

    def _y(self, value: float) -> float:
        return self._offset[1] + value * self._scale

    def _grad_rect(self, x1: float, y1: float, x2: float, y2: float, stops: tuple, tags: str = "") -> None:
        """Draws a vertical-gradient rectangle given in design coordinates."""
        left, top, right, bottom = self._x(x1), self._y(y1), self._x(x2), self._y(y2)
        image = self._gradient(round(right - left), round(bottom - top), stops)
        self.canvas.create_image(left, top, image=image, anchor="nw", tags=tags)

    def _text3d(self, x: float, y: float, text: str, font: tkfont.Font, fill: str, depth: int = 4, shadow: str = _TEXT_DEPTH) -> None:
        """Draws text with an extruded 3D look: stacked darker copies plus a soft drop shadow."""
        step = max(1, round(self._scale * 1.5)) if depth else 0
        cx, cy = self._x(x), self._y(y)
        for i in range(depth + 3, depth, -1):
            self.canvas.create_text(cx + i * step, cy + i * step, text=text, font=font, fill="#000000" if i > depth + 1 else "#02041a")
        for i in range(depth, 0, -1):
            self.canvas.create_text(cx + i * step, cy + i * step, text=text, font=font, fill=shadow)
        self.canvas.create_text(cx, cy, text=text, font=font, fill=fill)

    def _pill(self, x1: float, y1: float, x2: float, y2: float, top: str, bottom: str, outline: str = "") -> None:
        """Rounded "glossy" pill: dark base, lighter top half for a highlight."""
        c = self.canvas
        l, t, r, b = self._x(x1), self._y(y1), self._x(x2), self._y(y2)
        radius = (b - t) / 2
        c.create_polygon(_rounded_rect_points(l + 2, t + 3, r + 2, b + 3, radius), smooth=True, fill="#050a2c")
        c.create_polygon(_rounded_rect_points(l, t, r, b, radius), smooth=True, fill=bottom, outline=outline)
        inset = (b - t) * 0.08
        c.create_polygon(
            _rounded_rect_points(l + inset, t + inset, r - inset, (t + b) / 2, radius * 0.6), smooth=True, fill=top
        )

    # ---- rendering ---------------------------------------------------------

    def _on_canvas_configure(self, event: tk.Event) -> None:
        if event.width < 200 or event.height < 120:
            return
        scale = min(event.width / _DESIGN_W, event.height / _DESIGN_H)
        self._scale = scale
        self._offset = ((event.width - _DESIGN_W * scale) / 2, (event.height - _DESIGN_H * scale) / 2)
        if self._render_job is not None:
            self.root.after_cancel(self._render_job)
        self._render_job = self.root.after(40, self._render)

    def _logo_image(self) -> tk.PhotoImage | None:
        wanted = _LOGO_DESIGN_W * self._scale
        width = min(_LOGO_WIDTHS, key=lambda w: abs(w - wanted))
        if width not in self._logo_images:
            try:
                self._logo_images[width] = tk.PhotoImage(file=str(_ASSETS_DIR / f"logo_{width}.png"))
            except tk.TclError:
                return None
        return self._logo_images[width]

    def _render(self) -> None:
        self._render_job = None
        c = self.canvas
        width, height = c.winfo_width(), c.winfo_height()
        if width < 200 or height < 120:
            return
        c.delete("all")
        if self.foul_mode != "off":
            self._render_foul(width, height)
            return
        sc = self._scale

        # Background and header (full window width, ignoring the letterbox offset).
        c.create_image(0, 0, image=self._gradient(width, height, ((0.0, _BG_TOP), (1.0, _BG_BOTTOM))), anchor="nw")
        header_bottom = self._y(_HEADER_H)
        c.create_image(0, 0, image=self._gradient(width, round(header_bottom), ((0.0, "#ffffff"), (1.0, "#d5dcee"))), anchor="nw")
        c.create_rectangle(0, header_bottom, width, header_bottom + 7 * sc, fill=_CLUB_BLUE, outline="")
        c.create_rectangle(0, header_bottom + 7 * sc, width, header_bottom + 10 * sc, fill="#02587a", outline="")
        logo = self._logo_image()
        if logo is not None:
            c.create_image(self._x(20), header_bottom / 2, image=logo, anchor="w")
        title = self.match_title_var.get()
        if title:
            title_font = self._font(40)
            cx, cy = width / 2, header_bottom / 2
            c.create_text(cx + 2, cy + 2, text=title, font=title_font, fill="#ffffff")
            c.create_text(cx, cy, text=title, font=title_font, fill=_HEADER_TEXT)

        names = (self.player1_name_var.get(), self.player2_name_var.get())
        frames = (self.player1_frames_var.get(), self.player2_frames_var.get())
        scores = (self.player1_frame_score_var.get(), self.player2_frame_score_var.get())
        breaks = (self.player1_break_var.get(), self.player2_break_var.get())
        name_px = self._name_size
        for index in range(2):
            self._render_panel(index, names[index], frames[index], scores[index], breaks[index], name_px)

        race = self.race_label_var.get()
        c.create_text(width / 2 + 1, self._y(1018) + 1, text=race, font=self._font(34, "normal"), fill="#000000")
        c.create_text(width / 2, self._y(1018), text=race, font=self._font(34, "normal"), fill=_TEXT)
        self.canvas.create_text(self._x(960), self._y(575), text="\u2013", font=self._font(110), fill=_ACCENT)

    def _draw_break_balls(self, cx: float, cy: float) -> None:
        """Draws the balls potted in the current break as shaded coloured balls with counts."""
        counts = [(name, self.break_balls.count(name)) for name in _BALL_ORDER if name in self.break_balls]
        if not counts:
            return
        radius = 15 * self._scale
        font = self._font(30)
        item_w = (2 * radius + 8 * self._scale + 26 * self._scale)
        gap = 22 * self._scale
        total = len(counts) * item_w + (len(counts) - 1) * gap
        x = self._x(cx) - total / 2
        y = self._y(cy)
        for name, count in counts:
            color = _BALL_COLORS[name]
            c = self.canvas
            c.create_oval(x + 2, y - radius + 3, x + 2 * radius + 2, y + radius + 3, fill="#02041a", outline="")
            c.create_oval(x, y - radius, x + 2 * radius, y + radius, fill=color, outline="#000000")
            c.create_oval(x + radius * 0.35, y - radius * 0.7, x + radius * 0.85, y - radius * 0.25, fill=_BALL_HIGHLIGHT, outline="", stipple="gray50")
            c.create_text(x + 2 * radius + 8 * self._scale, y, text=f"\u00d7{count}", anchor="w", font=font, fill=_TEXT)
            x += item_w + gap

    def _render_panel(self, index: int, name: str, frames: str, score: str, brk: str, name_px: int) -> None:
        c = self.canvas
        x1, x2 = _PANEL_X[index]
        y1, y2 = _PANEL_Y1, _PANEL_Y2
        cx = (x1 + x2) / 2
        active = self.current_player == index + 1

        # Drop shadow, optional glow for whoever is on the table, then the gradient body.
        c.create_rectangle(self._x(x1) + 10, self._y(y1) + 14, self._x(x2) + 10, self._y(y2) + 14, fill="#040826", outline="")
        if active:
            for grow, color in ((14, "#3c4a12"), (8, "#6a8a1a")):
                c.create_rectangle(self._x(x1 - grow), self._y(y1 - grow), self._x(x2 + grow), self._y(y2 + grow), fill=color, outline="")
        self._grad_rect(x1, y1, x2, y2, ((0.0, _PANEL_TOP), (1.0, _PANEL_BOTTOM)))
        c.create_rectangle(
            self._x(x1), self._y(y1), self._x(x2), self._y(y2),
            outline=_ACCENT if active else _PANEL_EDGE, width=max(2, round(5 * self._scale)),
        )
        # Bevel highlight along the top edge.
        c.create_line(self._x(x1) + 4, self._y(y1) + 6, self._x(x2) - 4, self._y(y1) + 6, fill="#7d95ee" if not active else "#c4e063")

        # "ON THE TABLE" turn pill.
        top, bottom = (("#c6e65a", _ACCENT) if active else (_INACTIVE_PILL_TOP, _INACTIVE_PILL_BOTTOM))
        self._pill(cx - 170, y1 + 30, cx + 170, y1 + 86, top, bottom)
        label_font = self._font(24)
        label_color = "#0b2a05" if active else _MUTED_TEXT
        c.create_text(self._x(cx), self._y(y1 + 58), text="ON THE TABLE", font=label_font, fill=label_color)

        # Player name, shrunk until it fits the panel.
        name_font = self.player1_name_font if index == 0 else self.player2_name_font
        size = max(6, round(name_px * self._scale))
        name_font.configure(size=-size)
        max_width = (x2 - x1 - 70) * self._scale
        while size > 8 and name_font.measure(name) > max_width:
            size -= 2
            name_font.configure(size=-size)
        self._text3d(cx, y1 + 140, name, name_font, _TEXT, depth=4)

        # Frame score: glossy green box with extruded digits.
        bx1, bx2, by1, by2 = cx - 230, cx + 230, y1 + 205, y1 + 495
        c.create_rectangle(self._x(bx1) + 6, self._y(by1) + 8, self._x(bx2) + 6, self._y(by2) + 8, fill="#040826", outline="")
        self._grad_rect(bx1, by1, bx2, by2, _SCORE_STOPS)
        c.create_rectangle(self._x(bx1), self._y(by1), self._x(bx2), self._y(by2), outline="#02281a", width=max(2, round(4 * self._scale)))
        c.create_line(self._x(bx1) + 3, self._y(by1) + 3, self._x(bx2) - 3, self._y(by1) + 3, fill="#6be0a0")
        self._text3d((bx1 + bx2) / 2, (by1 + by2) / 2 - 4, score, self._font(220), _TEXT, depth=5, shadow="#012413")

        # Current break.
        self._text3d(cx, y1 + 540, brk, self._font(42), _ACCENT, depth=2, shadow="#26340a")
        if active:
            self._draw_break_balls(cx, y1 + 598)

        # Frames won pill.
        fx1, fx2, fy1, fy2 = cx - 120, cx + 120, y1 + 645, y1 + 750
        c.create_rectangle(self._x(fx1) + 4, self._y(fy1) + 6, self._x(fx2) + 4, self._y(fy2) + 6, fill="#040826", outline="")
        self._grad_rect(fx1, fy1, fx2, fy2, _FRAMES_PILL_STOPS)
        c.create_rectangle(self._x(fx1), self._y(fy1), self._x(fx2), self._y(fy2), outline=_PANEL_EDGE, width=max(1, round(2 * self._scale)))
        self._text3d(cx, fy1 + 40, frames, self._font(64), _ACCENT, depth=2, shadow="#26340a")
        c.create_text(self._x(cx), self._y(fy1 + 90), text="FRAMES WON", font=self._font(18), fill=_TEXT)

    def _toggle_fullscreen(self, _event: object = None) -> None:
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)

    def _exit_fullscreen(self, _event: object = None) -> None:
        self._fullscreen = False
        self.root.attributes("-fullscreen", False)

    def update_from_state(self, state: MatchState) -> None:
        """Refreshes every value from the given match state and redraws if anything changed."""
        self.match_title_var.set(state.match_title)
        self.player1_name_var.set(state.player1_name.upper())
        self.player2_name_var.set(state.player2_name.upper())
        # Both names share the same font size (driven by whichever name is
        # longer), so the two panels stay visually balanced.
        longest_name = max(state.player1_name, state.player2_name, key=len)
        self._name_size = _name_font_size(longest_name)
        self.player1_frames_var.set(str(state.player1_frames))
        self.player2_frames_var.set(str(state.player2_frames))
        self.player1_frame_score_var.set(str(state.player1_frame_score))
        self.player2_frame_score_var.set(str(state.player2_frame_score))
        self.player1_break_var.set(f"BREAK  {state.current_break}" if state.current_player == 1 else "BREAK  \u2013")
        self.player2_break_var.set(f"BREAK  {state.current_break}" if state.current_player == 2 else "BREAK  \u2013")
        if state.frames_to_win > 1:
            self.race_label_var.set(f"Race to {state.frames_to_win} \u00b7 best of {state.best_of} frames")
        else:
            self.race_label_var.set("Single frame")
        self.current_player = state.current_player
        self.break_balls = list(state.break_balls)

        key = tuple(v.get() for v in (
            self.match_title_var, self.player1_name_var, self.player2_name_var, self.player1_frames_var,
            self.player2_frames_var, self.player1_frame_score_var, self.player2_frame_score_var,
            self.player1_break_var, self.player2_break_var, self.race_label_var,
        )) + (self.current_player, tuple(self.break_balls))
        if key != self._last_key:
            self._last_key = key
            self._render()

    def pump(self) -> bool:
        """Processes pending Tk events without blocking.

        Returns False if the window has been closed (e.g. by the user),
        signalling that the caller should stop pumping and drop the reference.
        """
        try:
            if not self.root.winfo_exists():
                return False
            self.root.update_idletasks()
            self.root.update()
            return True
        except tk.TclError:
            return False

    def destroy(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def _state_from_payload(payload: dict) -> MatchState:
    """Builds a :class:`MatchState` from a ``/api/score`` JSON payload.

    Pure function (no I/O), kept separate from the polling loop so it can be
    unit-tested without a live HTTP server or a display.
    """
    return MatchState(
        player1_name=payload.get("player1_name", "Player 1"),
        player2_name=payload.get("player2_name", "Player 2"),
        player1_country=payload.get("player1_country", ""),
        player2_country=payload.get("player2_country", ""),
        player1_frames=payload.get("player1_frames", 0),
        player2_frames=payload.get("player2_frames", 0),
        player1_frame_score=payload.get("player1_frame_score", 0),
        player2_frame_score=payload.get("player2_frame_score", 0),
        frames_to_win=payload.get("frames_to_win", 1),
        current_player=payload.get("current_player", 1),
        current_break=payload.get("current_break", 0),
        break_balls=payload.get("break_balls", []),
        match_title=payload.get("match_title", ""),
    )


def _run_demo() -> None:
    """Opens the scoreboard window with static demo data so it can be eyeballed outside of OBS."""
    demo_state = MatchState(
        player1_name="Trump",
        player2_name="O'Sullivan",
        player1_frames=3,
        player2_frames=2,
        player1_frame_score=45,
        player2_frame_score=12,
        frames_to_win=10,
        current_player=1,
        match_title="World Championship - Final",
    )
    window = ScoreboardWindow(demo_state)
    print("Scoreboard preview window opened (static demo data) - close the window to stop.")
    window.root.mainloop()


def _run_live(url: str, poll_interval_ms: int = 400) -> None:
    """Opens the scoreboard window and keeps it updated by polling a JSON score URL.

    This is the entry point used when the scoreboard window is launched as a
    standalone OS process by ``obs-snooker.py`` (see
    ``obs-snooker.py``'s ``open_scoreboard_window()``), rather than being
    created in-process inside OBS's own Python interpreter. Running it as a
    separate process avoids a real macOS crash where Tk's Cocoa backend
    conflicts with OBS's own (Qt/Cocoa) ``NSApplication`` instance when a Tk
    window is created directly inside OBS's embedded interpreter - see
    ``AGENTS.md`` for the full explanation. Because this process gets its own
    dedicated main thread, it is safe to drive it with a normal
    ``Tk.mainloop()`` here, unlike the old in-process approach.
    """
    import json
    import urllib.error
    import urllib.request

    window = ScoreboardWindow(MatchState())
    base_url = url.rsplit("/api/", 1)[0] if "/api/" in url else url.rsplit("/", 1)[0]
    foul_seen: list = [None]

    def fetch(kind: str) -> bytes | None:
        try:
            with urllib.request.urlopen(f"{base_url}/foul/{kind}", timeout=5) as response:
                return response.read()
        except (urllib.error.URLError, OSError):
            return None

    def _sync_foul(foul: dict) -> None:
        mode, version, opacity = foul.get("mode", "off"), foul.get("version"), foul.get("opacity", 50)
        if (mode, version, opacity) == foul_seen[0]:
            return
        if mode == "off":
            window.set_foul("off")
        else:
            window.set_foul(mode, fetch("reference"), fetch("current") if mode == "compare" else None, opacity)
        foul_seen[0] = (mode, version, opacity)

    def poll() -> None:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read())
            window.update_from_state(_state_from_payload(payload))
            _sync_foul(payload.get("foul") or {})
        except (urllib.error.URLError, OSError, ValueError):
            # Overlay server not reachable yet/anymore - keep showing the
            # last known state and try again on the next poll.
            pass
        if window.root.winfo_exists():
            window.root.after(poll_interval_ms, poll)

    window.root.after(poll_interval_ms, poll)
    print(f"Scoreboard window polling {url} - close the window to stop.")
    window.root.mainloop()


def _main() -> None:
    """Command-line entry point.

    Run this file as a module (not directly), from the project root, with:

        python -m obs_snooker.scoreboard_window [--url http://127.0.0.1:8710/api/score]

    Running it directly as a script (e.g. ``python scoreboard_window.py`` or
    an IDE "Run" button on this file) breaks the package's relative imports
    (``from .match_state import ...``) the same way it does for
    ``overlay_server.py`` - see that module's ``_run_preview()`` docstring
    for the full explanation.
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=None,
        help="Poll this /api/score JSON URL for live data (served by OverlayServer). "
        "If omitted, shows static demo data instead.",
    )
    args = parser.parse_args()

    if args.url:
        _run_live(args.url)
    else:
        _run_demo()


if __name__ == "__main__":
    _main()
