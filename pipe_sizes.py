"""Named pipe sizes offered in the "add joints" dropdown, plus their defaults.

Add/remove/correct sizes or colors here — there's no UI for editing this list.
"""

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
# the body OD used for horizontal spacing. Sizes not listed here (including all casing)
# use the same diameter both ways.
VERTICAL_SIZE_OVERRIDES: dict[float, float] = {
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

_ALL_SIZES = CASING_SIZES + DRILL_PIPE_SIZES
_LABEL_BY_DIAMETER: dict[float, str] = {}
for _label, _d in _ALL_SIZES:
    _LABEL_BY_DIAMETER.setdefault(_d, _label)


def options() -> list[tuple[str, float]]:
    """(dropdown label, diameter in inches) pairs, grouped and labeled by category."""
    return [(f"Casing {label}", d) for label, d in CASING_SIZES] + [
        (f"Drill pipe {label}", d) for label, d in DRILL_PIPE_SIZES
    ]


def vertical_diameter_for(diameter_in: float) -> float:
    return VERTICAL_SIZE_OVERRIDES.get(diameter_in, diameter_in)


def fill_color_for(diameter_in: float) -> str:
    return SIZE_COLORS.get(diameter_in, DEFAULT_FILL_COLOR)


def label_for_diameter(diameter_in: float) -> str:
    return _LABEL_BY_DIAMETER.get(diameter_in, f'{diameter_in:g}"')
