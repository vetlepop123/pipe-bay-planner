"""Matplotlib rendering of a Job's bays as a deck-plan style figure."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Circle, Rectangle
from matplotlib.figure import Figure

import pipe_sizes
from models import (
    Job,
    row_layout,
    stack_height_m,
    pitch_m,
    DUNNAGE_M,
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
NOMINAL_DRAW_PITCH_M = 0.5  # 1 draw-unit = this many real meters, both horizontally and vertically
MIN_BAY_DRAW_WIDTH = 4  # minimum column width (in draw-units) so title text doesn't overlap neighbors
MIN_BAY_DRAW_HEIGHT = 3  # minimum wall height (in draw-units) so a tiny bay still reads as a container


def _pitch_units(diameter_in: float) -> float:
    return pitch_m(diameter_in) / NOMINAL_DRAW_PITCH_M


def _radius_units(diameter_in: float) -> float:
    return (diameter_in * INCHES_TO_METERS / 2) / NOMINAL_DRAW_PITCH_M


def _row_width_units(row: list) -> float:
    return sum(_pitch_units(j.diameter_in) for j in row)


def _row_height_units(row: list) -> float:
    # Rows are homogeneous in diameter (pack_rows never mixes sizes in one row).
    return (row[0].vertical_diameter_in * INCHES_TO_METERS) / NOMINAL_DRAW_PITCH_M


def _row_geometry(rows: list, floor_y: float) -> list[tuple[float, float]]:
    """Bottom-up (bottom_y, height_units) per row, with a true-to-scale dunnage gap between rows."""
    dunnage_u = DUNNAGE_M / NOMINAL_DRAW_PITCH_M
    geometry = []
    cursor = floor_y
    for i, row in enumerate(rows):
        if i > 0:
            cursor += dunnage_u
        h = _row_height_units(row)
        geometry.append((cursor, h))
        cursor += h
    return geometry


def _bay_legend_entries(bay) -> list[tuple[str, float, int]]:
    """(type_name, diameter, count) for each distinct combo in this bay, sorted by size then type."""
    counts: dict[tuple[str, float], int] = {}
    for j in bay.joints:
        key = (j.type_name, j.diameter_in)
        counts[key] = counts.get(key, 0) + 1
    return sorted(((t, d, c) for (t, d), c in counts.items()), key=lambda e: (e[1], e[0]))


def _first_red_dunnage_index(geometry: list[tuple[float, float]], preferred_u: float) -> int | None:
    """Index into geometry of the first dunnage gap (below row i) at/above the preferred height."""
    for i in range(1, len(geometry)):
        prev_top = geometry[i - 1][0] + geometry[i - 1][1]
        if prev_top >= preferred_u - 1e-9:
            return i
    return None


def build_figure(job: Job, zoom: float = 1.0, only_bays: list[str] | None = None) -> Figure:
    bays = [b for b in job.bays if only_bays is None or b.name in only_bays] if job.bays else []

    if not bays:
        fig = plt.figure(figsize=(6, 2))
        msg = "This rig has no bays defined in rigs.py." if not job.bays else "No bays selected to display."
        fig.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12)
        return fig

    bay_rows = {bay.name: row_layout(bay) for bay in bays}

    # Each bay gets its own legend (type + size + count), sitting right under its title.
    bay_legend_entries = {bay.name: _bay_legend_entries(bay) for bay in bays}
    max_legend_rows = max((len(v) for v in bay_legend_entries.values()), default=0)
    legend_area_h = max_legend_rows * LEGEND_ROW_HEIGHT

    bay_draw_widths = {
        bay.name: max(
            max((_row_width_units(r) for r in bay_rows[bay.name]), default=0.0),
            bay.length_m / NOMINAL_DRAW_PITCH_M,
            MIN_BAY_DRAW_WIDTH,
        )
        for bay in bays
    }
    # Walls are always drawn to the bay's real physical height, not just what's currently stacked.
    bay_draw_heights = {
        bay.name: max(
            stack_height_m(bay_rows[bay.name]) / NOMINAL_DRAW_PITCH_M,
            bay.height_m / NOMINAL_DRAW_PITCH_M,
            MIN_BAY_DRAW_HEIGHT,
        )
        for bay in bays
    }
    bay_height = max(bay_draw_heights.values(), default=1)

    preferred_u = PREFERRED_MAX_STACK_HEIGHT_M / NOMINAL_DRAW_PITCH_M
    bay_geometry = {bay.name: _row_geometry(bay_rows[bay.name], 0.0) for bay in bays}
    any_red_dunnage = any(
        _first_red_dunnage_index(bay_geometry[bay.name], preferred_u) is not None for bay in bays
    )
    footer_rows = 1 if any_red_dunnage else 0

    total_width = sum(bay_draw_widths[bay.name] for bay in bays) + BAY_GAP * (len(bays) - 1)
    total_height = TOP_MARGIN + legend_area_h + bay_height + BOTTOM_MARGIN + footer_rows * LEGEND_ROW_HEIGHT + 0.8

    fig_w = max(total_width * 0.55 + 1, 4) * zoom
    fig_h = max(total_height * 0.55 + 1, 3) * zoom
    fig = Figure(figsize=(fig_w, fig_h), dpi=150)
    ax = fig.add_subplot(111)

    fig.suptitle(f"{job.name}  ({job.rig_name})", fontsize=14, fontweight="bold", y=0.98)

    x_cursor = 0.0
    floor_y = 0.0
    # Shared across bays (so titles line up), with room reserved above every bay's own wall
    # for the tallest per-bay legend, regardless of which bay that legend belongs to.
    title_top = floor_y + bay_height + 0.3 + legend_area_h
    for bay in bays:
        rows = bay_rows[bay.name]
        bay_w = bay_draw_widths[bay.name]
        own_top = floor_y + bay_draw_heights[bay.name]
        geometry = bay_geometry[bay.name]

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
            wrap=True,
        )

        # Per-bay legend, right under the title — type, size and how many of each.
        for i, (type_name, diameter, count) in enumerate(bay_legend_entries[bay.name]):
            ly = title_top - i * LEGEND_ROW_HEIGHT - LEGEND_ROW_HEIGHT / 2
            jt = job.get_joint_type(type_name)
            ring = jt.ring_color if jt else "#000000"
            swatch_x = x_cursor + 0.2
            ax.add_patch(
                Circle(
                    (swatch_x, ly), 0.13, facecolor=pipe_sizes.fill_color_for(diameter), edgecolor=ring, linewidth=1.5
                )
            )
            ax.text(
                swatch_x + 0.22,
                ly,
                f"{type_name} {pipe_sizes.label_for_diameter(diameter)}: {count}",
                ha="left",
                va="center",
                fontsize=6.5,
            )

        # Dunnage bands between rows — drawn to true thickness so a pipe sits flush on top
        # of the dunnage below it, and the next pipe sits flush on top of that dunnage.
        # The first dunnage at or above the preferred max stack height is colored red,
        # flagging "everything from here up is only for when you need the extra height."
        red_idx = _first_red_dunnage_index(geometry, preferred_u)
        for i in range(1, len(geometry)):
            prev_top = geometry[i - 1][0] + geometry[i - 1][1]
            cur_bottom = geometry[i][0]
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
        for row_idx, row in enumerate(rows):
            row_bottom, row_h = geometry[row_idx]
            row_cursor = x_cursor
            for joint in row:
                jt = job.get_joint_type(joint.type_name)
                fill = pipe_sizes.fill_color_for(joint.diameter_in)
                ring = jt.ring_color if jt else "#000000"
                label = joint.label or str(joint.seq)
                pitch_u = _pitch_units(joint.diameter_in)
                radius_u = _radius_units(joint.diameter_in)
                cx = row_cursor + pitch_u / 2
                cy = row_bottom + row_h / 2
                circle = Circle((cx, cy), radius_u, facecolor=fill, edgecolor=ring, linewidth=2.2, zorder=2)
                ax.add_patch(circle)
                fontsize = max(MIN_JOINT_FONT_SIZE, min(MAX_JOINT_FONT_SIZE, radius_u * JOINT_FONT_SCALE))
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
                )
                row_cursor += pitch_u

        x_cursor += bay_w + BAY_GAP

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
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return fig
