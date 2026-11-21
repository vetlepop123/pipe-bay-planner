"""Core data model and layout logic for the pipe bay planning tool."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, asdict, replace

import pipe_sizes
import rigs

INCHES_TO_METERS = 0.0254
PREFERRED_MAX_STACK_HEIGHT_M = 2.5  # don't stack higher than this unless the bay needs it
DUNNAGE_M = 0.10  # spacer height between layers (none under the bottom layer)
HORIZONTAL_CLEARANCE_FACTOR = 1.05  # side-by-side spacing margin per joint; untuned guess, adjust as needed

# Risers are a fixed size and stack directly on each other (no dunnage), touching side by side.
# A bay holding risers can't hold any pipe or casing, and vice versa.
RISER_DIAMETER_IN = 48.0
RISER_CLEARANCE_FACTOR = 1.0
RISER_TYPES: dict[str, str] = {  # type name -> outer band color (drawn around a white core)
    "Buoyed riser": "#1E5AA8",
    "Unbuoyed riser": "#000000",
}
# "nested": each layer above the bottom sits in the grooves of the layer below — half a riser
# in from the wall, one fewer riser, alternating n / n-1 / n ... "perfect": straight on top.
RISER_STACKING_MODES = {"nested": "Nested (in the grooves)", "perfect": "Perfect (straight on top)"}

NUMBERING_DIRECTIONS = {
    "bottom_up_ltr": "Bottom-up, left → right",
    "bottom_up_rtl": "Bottom-up, right → left",
    "top_down_ltr": "Top-down, left → right",
    "top_down_rtl": "Top-down, right → left",
}
NUMBERING_GROUPINGS = {
    "per_type": "Each type + size separately",
    "together": "Everything together",
}

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
    category: str  # pipe_sizes.CASING / DRILL_PIPE / RISER
    diameter_in: float  # pipe OD for this specific joint, set when it was added
    order: int  # stacking order within the bay — controls position; the highest is on top and removed first
    label: str | None = None  # custom text shown instead of the number
    manual_number: int | None = None  # pins this joint's display number instead of numbering it automatically

    @property
    def vertical_diameter_in(self) -> float:
        """Effective diameter for stacking height (tool-joint size for drill pipe)."""
        return pipe_sizes.vertical_diameter_for(self.category, self.diameter_in)

    @property
    def size_key(self) -> tuple[str, float]:
        """What makes two joints the same size — rows, legends and numbering never mix different ones."""
        return (self.category, self.diameter_in)


def is_riser(joint: Joint) -> bool:
    return joint.category == pipe_sizes.RISER


@dataclass
class Bay:
    name: str
    length_m: float  # fixed physical length (how much fits side by side)
    height_m: float  # fixed physical stacking height
    row_capacity_overrides: dict[float, int] = field(default_factory=dict)  # diameter_in -> joints/row
    joints: list[Joint] = field(default_factory=list)
    riser_stacking: str = "nested"  # key of RISER_STACKING_MODES; only matters while the bay holds risers
    number_direction: str = "bottom_up_ltr"  # key of NUMBERING_DIRECTIONS
    number_start: int = 1  # first automatic number in each count
    number_grouping: str = "per_type"  # key of NUMBERING_GROUPINGS
    # Own start number for specific type + size groups (when counted separately), keyed by
    # group_key(); groups not listed start at number_start.
    group_starts: dict[str, int] = field(default_factory=dict)

    def count(self) -> int:
        return len(self.joints)

    def holds_risers(self) -> bool:
        return any(is_riser(j) for j in self.joints)

    def get_joint(self, order: int) -> Joint | None:
        return next((j for j in self.joints if j.order == order), None)

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
    merge_groups: list[list[str]] = field(default_factory=list)  # groups of original bay names now merged

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
    if name in RISER_TYPES:
        raise ValueError(f"'{name}' is a built-in riser type — pick another name.")
    if job.get_joint_type(name) is not None:
        raise ValueError(f"A joint type named '{name}' already exists.")
    job.joint_types.append(JointType(name=name, ring_color=ring_color, label=label or None))


def remove_joint_type(job: Job, name: str) -> None:
    job.joint_types = [t for t in job.joint_types if t.name != name]


# ---------------------------------------------------------------- bay merging
# Some adjacent bays are separated by a removable post (see rigs.mergeable_sequences).
# Merging/unmerging is only allowed while the affected bays are empty, so there's never
# any ambiguity about which original bay a joint "belongs to" when splitting back apart.


def _mergeable_sequence_for(rig_name: str, bay_name: str) -> dict | None:
    for seq in rigs.mergeable_sequences(rig_name):
        if bay_name in seq["bays"]:
            return seq
    return None


def merged_bay_name(bay_names: list[str]) -> str:
    """A readable name for a merge group, e.g. ["Pipe Bay 2", "Pipe Bay 3"] -> "Pipe Bay 2-3"."""
    parsed = [re.match(r"^(.*?)(\d+)$", n) for n in bay_names]
    if all(parsed) and len({m.group(1) for m in parsed}) == 1:
        prefix = parsed[0].group(1)
        nums = [m.group(2) for m in parsed]
        return f"{prefix}{nums[0]}-{nums[-1]}" if len(nums) > 1 else f"{prefix}{nums[0]}"
    return "+".join(bay_names)


def can_merge(job: Job, bay_names: list[str]) -> str | None:
    """Returns an error message if bay_names can't be merged right now, else None."""
    if len(bay_names) < 2:
        return "Select at least two bays to merge."
    seq = _mergeable_sequence_for(job.rig_name, bay_names[0])
    if seq is None or not all(n in seq["bays"] for n in bay_names):
        return "All selected bays must be from the same mergeable group."
    seq_order = seq["bays"]
    indices = sorted(seq_order.index(n) for n in bay_names)
    if indices != list(range(indices[0], indices[0] + len(indices))):
        return "Only bays next to each other can be merged."
    heights = set()
    for name in bay_names:
        bay = job.get_bay(name)
        if bay is None:
            return f"'{name}' isn't currently available to merge (already part of another merge?)."
        if bay.count() > 0:
            return f"'{name}' must be empty before merging — clear or remove its joints first."
        heights.add(bay.height_m)
    if len(heights) > 1:
        return "Selected bays have different heights — can't merge them."
    return None


def merge_bays(job: Job, bay_names: list[str]) -> None:
    error = can_merge(job, bay_names)
    if error:
        raise ValueError(error)
    seq = _mergeable_sequence_for(job.rig_name, bay_names[0])
    post_thickness_m = seq["post_thickness_m"]
    ordered_names = [n for n in seq["bays"] if n in bay_names]
    component_bays = [job.get_bay(n) for n in ordered_names]
    n = len(component_bays)
    merged_length_m = sum(b.length_m for b in component_bays) + post_thickness_m * (n - 1)
    merged_height_m = component_bays[0].height_m

    diameters = set()
    for b in component_bays:
        diameters.update(b.row_capacity_overrides.keys())
    merged_overrides = {}
    for d in diameters:
        if all(d in b.row_capacity_overrides for b in component_bays):
            base = sum(b.row_capacity_overrides[d] for b in component_bays)
            extra = int((post_thickness_m * (n - 1)) / pitch_m(d) + 1e-9)
            merged_overrides[d] = base + extra

    merged_bay = Bay(
        name=merged_bay_name(ordered_names),
        length_m=merged_length_m,
        height_m=merged_height_m,
        row_capacity_overrides=merged_overrides,
    )
    component_names = set(ordered_names)
    first_index = min(i for i, b in enumerate(job.bays) if b.name in component_names)
    job.bays = [b for b in job.bays if b.name not in component_names]
    job.bays.insert(first_index, merged_bay)
    job.merge_groups.append(ordered_names)


def unmerge_bay(job: Job, bay_name: str) -> None:
    bay = job.get_bay(bay_name)
    if bay is None:
        raise ValueError(f"'{bay_name}' not found.")
    group = next((g for g in job.merge_groups if merged_bay_name(g) == bay_name), None)
    if group is None:
        raise ValueError(f"'{bay_name}' isn't a merged bay.")
    if bay.count() > 0:
        raise ValueError(f"'{bay_name}' must be empty before splitting it back apart.")
    specs = {s["name"]: s for s in rigs.bay_specs(job.rig_name)}
    restored = [
        Bay(
            name=name,
            length_m=specs[name]["length_m"],
            height_m=specs[name]["height_m"],
            row_capacity_overrides=dict(specs[name].get("row_capacity", {})),
        )
        for name in group
    ]
    index = next(i for i, b in enumerate(job.bays) if b.name == bay_name)
    job.bays = job.bays[:index] + restored + job.bays[index + 1 :]
    job.merge_groups.remove(group)


# ---------------------------------------------------------------- physical packing
# Bays are a fixed physical size, but joints of different diameters can be added over
# time, so rows are packed by actual pipe size rather than a precomputed joint-count
# grid: joints fill a row left to right until the next one wouldn't physically fit,
# then a new row starts on top.


def pitch_m(diameter_in: float) -> float:
    """Physical center-to-center spacing a joint of this diameter needs in a row, clearance included."""
    # 48" is only ever a riser, and risers sit touching each other.
    factor = RISER_CLEARANCE_FACTOR if diameter_in == RISER_DIAMETER_IN else HORIZONTAL_CLEARANCE_FACTOR
    return diameter_in * INCHES_TO_METERS * factor


@dataclass
class Row:
    """One layer of a bay, positioned in meters from the bay's floor and left wall."""

    joints: list[Joint]  # left to right
    bottom_m: float
    height_m: float
    x_offset_m: float = 0.0  # nested riser layers start half a riser in from the wall

    @property
    def top_m(self) -> float:
        return self.bottom_m + self.height_m


def pack_rows(
    joints: list[Joint], length_m: float, capacity_overrides: dict[float, int] | None = None
) -> list[list[Joint]]:
    """Pack already bottom-up-ordered joints into rows constrained by the bay's length.

    A row never mixes pipe sizes — a size change (diameter or category) always starts a new
    row, even if the current row still has physical room left. If capacity_overrides has an
    entry for a joint's diameter, that exact joints-per-row count is used instead of the
    length/pitch formula (real rack capacity doesn't always match the formula exactly).
    """
    capacity_overrides = capacity_overrides or {}
    rows: list[list[Joint]] = []
    current: list[Joint] = []
    used_m = 0.0
    current_size: tuple[str, float] | None = None
    for j in joints:
        cap = capacity_overrides.get(j.diameter_in)
        if not current:
            new_row_needed = False
        elif j.size_key != current_size:
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
        current_size = j.size_key
    if current:
        rows.append(current)
    return rows


def _riser_rows(bay: Bay, ordered: list[Joint]) -> list[Row]:
    diameter_m = RISER_DIAMETER_IN * INCHES_TO_METERS
    pitch = pitch_m(RISER_DIAMETER_IN)
    cap = effective_row_capacity(bay, RISER_DIAMETER_IN)
    nested = bay.riser_stacking == "nested" and cap >= 2  # a single column can only stack straight up
    # A nested riser rests on the two below it, so its center is only this far above theirs.
    layer_rise_m = math.sqrt(diameter_m**2 - (pitch / 2) ** 2) if nested else diameter_m
    # Every other nested layer is shifted half a riser toward the far wall. If the bay has that
    # much spare length, the shifted layer still fits a full row (its last riser leans on the
    # far wall); otherwise it loses one riser.
    shifted_cap = cap if (cap + 0.5) * pitch <= bay.length_m + 1e-9 else cap - 1
    rows: list[Row] = []
    start = 0
    while start < len(ordered):
        shifted = nested and len(rows) % 2 == 1
        size = shifted_cap if shifted else cap
        rows.append(
            Row(
                joints=ordered[start : start + size],
                bottom_m=len(rows) * layer_rise_m,
                height_m=diameter_m,
                x_offset_m=pitch / 2 if shifted else 0.0,
            )
        )
        start += size
    return rows


def row_layout(bay: Bay, joints: list[Joint] | None = None) -> list[Row]:
    """Bottom-up rows of a bay's joints (or of a hypothetical set of joints for this bay)."""
    ordered = sorted(bay.joints if joints is None else joints, key=lambda j: j.order)
    if any(is_riser(j) for j in ordered):
        return _riser_rows(bay, ordered)
    rows: list[Row] = []
    cursor = 0.0
    for i, packed in enumerate(pack_rows(ordered, bay.length_m, bay.row_capacity_overrides)):
        if i > 0:
            cursor += DUNNAGE_M
        rows.append(Row(joints=packed, bottom_m=cursor, height_m=_row_height_m(packed)))
        cursor += rows[-1].height_m
    return rows


def formula_row_capacity(bay: Bay, diameter_in: float) -> int:
    """What the length/pitch formula alone would give for this size, ignoring any override."""
    return max(1, int(bay.length_m / pitch_m(diameter_in)))


def effective_row_capacity(bay: Bay, diameter_in: float) -> int:
    """The joints-per-row count actually used for this size: an override if set, else the formula."""
    override = bay.row_capacity_overrides.get(diameter_in)
    return override if override is not None else formula_row_capacity(bay, diameter_in)


def set_row_capacity_override(bay: Bay, diameter_in: float, max_per_row: int) -> None:
    if max_per_row < 1:
        raise ValueError("Max per layer must be at least 1.")
    bay.row_capacity_overrides[diameter_in] = max_per_row


def clear_row_capacity_override(bay: Bay, diameter_in: float) -> None:
    bay.row_capacity_overrides.pop(diameter_in, None)


def _row_height_m(row: list[Joint]) -> float:
    return max((j.vertical_diameter_in * INCHES_TO_METERS for j in row), default=0.0)


def stack_height_m(rows: list[Row]) -> float:
    return max((r.top_m for r in rows), default=0.0)


def preferred_breach_row(bay: Bay) -> int | None:
    """0-based row index where the stack first passes the preferred max height, or None if it never does.

    The preferred max is a guideline for pipe stacked on dunnage, so it doesn't apply to risers.
    """
    if bay.holds_risers():
        return None
    for i, row in enumerate(row_layout(bay)):
        if row.top_m > PREFERRED_MAX_STACK_HEIGHT_M:
            return i
    return None


def _check_fits(bay: Bay, joints: list[Joint], what: str) -> None:
    total_height = stack_height_m(row_layout(bay, joints))
    if total_height > bay.height_m + 1e-9:
        raise ValueError(
            f"{what} would need {total_height:.2f} m of stack height, "
            f"but '{bay.name}' only has {bay.height_m:g} m to work with."
        )


def _check_no_mixing(bay: Bay, riser: bool) -> None:
    others = [j for j in bay.joints if is_riser(j) != riser]
    if others:
        if riser:
            raise ValueError(f"'{bay.name}' has pipe/casing in it — risers can only go in a bay with nothing else.")
        raise ValueError(f"'{bay.name}' holds risers — no pipe or casing can go in the same bay.")


def _checked_size(type_name: str, category: str, diameter_in: float) -> tuple[str, float]:
    """The (category, diameter) a joint of this type ends up with — risers are always 48"."""
    if type_name in RISER_TYPES:
        return pipe_sizes.RISER, RISER_DIAMETER_IN
    if category == pipe_sizes.RISER:
        raise ValueError(f"'{type_name}' isn't a riser type.")
    if diameter_in <= 0:
        raise ValueError("Pipe diameter must be greater than 0.")
    return category, diameter_in


def add_joints(
    bay: Bay, type_name: str, count: int, category: str, diameter_in: float, label: str | None = None
) -> None:
    if count < 1:
        raise ValueError("Count must be at least 1.")
    category, diameter_in = _checked_size(type_name, category, diameter_in)
    _check_no_mixing(bay, category == pipe_sizes.RISER)
    ordered = sorted(bay.joints, key=lambda j: j.order)
    next_order = (ordered[-1].order if ordered else 0) + 1
    new_joints = [
        Joint(
            type_name=type_name,
            category=category,
            diameter_in=diameter_in,
            order=next_order + i,
            label=label or None,
        )
        for i in range(count)
    ]
    hypothetical = ordered + new_joints
    _check_fits(bay, hypothetical, f"Adding {count} × {pipe_sizes.size_label(category, diameter_in)}")
    bay.joints = hypothetical


def _set_joints_compacted(bay: Bay, joints: list[Joint]) -> None:
    """Store joints with their stacking orders renumbered 1..n (gaps closed, relative order kept)."""
    remaining = sorted(joints, key=lambda j: j.order)
    for new_order, j in enumerate(remaining, start=1):
        j.order = new_order
    bay.joints = remaining


def remove_joints(bay: Bay, count: int) -> None:
    """Remove the top `count` joints — the last ones stacked come off first."""
    if count < 1:
        raise ValueError("Count must be at least 1.")
    if count > len(bay.joints):
        raise ValueError(f"Cannot remove {count} joints — only {len(bay.joints)} in '{bay.name}'.")
    _set_joints_compacted(bay, sorted(bay.joints, key=lambda j: j.order)[:-count])


def delete_joint(bay: Bay, order: int) -> None:
    """Remove one specific joint; everything stacked after it moves down to close the gap."""
    if bay.get_joint(order) is None:
        raise ValueError(f"That joint is no longer in '{bay.name}'.")
    _set_joints_compacted(bay, [j for j in bay.joints if j.order != order])


def update_joint(
    bay: Bay,
    order: int,
    type_name: str,
    category: str,
    diameter_in: float,
    label: str | None,
    manual_number: int | None,
) -> None:
    joint = bay.get_joint(order)
    if joint is None:
        raise ValueError(f"That joint is no longer in '{bay.name}'.")
    category, diameter_in = _checked_size(type_name, category, diameter_in)
    if (category == pipe_sizes.RISER) != is_riser(joint):
        raise ValueError("A riser can only be changed to another riser type, and pipe/casing to another pipe type.")
    if manual_number is not None and manual_number < 1:
        raise ValueError("Number must be at least 1.")
    updated = replace(
        joint,
        type_name=type_name,
        category=category,
        diameter_in=diameter_in,
        label=label or None,
        manual_number=manual_number,
    )
    hypothetical = [updated if j is joint else j for j in bay.joints]
    _check_fits(bay, hypothetical, "That change")
    bay.joints = hypothetical


def set_riser_stacking(bay: Bay, mode: str) -> None:
    if mode not in RISER_STACKING_MODES:
        raise ValueError(f"Unknown riser stacking '{mode}'.")
    if bay.holds_risers():
        _check_fits(replace(bay, riser_stacking=mode), bay.joints, f"Switching to {mode} stacking")
    bay.riser_stacking = mode


def set_numbering(bay: Bay, direction: str, start: int, grouping: str) -> None:
    if direction not in NUMBERING_DIRECTIONS:
        raise ValueError(f"Unknown numbering direction '{direction}'.")
    if grouping not in NUMBERING_GROUPINGS:
        raise ValueError(f"Unknown numbering grouping '{grouping}'.")
    if start < 1:
        raise ValueError("Start number must be at least 1.")
    bay.number_direction = direction
    bay.number_start = start
    bay.number_grouping = grouping


def group_key(type_name: str, category: str, diameter_in: float) -> str:
    """Identifies a type + size group; a string so it can be a JSON object key."""
    return json.dumps([type_name, category, diameter_in])


def number_groups(bay: Bay) -> list[tuple[str, str, float]]:
    """(type_name, category, diameter) of every type + size group in the bay, sorted by size then type."""
    groups = {(j.type_name, j.category, j.diameter_in) for j in bay.joints}
    return sorted(groups, key=lambda g: (g[2], g[1], g[0]))


def group_start(bay: Bay, type_name: str, category: str, diameter_in: float) -> int:
    return bay.group_starts.get(group_key(type_name, category, diameter_in), bay.number_start)


def set_group_start(bay: Bay, type_name: str, category: str, diameter_in: float, start: int) -> None:
    """Give one type + size group its own start number; setting it back to the bay's start clears it."""
    if start < 1:
        raise ValueError("Start number must be at least 1.")
    key = group_key(type_name, category, diameter_in)
    if start == bay.number_start:
        bay.group_starts.pop(key, None)
    else:
        bay.group_starts[key] = start


def _number_group(bay: Bay, joint: Joint) -> str:
    """Joints with the same key share one count."""
    if bay.number_grouping == "per_type":
        return group_key(joint.type_name, joint.category, joint.diameter_in)
    return ""


def _count_start(bay: Bay, key: str) -> int:
    return bay.group_starts.get(key, bay.number_start) if key else bay.number_start


def clear_manual_numbers(bay: Bay) -> None:
    for j in bay.joints:
        j.manual_number = None


def display_numbers(bay: Bay) -> dict[int, int]:
    """joint order -> number shown on it.

    Each type + size has its own count (starting at its own start number, if it has one),
    or the whole bay shares one count, per bay.number_grouping.
    Joints are numbered in the bay's chosen direction through the current layout, so the
    numbers follow the joints' positions. A manually numbered joint keeps its number, and
    automatic numbering skips over the numbers already taken manually in that count.
    """
    rows = row_layout(bay)
    if bay.number_direction.startswith("top_down"):
        rows = rows[::-1]
    right_to_left = bay.number_direction.endswith("rtl")
    sequence = [j for r in rows for j in (reversed(r.joints) if right_to_left else r.joints)]

    taken: dict[str, set[int]] = {}
    for j in sequence:
        if j.manual_number is not None:
            taken.setdefault(_number_group(bay, j), set()).add(j.manual_number)
    next_number: dict[str, int] = {}
    numbers: dict[int, int] = {}
    for j in sequence:
        if j.manual_number is not None:
            numbers[j.order] = j.manual_number
            continue
        key = _number_group(bay, j)
        n = next_number.get(key, _count_start(bay, key))
        while n in taken.get(key, ()):
            n += 1
        numbers[j.order] = n
        next_number[key] = n + 1
    return numbers


def clear_bay(bay: Bay) -> None:
    bay.joints = []


def job_to_dict(job: Job) -> dict:
    return asdict(job)


def job_from_dict(data: dict) -> Job:
    bays = [
        Bay(
            **{
                **b,
                # JSON object keys are always strings, so diameters need converting back to float.
                "row_capacity_overrides": {float(k): v for k, v in b["row_capacity_overrides"].items()},
                "joints": [Joint(**j) for j in b["joints"]],
            }
        )
        for b in data["bays"]
    ]
    return Job(
        **{**data, "bays": bays, "joint_types": [JointType(**t) for t in data["joint_types"]]}
    )
