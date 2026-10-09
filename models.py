"""Core data model and layout logic for the pipe bay planning tool."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import pipe_sizes
import rigs

INCHES_TO_METERS = 0.0254
PREFERRED_MAX_STACK_HEIGHT_M = 2.5  # don't stack higher than this unless the bay needs it
DUNNAGE_M = 0.10  # spacer height between layers (none under the bottom layer)
HORIZONTAL_CLEARANCE_FACTOR = 1.05  # side-by-side spacing margin per joint; untuned guess, adjust as needed

# Fill color comes from pipe size (pipe_sizes.SIZE_COLORS); a type only supplies the ring
# color and an optional default label suggestion (e.g. "SHOE") offered when adding joints.
DEFAULT_JOINT_TYPES = [
    {"name": "Slick", "ring_color": "#000000", "label": None},
    {"name": "Cent x1", "ring_color": "#2E8B57", "label": None},
    {"name": "Cent x2", "ring_color": "#1E5AA8", "label": None},
    {"name": "Shoetrack", "ring_color": "#D12E2E", "label": "SHOE"},
    {"name": "Backup", "ring_color": "#555555", "label": "BU"},
]


@dataclass
class JointType:
    name: str
    ring_color: str = "#000000"
    label: str | None = None  # default label suggestion offered when adding this type (e.g. "SHOE")


@dataclass
class Joint:
    type_name: str
    diameter_in: float  # pipe OD for this specific joint, set when it was added
    vertical_diameter_in: float  # effective diameter for stacking height (tool-joint size for drill pipe)
    order: int  # insertion order within the bay — controls stacking position and FIFO removal
    seq: int  # display number, counted within this joint's own (type_name, diameter_in) group only
    label: str | None = None  # custom text shown instead of the number, set when this joint was added


@dataclass
class Bay:
    name: str
    length_m: float  # fixed physical length (how much fits side by side)
    height_m: float  # fixed physical stacking height
    row_capacity_overrides: dict[float, int] = field(default_factory=dict)  # diameter_in -> joints/row
    joints: list[Joint] = field(default_factory=list)

    def count(self) -> int:
        return len(self.joints)

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for j in self.joints:
            counts[j.type_name] = counts.get(j.type_name, 0) + 1
        return counts


@dataclass
class Job:
    name: str
    rig_name: str = ""
    bays: list[Bay] = field(default_factory=list)
    joint_types: list[JointType] = field(default_factory=list)

    def get_bay(self, bay_name: str) -> Bay | None:
        return next((b for b in self.bays if b.name == bay_name), None)

    def get_joint_type(self, type_name: str) -> JointType | None:
        return next((t for t in self.joint_types if t.name == type_name), None)


def bays_from_rig(rig_name: str) -> list[Bay]:
    return [
        Bay(
            name=spec["name"],
            length_m=spec["length_m"],
            height_m=spec["height_m"],
            row_capacity_overrides=dict(spec.get("row_capacity", {})),
        )
        for spec in rigs.bay_specs(rig_name)
    ]


def new_job(name: str, rig_name: str) -> Job:
    return Job(
        name=name,
        rig_name=rig_name,
        bays=bays_from_rig(rig_name),
        joint_types=[JointType(**t) for t in DEFAULT_JOINT_TYPES],
    )


def add_joint_type(job: Job, name: str, ring_color: str, label: str | None = None) -> None:
    if job.get_joint_type(name) is not None:
        raise ValueError(f"A joint type named '{name}' already exists.")
    job.joint_types.append(JointType(name=name, ring_color=ring_color, label=label or None))


def remove_joint_type(job: Job, name: str) -> None:
    job.joint_types = [t for t in job.joint_types if t.name != name]


# ---------------------------------------------------------------- physical packing
# Bays are a fixed physical size, but joints of different diameters can be added over
# time, so rows are packed by actual pipe size rather than a precomputed joint-count
# grid: joints fill a row left to right until the next one wouldn't physically fit,
# then a new row starts on top.


def pitch_m(diameter_in: float) -> float:
    """Physical center-to-center spacing a joint of this diameter needs in a row, clearance included."""
    return diameter_in * INCHES_TO_METERS * HORIZONTAL_CLEARANCE_FACTOR


def pack_rows(
    joints: list[Joint], length_m: float, capacity_overrides: dict[float, int] | None = None
) -> list[list[Joint]]:
    """Pack already bottom-up-ordered joints into rows constrained by the bay's length.

    A row never mixes pipe sizes — a diameter change always starts a new row, even if
    the current row still has physical room left. If capacity_overrides has an entry for
    a joint's diameter, that exact joints-per-row count is used instead of the length/pitch
    formula (real rack capacity doesn't always match the formula exactly).
    """
    capacity_overrides = capacity_overrides or {}
    rows: list[list[Joint]] = []
    current: list[Joint] = []
    used_m = 0.0
    current_diameter: float | None = None
    for j in joints:
        cap = capacity_overrides.get(j.diameter_in)
        if not current:
            new_row_needed = False
        elif j.diameter_in != current_diameter:
            new_row_needed = True
        elif cap is not None:
            new_row_needed = len(current) >= cap
        else:
            new_row_needed = used_m + pitch_m(j.diameter_in) > length_m + 1e-9
        if new_row_needed:
            rows.append(current)
            current = []
            used_m = 0.0
        current.append(j)
        used_m += pitch_m(j.diameter_in)
        current_diameter = j.diameter_in
    if current:
        rows.append(current)
    return rows


def row_layout(bay: Bay) -> list[list[Joint]]:
    """Bottom-up rows of a bay's joints, packed by each joint's own diameter."""
    ordered = sorted(bay.joints, key=lambda j: j.order)
    return pack_rows(ordered, bay.length_m, bay.row_capacity_overrides)


def _row_height_m(row: list[Joint]) -> float:
    return max((j.vertical_diameter_in * INCHES_TO_METERS for j in row), default=0.0)


def stack_height_m(rows: list[list[Joint]]) -> float:
    if not rows:
        return 0.0
    return sum(_row_height_m(r) for r in rows) + DUNNAGE_M * (len(rows) - 1)


def preferred_breach_row(bay: Bay) -> int | None:
    """0-based row index where cumulative stack height first passes the preferred max, or None if it never does."""
    rows = row_layout(bay)
    cum = 0.0
    for i, row in enumerate(rows):
        cum += _row_height_m(row) + (DUNNAGE_M if i > 0 else 0.0)
        if cum > PREFERRED_MAX_STACK_HEIGHT_M:
            return i
    return None


def add_joints(bay: Bay, type_name: str, count: int, diameter_in: float, label: str | None = None) -> None:
    if count < 1:
        raise ValueError("Count must be at least 1.")
    if diameter_in <= 0:
        raise ValueError("Pipe diameter must be greater than 0.")
    ordered = sorted(bay.joints, key=lambda j: j.order)
    next_order = (ordered[-1].order if ordered else 0) + 1
    group_seq = max(
        (j.seq for j in bay.joints if j.type_name == type_name and j.diameter_in == diameter_in), default=0
    )
    vertical_diameter_in = pipe_sizes.vertical_diameter_for(diameter_in)
    new_joints = [
        Joint(
            type_name=type_name,
            diameter_in=diameter_in,
            vertical_diameter_in=vertical_diameter_in,
            order=next_order + i,
            seq=group_seq + 1 + i,
            label=label or None,
        )
        for i in range(count)
    ]
    hypothetical = ordered + new_joints
    total_height = stack_height_m(pack_rows(hypothetical, bay.length_m, bay.row_capacity_overrides))
    if total_height > bay.height_m + 1e-9:
        raise ValueError(
            f"Adding {count} joint(s) at {diameter_in:g}\" would need {total_height:.2f} m of stack height, "
            f"but '{bay.name}' only has {bay.height_m:g} m to work with."
        )
    bay.joints = hypothetical


def _renumber_seq_groups(joints: list[Joint]) -> None:
    """Reassign each joint's display seq within its own (type_name, diameter_in) group, in stacking order."""
    group_counts: dict[tuple[str, float], int] = {}
    for j in sorted(joints, key=lambda j: j.order):
        key = (j.type_name, j.diameter_in)
        group_counts[key] = group_counts.get(key, 0) + 1
        j.seq = group_counts[key]


def remove_joints(bay: Bay, count: int) -> None:
    if count < 1:
        raise ValueError("Count must be at least 1.")
    if count > len(bay.joints):
        raise ValueError(f"Cannot remove {count} joints — only {len(bay.joints)} in '{bay.name}'.")
    remaining = sorted(bay.joints, key=lambda j: j.order)[count:]
    for new_order, j in enumerate(remaining, start=1):
        j.order = new_order
    _renumber_seq_groups(remaining)
    bay.joints = remaining


def clear_bay(bay: Bay) -> None:
    bay.joints = []


def job_to_dict(job: Job) -> dict:
    return asdict(job)


def job_from_dict(data: dict) -> Job:
    joint_types = [JointType(**t) for t in data.get("joint_types", [])]
    bays = [
        Bay(
            name=b["name"],
            length_m=b["length_m"],
            height_m=b["height_m"],
            # JSON object keys are always strings, so diameters need converting back to float.
            row_capacity_overrides={float(k): v for k, v in b.get("row_capacity_overrides", {}).items()},
            joints=[Joint(**j) for j in b.get("joints", [])],
        )
        for b in data.get("bays", [])
    ]
    return Job(name=data["name"], rig_name=data.get("rig_name", ""), bays=bays, joint_types=joint_types)
