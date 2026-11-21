"""Matplotlib rendering of a Job's bays as a deck-plan style figure."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")

# Figures are built with Figure() directly, never pyplot: pyplot keeps global state that leaks
# memory and isn't safe with several Streamlit sessions rendering at once.
import matplotlib.patheffects as pe
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import PatchCollection
from matplotlib.patches import Circle, Patch, Rectangle
from matplotlib.figure import Figure

import pipe_sizes
from models import (
    Job,
    Row,
    RISER_TYPES,
    display_numbers,
    is_riser,
    row_layout,
    stack_height_m,
    pitch_m,
    PREFERRED_MAX_STACK_HEIGHT_M,
    INCHES_TO_METERS,
)

BAY_GAP = 1.1
TOP_MARGIN = 1.0  # space for the bay title above the stack
BOTTOM_MARGIN = 0.6
LEGEND_ROW_HEIGHT = 0.55
DUNNAGE_COLOR = "#a9793f"
DUNNAGE_OVER_PREFERRED_COLOR = "#cc2222"
JOINT_FONT_COLOR = "black"
MIN_JOINT_FONT_SIZE = 4
MAX_JOINT_FONT_SIZE = 13
JOINT_FONT_SCALE = 18  # fontsize(pt) = radius(draw units) * this, clamped to the min/max above
RISER_CORE_RATIO = 0.6  # white core radius as a fraction of the riser's full radius
SELECTED_COLOR = "#ff00cc"
NOMINAL_DRAW_PITCH_M = 0.5  # 1 draw-unit = this many real meters, both horizontally and vertically
MIN_BAY_DRAW_WIDTH = 4  # minimum column width (in draw-units) so title text doesn't overlap neighbors
MIN_BAY_DRAW_HEIGHT = 3  # minimum wall height (in draw-units) so a tiny bay still reads as a container
PAD_INCHES = 0.1  # whitespace kept around the cropped PNG


@dataclass
class JointHit:
    """Where a joint was drawn, in data units, so a click on the image can be traced back to it."""

    bay_name: str
    order: int
    x: float
    y: float
    radius: float


@dataclass
class View:
    """A rendered deck plan as PNG bytes, plus what's needed to turn a click on it back into a joint.

    Plain data only, so it can be cached between Streamlit reruns.
    """

    png: bytes
    width: int
    height: int
    hits: list[JointHit]
    crop_x0_in: float  # left edge of the PNG crop, in figure inches
    crop_y1_in: float  # top edge of the PNG crop, in figure inches
    dpi: float  # PNG pixels per inch
    fig_dpi: float  # display pixels per inch that data_from_display expects
    data_from_display: list[list[float]]  # 3x3 affine matrix: display pixels -> data units

    def joint_at(self, px: float, py: float) -> JointHit | None:
        """The joint drawn at pixel (px, py) of the PNG (origin top-left), if any."""
        x_disp = (self.crop_x0_in + px / self.dpi) * self.fig_dpi
        y_disp = (self.crop_y1_in - py / self.dpi) * self.fig_dpi  # image rows count down from the top
        (a, b, c), (d, e, f), _ = self.data_from_display
        return hit_test(self.hits, a * x_disp + b * y_disp + c, d * x_disp + e * y_disp + f)


def _units(m: float) -> float:
    return m / NOMINAL_DRAW_PITCH_M


def _pitch_units(diameter_in: float) -> float:
    return _units(pitch_m(diameter_in))


def _radius_units(diameter_in: float) -> float:
    return _units(diameter_in * INCHES_TO_METERS / 2)


def _row_width_units(row: Row) -> float:
    return _units(row.x_offset_m) + sum(_pitch_units(j.diameter_in) for j in row.joints)


def _bay_legend_entries(bay) -> list[tuple[str, str, float, int]]:
    """(type_name, category, diameter, count) for each distinct combo in this bay, sorted by size then type."""
    counts: dict[tuple[str, str, float], int] = {}
    for j in bay.joints:
        key = (j.type_name, j.category, j.diameter_in)
        counts[key] = counts.get(key, 0) + 1
    return sorted(((t, cat, d, c) for (t, cat, d), c in counts.items()), key=lambda e: (e[2], e[1], e[0]))


def _first_red_dunnage_index(rows: list[Row]) -> int | None:
    """Index of the first row whose dunnage (below it) sits at/above the preferred height."""
    for i in range(1, len(rows)):
        if rows[i - 1].top_m >= PREFERRED_MAX_STACK_HEIGHT_M - 1e-9:
            return i
    return None


def _has_dunnage(bay) -> bool:
    return not bay.holds_risers()  # risers stack directly on each other


def _marker_patches(
    type_name: str, diameter_in: float, center: tuple[float, float], radius: float, job: Job, lw: float
) -> list[Patch]:
    """A joint's circle: risers are a colored band around a white core, pipe is size-colored with a type ring."""
    if type_name in RISER_TYPES:
        band = RISER_TYPES[type_name]
        return [
            Circle(center, radius, facecolor=band, edgecolor="white", linewidth=lw / 2),
            Circle(center, radius * RISER_CORE_RATIO, facecolor="white", edgecolor="none"),
        ]
    jt = job.get_joint_type(type_name)
    ring = jt.ring_color if jt else "#000000"
    fill = pipe_sizes.fill_color_for(diameter_in)
    return [Circle(center, radius, facecolor=fill, edgecolor=ring, linewidth=lw)]


def build_plan(
    job: Job,
    zoom: float = 1.0,
    only_bays: list[str] | None = None,
    selected: tuple[str, int] | None = None,
) -> tuple[Figure, list[JointHit]]:
    """The deck-plan figure, plus where each joint landed on it. `selected` (bay name, joint order) is highlighted."""
    bays = [b for b in job.bays if only_bays is None or b.name in only_bays] if job.bays else []

    if not bays:
        fig = Figure(figsize=(6, 2))
        msg = "This rig has no bays defined in rigs.py." if not job.bays else "No bays selected to display."
        fig.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12)
        return fig, []

    bay_rows = {bay.name: row_layout(bay) for bay in bays}

    # Each bay gets its own legend (type + size + count), sitting right under its title.
    bay_legend_entries = {bay.name: _bay_legend_entries(bay) for bay in bays}
    max_legend_rows = max((len(v) for v in bay_legend_entries.values()), default=0)
    legend_area_h = max_legend_rows * LEGEND_ROW_HEIGHT

    bay_draw_widths = {
        bay.name: max(
            max((_row_width_units(r) for r in bay_rows[bay.name]), default=0.0),
            _units(bay.length_m),
            MIN_BAY_DRAW_WIDTH,
        )
        for bay in bays
    }
    # Walls are always drawn to the bay's real physical height, not just what's currently stacked.
    bay_draw_heights = {
        bay.name: max(
            _units(stack_height_m(bay_rows[bay.name])),
            _units(bay.height_m),
            MIN_BAY_DRAW_HEIGHT,
        )
        for bay in bays
    }
    bay_height = max(bay_draw_heights.values(), default=1)

    any_red_dunnage = any(_has_dunnage(bay) and _first_red_dunnage_index(bay_rows[bay.name]) is not None for bay in bays)
    footer_rows = 1 if any_red_dunnage else 0

    total_width = sum(bay_draw_widths[bay.name] for bay in bays) + BAY_GAP * (len(bays) - 1)
    total_height = TOP_MARGIN + legend_area_h + bay_height + BOTTOM_MARGIN + footer_rows * LEGEND_ROW_HEIGHT + 0.8

    fig_w = max(total_width * 0.55 + 1, 4) * zoom
    fig_h = max(total_height * 0.55 + 1, 3) * zoom
    fig = Figure(figsize=(fig_w, fig_h), dpi=150)
    ax = fig.add_subplot(111)

    fig.suptitle(f"{job.name}  ({job.rig_name})", fontsize=14, fontweight="bold", y=0.98)

    hits: list[JointHit] = []
    # All circles go into one collection at the end — far faster than hundreds of separate
    # patches, which matplotlib measures one by one. Axis limits are set by hand below anyway.
    markers: list[Patch] = []
    x_cursor = 0.0
    floor_y = 0.0
    # Shared across bays (so titles line up), with room reserved above every bay's own wall
    # for the tallest per-bay legend, regardless of which bay that legend belongs to.
    title_top = floor_y + bay_height + 0.3 + legend_area_h
    for bay in bays:
        rows = bay_rows[bay.name]
        bay_w = bay_draw_widths[bay.name]
        own_top = floor_y + bay_draw_heights[bay.name]
        numbers = display_numbers(bay)

        # Bay side walls and floor, always drawn to the bay's real physical size. No roof line —
        # bays are open at the top, side walls alone convey the full height.
        ax.plot([x_cursor, x_cursor], [floor_y, own_top], color="#888888", linewidth=3, solid_capstyle="butt")
        ax.plot([x_cursor + bay_w, x_cursor + bay_w], [floor_y, own_top], color="#888888", linewidth=3, solid_capstyle="butt")
        ax.plot([x_cursor, x_cursor + bay_w], [floor_y, floor_y], color="#888888", linewidth=3, solid_capstyle="butt")

        # Title (aligned across bays regardless of individual height)
        ax.text(
            x_cursor + bay_w / 2,
            title_top + 0.2,
            bay.name,
            ha="center",
            va="bottom",
            fontsize=9.5,
            fontweight="bold",
        )

        # Per-bay legend, right under the title — type, size and how many of each.
        for i, (type_name, category, diameter, count) in enumerate(bay_legend_entries[bay.name]):
            ly = title_top - i * LEGEND_ROW_HEIGHT - LEGEND_ROW_HEIGHT / 2
            swatch_x = x_cursor + 0.2
            markers += _marker_patches(type_name, diameter, (swatch_x, ly), 0.13, job, lw=1.5)
            ax.text(
                swatch_x + 0.22,
                ly,
                f"{type_name} {pipe_sizes.size_label(category, diameter)}: {count}",
                ha="left",
                va="center",
                fontsize=6.5,
            )

        # Dunnage bands between rows — drawn to true thickness so a pipe sits flush on top
        # of the dunnage below it, and the next pipe sits flush on top of that dunnage.
        # The first dunnage at or above the preferred max stack height is colored red,
        # flagging "everything from here up is only for when you need the extra height."
        if _has_dunnage(bay):
            red_idx = _first_red_dunnage_index(rows)
            for i in range(1, len(rows)):
                prev_top = floor_y + _units(rows[i - 1].top_m)
                cur_bottom = floor_y + _units(rows[i].bottom_m)
                color = DUNNAGE_OVER_PREFERRED_COLOR if i == red_idx else DUNNAGE_COLOR
                ax.add_patch(
                    Rectangle(
                        (x_cursor, prev_top),
                        bay_w,
                        cur_bottom - prev_top,
                        facecolor=color,
                        edgecolor="none",
                        zorder=1,
                    )
                )

        # Joints — circle size and spacing are scaled to each joint's real diameter, so
        # different pipe sizes are visibly different, not just differently counted.
        for row in rows:
            row_cursor = x_cursor + _units(row.x_offset_m)
            cy = floor_y + _units(row.bottom_m + row.height_m / 2)
            for joint in row.joints:
                pitch_u = _pitch_units(joint.diameter_in)
                radius_u = _radius_units(joint.diameter_in)
                cx = row_cursor + pitch_u / 2
                markers += _marker_patches(joint.type_name, joint.diameter_in, (cx, cy), radius_u, job, lw=2.2)
                if selected == (bay.name, joint.order):
                    ax.add_patch(
                        Circle((cx, cy), radius_u * 1.12, facecolor="none", edgecolor=SELECTED_COLOR, linewidth=3, zorder=4)
                    )
                label = joint.label or str(numbers[joint.order])
                text_radius_u = radius_u * RISER_CORE_RATIO if is_riser(joint) else radius_u
                fontsize = max(MIN_JOINT_FONT_SIZE, min(MAX_JOINT_FONT_SIZE, text_radius_u * JOINT_FONT_SCALE))
                ax.text(
                    cx,
                    cy,
                    label,
                    ha="center",
                    va="center",
                    fontsize=fontsize,
                    fontweight="bold",
                    color=JOINT_FONT_COLOR,
                    zorder=3,
                    path_effects=[pe.withStroke(linewidth=1.5, foreground="white", alpha=0.7)],
                    in_layout=False,  # always inside the bay walls, so no need to measure it for cropping
                )
                hits.append(JointHit(bay.name, joint.order, cx, cy, radius_u))
                row_cursor += pitch_u

        x_cursor += bay_w + BAY_GAP

    ax.add_collection(PatchCollection(markers, match_original=True, zorder=2), autolim=False)

    # Footer note — only needed when a red dunnage band actually appears somewhere.
    footer_y0 = floor_y - 0.5
    if any_red_dunnage:
        ax.add_patch(
            Rectangle((0.05, footer_y0 - 0.08), 0.4, 0.16, facecolor=DUNNAGE_OVER_PREFERRED_COLOR, edgecolor="none")
        )
        ax.text(
            0.6,
            footer_y0,
            f"dunnage above preferred max stack height ({PREFERRED_MAX_STACK_HEIGHT_M:g} m) — go higher only if needed",
            ha="left",
            va="center",
            fontsize=7.5,
            color=DUNNAGE_OVER_PREFERRED_COLOR,
        )

    ax.set_xlim(-0.5, total_width + 0.5)
    ax.set_ylim(footer_y0 - footer_rows * LEGEND_ROW_HEIGHT - 0.3, title_top + TOP_MARGIN)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig, hits


def render_view(
    job: Job, zoom: float, only_bays: list[str], selected: tuple[str, int] | None, dpi: int = 150
) -> View:
    """The on-screen deck plan as a tightly cropped PNG that clicks can be traced back from.

    The crop box is computed here and passed to savefig explicitly (rather than
    bbox_inches="tight"), so the pixel -> data mapping is known exactly.
    """
    fig, hits = build_plan(job, zoom=zoom, only_bays=only_bays, selected=selected)
    canvas = FigureCanvasAgg(fig)
    bbox = fig.get_tightbbox(canvas.get_renderer()).padded(PAD_INCHES)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches=bbox)
    # Read after savefig, once the equal-aspect layout is final.
    matrix = fig.axes[0].transData.inverted().get_affine().get_matrix().tolist() if fig.axes else [[0.0] * 3] * 3
    png = buf.getvalue()
    return View(
        png=png,
        # Straight from the PNG header (bytes 16-24 of the IHDR chunk), exactly as savefig rounded it.
        width=int.from_bytes(png[16:20], "big"),
        height=int.from_bytes(png[20:24], "big"),
        hits=hits,
        crop_x0_in=bbox.x0,
        crop_y1_in=bbox.y1,
        dpi=dpi,
        fig_dpi=fig.dpi,
        data_from_display=matrix,
    )


def export_bytes(job: Job, only_bays: list[str], fmt: str) -> bytes:
    """The deck plan as a downloadable "png" or "pdf", always at 100% and without any selection."""
    fig, _ = build_plan(job, only_bays=only_bays)
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, bbox_inches="tight", dpi=200)
    return buf.getvalue()


def hit_test(hits: list[JointHit], x: float, y: float) -> JointHit | None:
    """The joint drawn at data point (x, y), if any."""
    inside = [h for h in hits if math.hypot(h.x - x, h.y - y) <= h.radius]
    return min(inside, key=lambda h: math.hypot(h.x - x, h.y - y), default=None)
