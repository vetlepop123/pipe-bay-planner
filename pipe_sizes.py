"""Named pipe sizes offered in the "add joints" dropdown, plus their defaults.

Add/remove/correct sizes or colors here — there's no UI for editing this list.

Every joint records its category as well as its diameter, because casing and drill pipe
share some diameters (5 1/2", 5 7/8") but stack to different heights.
"""

CASING = "Casing"
DRILL_PIPE = "Drill pipe"
RISER = "Riser"

CASING_SIZES: list[tuple[str, float]] = [
    ('20"', 20.0),
    ('17"', 17.0),
    ('13 5/8"', 13.625),
    ('13 3/8"', 13.375),
    ('12 1/4"', 12.25),
    ('10 3/4"', 10.75),
    ('9 7/8"', 9.875),
    ('9 5/8"', 9.625),
    ('7"', 7.0),
    ('5 1/2"', 5.5),
    ('5 7/8"', 5.875),
]

DRILL_PIPE_SIZES: list[tuple[str, float]] = [
    ('6 5/8"', 6.625),
    ('5 7/8"', 5.875),
    ('5 1/2"', 5.5),
    ('5"', 5.0),
    ('4"', 4.0),
    ('3 1/2"', 3.5),
]

# Drill pipe stacks on its tool joints (the thicker threaded ends), which sit taller than
# the pipe body — so the diameter that matters for vertical stacking height is bigger than
# the body OD used for horizontal spacing. Casing and risers use the same diameter both ways.
DRILL_PIPE_TOOL_JOINT_SIZES: dict[float, float] = {
    6.625: 8.0,
    5.875: 7.0,
    5.5: 7.0,
    5.0: 6.625,
    4.0: 5.0,
    3.5: 5.0,
}

# One default fill color per real-world diameter, shared across casing/drill pipe so the
# same physical size always reads as the same color regardless of category.
SIZE_COLORS: dict[float, str] = {
    20.0: "#1f77b4",
    17.0: "#ff7f0e",
    13.625: "#2ca02c",
    13.375: "#d62728",
    12.25: "#f7b6d2",
    10.75: "#9467bd",
    9.875: "#8c564b",
    9.625: "#e377c2",
    7.0: "#7f7f7f",
    6.625: "#bcbd22",
    5.875: "#17becf",
    5.5: "#aec7e8",
    5.0: "#ffbb78",
    4.0: "#98df8a",
    3.5: "#c5b0d5",
}
DEFAULT_FILL_COLOR = "#999999"

_SIZES_BY_CATEGORY = {CASING: CASING_SIZES, DRILL_PIPE: DRILL_PIPE_SIZES}


def options() -> list[tuple[str, str, float]]:
    """(dropdown label, category, diameter in inches) for every pipe size, casing first."""
    return [(f"{category} {label}", category, d) for category, sizes in _SIZES_BY_CATEGORY.items() for label, d in sizes]


def vertical_diameter_for(category: str, diameter_in: float) -> float:
    if category == DRILL_PIPE:
        return DRILL_PIPE_TOOL_JOINT_SIZES.get(diameter_in, diameter_in)
    return diameter_in


def fill_color_for(diameter_in: float) -> str:
    return SIZE_COLORS.get(diameter_in, DEFAULT_FILL_COLOR)


def size_label(category: str, diameter_in: float) -> str:
    """Short size text, e.g. '9 5/8"' for casing and '5 1/2" DP' for drill pipe."""
    label = next((lbl for lbl, d in _SIZES_BY_CATEGORY.get(category, []) if d == diameter_in), f'{diameter_in:g}"')
    return f"{label} DP" if category == DRILL_PIPE else label
