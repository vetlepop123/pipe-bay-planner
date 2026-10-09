"""Pre-defined rig deck layouts.

Bays are fixed physical structures on a rig's deck — the app has no UI for
adding or removing them. To add a new rig, or correct a bay's dimensions,
edit RIG_LAYOUTS below.

length_m = usable length of the bay (how many joints fit side by side)
height_m = usable stacking height of the bay (how many rows of joints fit)

A bay's capacity in *joints per row* normally comes from length_m divided by
the pipe's spaced-out width (see models.pitch_m) — but that simple formula
doesn't hold perfectly across every bay/size combo in practice (rack guides,
hardware, etc.), so row_capacity lets you pin the exact real joints-per-row
for specific sizes. Sizes not listed there still fall back to the formula.
row_capacity keys are diameter in inches, matching pipe_sizes.py.

Bays are listed in the order they should be drawn, left to right.
"""

RIG_LAYOUTS: dict[str, list[dict]] = {
    "COSL Innovator": [
        {
            "name": "Riser Bay 1",
            "length_m": 4.35,
            "height_m": 4.2,
            "row_capacity": {20.0: 7, 12.25: 12, 9.625: 17},
        },
        {
            "name": "Riser Bay 2",
            "length_m": 2.48,
            "height_m": 4.2,
            "row_capacity": {20.0: 4, 12.25: 6, 9.625: 9},
        },
        {
            "name": "Riser Bay 3",
            "length_m": 2.48,
            "height_m": 4.2,
            "row_capacity": {20.0: 4, 12.25: 6, 9.625: 9},
        },
        {
            "name": "Pipe Bay 1",
            "length_m": 3.0,
            "height_m": 2.14,
            "row_capacity": {20.0: 4, 12.25: 7, 9.625: 11},
        },
        {
            "name": "Pipe Bay 2",
            "length_m": 2.5,
            "height_m": 2.14,
            "row_capacity": {20.0: 4, 12.25: 6, 9.625: 10},
        },
        {
            "name": "Pipe Bay 3",
            "length_m": 2.56,
            "height_m": 2.14,
            "row_capacity": {20.0: 4, 12.25: 6, 9.625: 10},
        },
        {
            "name": "Pipe Bay 4",
            "length_m": 2.6,
            "height_m": 2.14,
            "row_capacity": {20.0: 4, 12.25: 6, 9.625: 10},
        },
        {
            "name": "Pipe Bay 5",
            "length_m": 2.6,
            "height_m": 2.14,
            "row_capacity": {20.0: 4, 12.25: 6, 9.625: 10},
        },
    ],
}


def rig_names() -> list[str]:
    return list(RIG_LAYOUTS.keys())


def bay_specs(rig_name: str) -> list[dict]:
    if rig_name not in RIG_LAYOUTS:
        raise ValueError(f"Unknown rig '{rig_name}'.")
    return RIG_LAYOUTS[rig_name]
