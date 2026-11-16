"""Standalone Tkinter scoreboard window, sized for a dedicated 1920x1080 monitor.

This module intentionally has no dependency on ``obspython`` so it can be
imported and tested completely outside of OBS (e.g. with plain ``python`` or
``pytest``), as long as Tkinter/Tcl-Tk is available on the system.

The whole scoreboard is drawn on a single ``Canvas`` from a 1920x1080 design
grid that is scaled to the window size. Tk has no native gradients, shadows
or rounded rectangles, so gradients are generated as ``PhotoImage`` strips,
soft shadows/glows are faked with stacked rounded shapes, and rounded
rectangles are composed from rectangles plus corner circles.
"""

from __future__ import annotations

from pathlib import Path
import base64
import io
import tkinter as tk
from tkinter import font as tkfont

from .match_state import MatchState

# Modern dark theme: deep navy background, flat rounded cards with thin
# borders, and the club's lime (snooker-morava.cz) used only as an accent for
# whoever is at the table and the running break.
_BG_TOP = "#0c1433"
_BG_BOTTOM = "#050817"
_HEADER_BG = "#0e1638"
_HEADER_LINE = "#222c5c"
_PANEL_FILL = "#121a3f"
_PANEL_EDGE = "#243063"
_SHADOW = "#03050f"
_WELL_FILL = "#080d26"  # recessed boxes: frame score, frames won
_WELL_EDGE = "#273266"
_ACCENT = "#97bf26"  # club lime
_ACCENT_TEXT = "#0c1a02"
_TEXT = "#ffffff"
_MUTED_TEXT = "#8a95c2"
_DIM_TEXT = "#4a5587"
_PANEL_RADIUS = 32
_BALL_ORDER = ("red", "yellow", "green", "brown", "blue", "pink", "black")
_BALL_COLORS = {
    "red": "#d91f1f", "yellow": "#f2d21b", "green": "#1f9a44", "brown": "#8a5226",
    "blue": "#2f6fe0", "pink": "#f27ab8", "black": "#2a2a2a",
}
_LOGO_WIDTHS = (140, 220, 300, 450, 600)
_LOGO_DESIGN_W = 300
_ASSETS_DIR = Path(__file__).parent / "assets"
# First installed family wins: macOS fonts first, then their Windows
# counterparts, then Tk's always-available fallback.
_UI_FONTS = ("Avenir Next", "Segoe UI", "Helvetica Neue", "Helvetica", "Arial")
_DIGIT_FONTS = ("DIN Alternate", "Bahnschrift", "Avenir Next", "Segoe UI", "Helvetica")

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


def _mix(color: str, other: str, t: float) -> str:
    """Blends ``color`` towards ``other`` by ``t`` (0 = color, 1 = other)."""
    return _gradient_color(((0.0, color), (1.0, other)), t)


def _spaced(text: str) -> str:
    """Adds hair spaces between letters - Tk has no letter-spacing option for small caps labels."""
    return " ".join(text)


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
        installed = set(tkfont.families(self.root))
        self._ui_family = next((f for f in _UI_FONTS if f in installed), "Helvetica")
        self._digit_family = next((f for f in _DIGIT_FONTS if f in installed), "Helvetica")
        self.player1_name_font = tkfont.Font(family=self._ui_family, size=-_NAME_BASE_SIZE, weight="bold")
        self.player2_name_font = tkfont.Font(family=self._ui_family, size=-_NAME_BASE_SIZE, weight="bold")
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

    def _font(self, px: float, weight: str = "bold", digits: bool = False) -> tkfont.Font:
        size = max(6, round(px * self._scale))
        family = self._digit_family if digits else self._ui_family
        key = (size, weight, family)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(family=family, size=-size, weight=weight)
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

    def _fill_round(self, left: float, top: float, right: float, bottom: float, radius: float, color: str) -> None:
        """Solid rounded rectangle in canvas pixels, built from two rectangles and four corner circles.

        A ``smooth=True`` polygon only gives tight, slightly wobbly corners in
        Tk, so true circular corners are composed by hand instead.
        """
        c = self.canvas
        r = max(0.0, min(radius, (right - left) / 2, (bottom - top) / 2))
        c.create_rectangle(left + r, top, right - r, bottom, fill=color, outline="")
        c.create_rectangle(left, top + r, right, bottom - r, fill=color, outline="")
        for cx, cy in ((left + r, top + r), (right - r, top + r), (left + r, bottom - r), (right - r, bottom - r)):
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

    def _round_rect(self, x1: float, y1: float, x2: float, y2: float, radius: float, fill: str, outline: str = "", width: float = 0) -> None:
        """Draws a rounded rectangle given in design coordinates, optionally with a border drawn inside its edge."""
        left, top, right, bottom = self._x(x1), self._y(y1), self._x(x2), self._y(y2)
        r = radius * self._scale
        if outline and width:
            w = max(1, round(width * self._scale))
            self._fill_round(left, top, right, bottom, r, outline)
            left, top, right, bottom, r = left + w, top + w, right - w, bottom - w, max(0, r - w)
        self._fill_round(left, top, right, bottom, r, fill)

    def _soft_shadow(self, x1: float, y1: float, x2: float, y2: float, radius: float, base: str, offset: float = 14) -> None:
        """Fakes a blurred drop shadow with a few stacked, progressively darker rounded rectangles."""
        for spread, t in ((14, 0.35), (8, 0.6), (3, 0.85)):
            self._round_rect(
                x1 - spread, y1 - spread + offset, x2 + spread, y2 + spread + offset,
                radius + spread, _mix(base, _SHADOW, t),
            )

    def _glow(self, x1: float, y1: float, x2: float, y2: float, radius: float, base: str, color: str) -> None:
        """Fakes an outer glow: rings fading from ``color`` out into the ``base`` background."""
        for spread, t in ((22, 0.08), (15, 0.16), (9, 0.28), (4, 0.45)):
            self._round_rect(x1 - spread, y1 - spread, x2 + spread, y2 + spread, radius + spread, _mix(base, color, t))

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
                self._logo_images[width] = tk.PhotoImage(file=str(_ASSETS_DIR / f"logo_light_{width}.png"))
            except tk.TclError:
                return None
        return self._logo_images[width]

    def _bg_at(self, design_y: float) -> str:
        """Background color behind a given design y (the background is a vertical gradient over the whole window)."""
        height = max(1, self.canvas.winfo_height())
        return _gradient_color(((0.0, _BG_TOP), (1.0, _BG_BOTTOM)), self._y(design_y) / height)

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
        c.create_rectangle(0, 0, width, header_bottom, fill=_HEADER_BG, outline="")
        c.create_line(0, header_bottom, width, header_bottom, fill=_HEADER_LINE, width=max(1, round(2 * sc)))
        logo = self._logo_image()
        if logo is not None:
            c.create_image(self._x(40), header_bottom / 2, image=logo, anchor="w")
        title = self.match_title_var.get()
        if title:
            c.create_text(width / 2, header_bottom / 2 - 4 * sc, text=title, font=self._font(42), fill=_TEXT)
            c.create_line(
                width / 2 - 50 * sc, header_bottom - 22 * sc, width / 2 + 50 * sc, header_bottom - 22 * sc,
                fill=_ACCENT, width=max(2, round(4 * sc)), capstyle="round",
            )

        names = (self.player1_name_var.get(), self.player2_name_var.get())
        frames = (self.player1_frames_var.get(), self.player2_frames_var.get())
        scores = (self.player1_frame_score_var.get(), self.player2_frame_score_var.get())
        breaks = (self.player1_break_var.get(), self.player2_break_var.get())
        name_px = self._name_size
        for index in range(2):
            self._render_panel(index, names[index], frames[index], scores[index], breaks[index], name_px)

        # Thin separator between the two panels.
        c.create_line(self._x(935), self._y(575), self._x(985), self._y(575), fill=_DIM_TEXT, width=max(2, round(6 * sc)), capstyle="round")

        race = self.race_label_var.get()
        c.create_text(width / 2, self._y(1018), text=race, font=self._font(30, "normal"), fill=_MUTED_TEXT)

    def _draw_ball(self, x: float, y: float, radius: float, color: str) -> None:
        """Draws a shaded snooker ball (canvas pixels): dark rim, lighter core towards the top-left, specular dot."""
        c = self.canvas
        c.create_oval(x - radius, y - radius, x + radius, y + radius, fill=_mix(color, "#000000", 0.35), outline="")
        for t, shrink, shift in ((0.0, 0.88, 0.06), (0.18, 0.62, 0.16), (0.32, 0.36, 0.26)):
            r = radius * shrink
            cx, cy = x - radius * shift, y - radius * shift
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=_mix(color, "#ffffff", t), outline="")
        hr = radius * 0.16
        hx, hy = x - radius * 0.38, y - radius * 0.40
        c.create_oval(hx - hr, hy - hr, hx + hr, hy + hr, fill="#ffffff", outline="")

    def _draw_break_balls(self, cx: float, cy: float) -> None:
        """Draws the balls potted in the current break as shaded coloured balls with counts."""
        counts = [(name, self.break_balls.count(name)) for name in _BALL_ORDER if name in self.break_balls]
        if not counts:
            return
        sc = self._scale
        radius = 16 * sc
        font = self._font(26, "normal")
        label_gap = 8 * sc
        gap = 26 * sc
        items = [(name, f"×{count}", font.measure(f"×{count}")) for name, count in counts]
        total = sum(2 * radius + label_gap + w for _, _, w in items) + gap * (len(items) - 1)
        x = self._x(cx) - total / 2
        y = self._y(cy)
        for name, label, label_w in items:
            self._draw_ball(x + radius, y, radius, _BALL_COLORS[name])
            self.canvas.create_text(x + 2 * radius + label_gap, y, text=label, anchor="w", font=font, fill=_MUTED_TEXT)
            x += 2 * radius + label_gap + label_w + gap

    def _render_panel(self, index: int, name: str, frames: str, score: str, brk: str, name_px: int) -> None:
        c = self.canvas
        sc = self._scale
        x1, x2 = _PANEL_X[index]
        y1, y2 = _PANEL_Y1, _PANEL_Y2
        cx = (x1 + x2) / 2
        active = self.current_player == index + 1
        bg = self._bg_at((y1 + y2) / 2)

        # Card: soft shadow, lime glow for whoever is at the table, flat body with a thin border.
        self._soft_shadow(x1, y1, x2, y2, _PANEL_RADIUS, bg)
        if active:
            self._glow(x1, y1, x2, y2, _PANEL_RADIUS, bg, _ACCENT)
        self._round_rect(
            x1, y1, x2, y2, _PANEL_RADIUS, _PANEL_FILL,
            outline=_ACCENT if active else _PANEL_EDGE, width=3 if active else 2,
        )

        # Turn pill: solid lime when at the table, a faint outline otherwise.
        px1, px2, py1, py2 = cx - 150, cx + 150, y1 + 40, y1 + 92
        if active:
            self._round_rect(px1, py1, px2, py2, 26, _ACCENT)
            label_color = _ACCENT_TEXT
        else:
            self._round_rect(px1, py1, px2, py2, 26, _PANEL_FILL, outline=_PANEL_EDGE, width=2)
            label_color = _DIM_TEXT
        c.create_text(self._x(cx), self._y((py1 + py2) / 2), text=_spaced("ON THE TABLE"), font=self._font(20), fill=label_color)

        # Player name, shrunk until it fits the panel.
        name_font = self.player1_name_font if index == 0 else self.player2_name_font
        size = max(6, round(name_px * sc))
        name_font.configure(size=-size)
        max_width = (x2 - x1 - 80) * sc
        while size > 8 and name_font.measure(name) > max_width:
            size -= 2
            name_font.configure(size=-size)
        c.create_text(self._x(cx), self._y(y1 + 152) + 3 * sc, text=name, font=name_font, fill=_SHADOW)
        c.create_text(self._x(cx), self._y(y1 + 152), text=name, font=name_font, fill=_TEXT)

        # Frame score: large white digits in a recessed rounded well.
        bx1, bx2, by1, by2 = cx - 230, cx + 230, y1 + 215, y1 + 495
        self._round_rect(bx1, by1, bx2, by2, 28, _WELL_FILL, outline=_WELL_EDGE, width=2)
        c.create_text(self._x(cx), self._y((by1 + by2) / 2), text=score, font=self._font(210, digits=True), fill=_TEXT)
        if active:
            c.create_line(
                self._x(cx - 60), self._y(by2 - 22), self._x(cx + 60), self._y(by2 - 22),
                fill=_ACCENT, width=max(2, round(6 * sc)), capstyle="round",
            )

        # Current break: small spaced label + value (lime while it is running).
        label, _, value = brk.partition("  ")
        label_font, value_font = self._font(22), self._font(44, digits=True)
        label_text = _spaced(label)
        gap = 16 * sc
        total = label_font.measure(label_text) + gap + value_font.measure(value)
        bx = self._x(cx) - total / 2
        by = self._y(y1 + 552)
        c.create_text(bx, by, text=label_text, anchor="w", font=label_font, fill=_MUTED_TEXT)
        c.create_text(
            bx + label_font.measure(label_text) + gap, by, text=value, anchor="w", font=value_font,
            fill=_ACCENT if active else _DIM_TEXT,
        )
        if active:
            self._draw_break_balls(cx, y1 + 605)

        # Frames won.
        fx1, fx2, fy1, fy2 = cx - 130, cx + 130, y1 + 648, y1 + 748
        self._round_rect(fx1, fy1, fx2, fy2, 22, _WELL_FILL, outline=_WELL_EDGE, width=2)
        c.create_text(self._x(cx), self._y(fy1 + 40), text=frames, font=self._font(58, digits=True), fill=_TEXT)
        c.create_text(self._x(cx), self._y(fy1 + 80), text=_spaced("FRAMES WON"), font=self._font(16), fill=_MUTED_TEXT)

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
