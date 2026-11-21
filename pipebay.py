"""Pipe Bay Layout Planning Tool — Streamlit app."""

from __future__ import annotations

import json

import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates

import models
import pipe_sizes
import render
import rigs
import storage

st.set_page_config(page_title="Pipe Bay Planner", layout="wide")


def _required_password() -> str | None:
    # No secrets.toml locally means no password configured — the gate is only enforced
    # once a deployment sets an app_password secret.
    try:
        return st.secrets.get("app_password")
    except Exception:
        return None


def _check_password() -> bool:
    required = _required_password()
    if not required or st.session_state.get("authenticated"):
        return True
    st.title("Pipe Bay Planner")
    pw = st.text_input("Password", type="password", key="login_password")
    if st.button("Log in"):
        if pw == required:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


if not _check_password():
    st.stop()


def _database_url() -> str | None:
    # Set database_url in Streamlit Cloud's Secrets to a hosted Postgres database, e.g.
    #   database_url = "postgresql://user:password@host:5432/postgres"
    # Without it, jobs go to a local SQLite file (fine locally, wiped on Streamlit Cloud).
    try:
        return st.secrets.get("database_url")
    except Exception:
        return None


try:
    storage.connect(_database_url())
except Exception as e:
    st.error(f"Couldn't connect to the database: {e}")
    st.stop()


def _save() -> None:
    try:
        storage.save_job(st.session_state.job)
    except Exception as e:
        st.warning(f"Your change applied, but saving it to the database failed: {e}")


# Bay settings widgets mirror the model: their state is reset from the bay every run, and
# changes go through these callbacks, so a refused change snaps the widget back and a
# loaded job/snapshot always shows its own settings.
def _flash(message: str) -> None:
    st.session_state.setdefault("flash", []).append(message)


def _on_riser_stacking_change(bay_name: str, key: str) -> None:
    bay = st.session_state.job.get_bay(bay_name)
    if bay is None:
        return
    try:
        models.set_riser_stacking(bay, st.session_state[key])
        _save()
    except ValueError as e:
        _flash(str(e))


def _on_numbering_change(bay_name: str, direction_key: str, start_key: str, grouping_key: str) -> None:
    bay = st.session_state.job.get_bay(bay_name)
    if bay is None:
        return
    try:
        models.set_numbering(
            bay, st.session_state[direction_key], int(st.session_state[start_key]), st.session_state[grouping_key]
        )
        _save()
    except ValueError as e:
        _flash(str(e))


def _on_group_start_change(bay_name: str, group: tuple[str, str, float], key: str) -> None:
    bay = st.session_state.job.get_bay(bay_name)
    if bay is None:
        return
    try:
        models.set_group_start(bay, *group, int(st.session_state[key]))
        _save()
    except ValueError as e:
        _flash(str(e))


def _select_joint(selection: tuple[str, int] | None) -> None:
    st.session_state.selected_joint = selection
    # Fresh widget keys per selection, so the edit form never shows a previous joint's values.
    st.session_state.edit_nonce = st.session_state.get("edit_nonce", 0) + 1


# Streamlit reruns this whole script on every click, so drawing is cached on the job's
# contents (as JSON) and only redone when something that's drawn actually changes.
@st.cache_data(max_entries=20, show_spinner=False)
def _cached_view(job_json: str, zoom: float, bays: tuple[str, ...], selected: tuple[str, int] | None) -> render.View:
    return render.render_view(models.job_from_dict(json.loads(job_json)), zoom, list(bays), selected)


@st.cache_data(max_entries=10, show_spinner="Preparing download…")
def _cached_export(job_json: str, bays: tuple[str, ...], fmt: str) -> bytes:
    return render.export_bytes(models.job_from_dict(json.loads(job_json)), list(bays), fmt)


class _EncodedPng:
    """Hands already-encoded PNG bytes to streamlit_image_coordinates, which otherwise
    wants an image object and would decode and re-compress the whole picture every run."""

    def __init__(self, data: bytes):
        self.data = data

    def save(self, fp, **_) -> None:
        fp.write(self.data)


# ---------------------------------------------------------------- job setup
PIPE_SIZE_OPTIONS = pipe_sizes.options()
PIPE_SIZE_LABELS = [label for label, _, _ in PIPE_SIZE_OPTIONS]
PIPE_SIZE_BY_LABEL = {label: (category, d) for label, category, d in PIPE_SIZE_OPTIONS}
PIPE_SIZE_LABEL_BY_SIZE = {size: label for label, size in PIPE_SIZE_BY_LABEL.items()}
RISER_SIZE_LABEL = f'Riser {models.RISER_DIAMETER_IN:g}"'


def _open_job(job: models.Job | None) -> None:
    """Switch to `job`, or back to the start screen if None."""
    st.session_state.job = job
    _select_joint(None)


def _start_screen() -> None:
    """Shown whenever no job is open, so nobody lands in (and edits) someone else's job by accident."""
    st.title("Pipe Bay Planner")
    st.write("Open a saved job, or start a new one.")
    open_col, new_col = st.columns(2, gap="large")

    with open_col, st.container(border=True):
        st.subheader("Open a saved job")
        jobs = storage.list_jobs()
        if jobs:
            name = st.selectbox("Job", jobs, index=None, placeholder="Choose a job…", key="start_open_name")
            if st.button("Open job", type="primary", disabled=name is None) and name is not None:
                try:
                    _open_job(storage.load_job(name))
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't open '{name}': {e}")
        else:
            st.caption("No saved jobs yet.")

    with new_col, st.container(border=True):
        st.subheader("Create a new job")
        new_name = st.text_input("Job name", key="start_new_name").strip()
        new_rig = st.selectbox("Rig", rigs.rig_names(), key="start_new_rig")
        if st.button("Create job", type="primary"):
            if not new_name:
                st.warning("Enter a name.")
            elif new_name in storage.list_jobs():
                st.warning("A job with that name already exists — open it instead, or pick another name.")
            else:
                _open_job(models.new_job(new_name, new_rig))
                _save()
                st.rerun()

    with st.expander("Restore a job from a downloaded backup file"):
        restore_file = st.file_uploader("Backup file", type="json", key="restore_upload")
        if restore_file is not None:
            try:
                restored = models.job_from_dict(json.loads(restore_file.getvalue().decode("utf-8")))
            except Exception as e:
                st.error(f"Couldn't read that file: {e}")
                return
            exists = restored.name in storage.list_jobs()
            if exists:
                st.warning(f"A saved job named '{restored.name}' already exists. Restoring replaces it with the file.")
            confirmed = st.checkbox(f"Replace the saved '{restored.name}'", key="restore_confirm") if exists else True
            if st.button("Restore job", disabled=not confirmed):
                try:
                    storage.save_job(restored)
                    _open_job(restored)
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't save the restored job: {e}")


if st.session_state.get("job") is None:
    _start_screen()
    st.stop()

job = st.session_state.job

st.sidebar.header("Job")
st.sidebar.markdown(f"**{job.name}**  \n{job.rig_name}")
if st.sidebar.button("Close job", help="Back to the start screen, to open another job or create a new one."):
    _open_job(None)
    st.rerun()

with st.sidebar.expander("Rename / delete job"):
    # Keyed per job, so a name typed while another job was open isn't carried over.
    rename = st.text_input("Rename to", value=job.name, key=f"rename_job_{job.name}")
    rcol1, rcol2 = st.columns(2)
    if rcol1.button("Rename"):
        new_name = (rename or "").strip()
        if new_name and new_name != job.name:
            try:
                storage.rename_job(job, new_name)
                st.rerun()
            except Exception as e:
                st.warning(f"Couldn't rename: {e}")
    if rcol2.button("Delete job"):
        try:
            storage.delete_job(job.name)
            _open_job(None)
            st.rerun()
        except Exception as e:
            st.warning(f"Couldn't delete '{job.name}': {e}")

with st.sidebar.expander("Backup"):
    backup_bytes = json.dumps(models.job_to_dict(job), indent=2).encode("utf-8")
    st.download_button(
        "Download this job as a file",
        backup_bytes,
        file_name=f"{job.name}.json",
        mime="application/json",
        help="Save a copy of this job on your own computer, e.g. before big changes. "
        "Restore it from the start screen (Close job).",
    )

mergeable_names = {n for seq in rigs.mergeable_sequences(job.rig_name) for n in seq["bays"]}
mergeable_candidates = [b.name for b in job.bays if b.name in mergeable_names]
if mergeable_candidates or job.merge_groups:
    with st.sidebar.expander("Merge pipe bays"):
        if mergeable_candidates:
            to_merge = st.multiselect(
                "Select adjacent bays to merge",
                mergeable_candidates,
                key="merge_select",
                help="Only bays next to each other can be merged (e.g. 2-3, 3-4-5), and they must be empty first.",
            )
            if st.button("Merge selected"):
                try:
                    models.merge_bays(job, to_merge)
                    _save()
                    st.rerun()
                except ValueError as e:
                    st.warning(str(e))
        if job.merge_groups:
            st.caption("Currently merged:")
            for group in job.merge_groups:
                merged_name = models.merged_bay_name(group)
                mcol1, mcol2 = st.columns([3, 1])
                mcol1.write(merged_name)
                if mcol2.button("Split", key=f"unmerge_{merged_name}"):
                    try:
                        models.unmerge_bay(job, merged_name)
                        _save()
                        st.rerun()
                    except ValueError as e:
                        st.warning(str(e))

st.sidebar.header("Joint types (legend)")
with st.sidebar.expander("Add joint type"):
    jt_name = st.text_input("Name", key="new_jt_name")
    jt_ring = st.color_picker("Ring color", "#000000", key="new_jt_ring")
    jt_label = st.text_input("Default label (optional, e.g. SHOE)", key="new_jt_label")
    if st.button("Add joint type"):
        try:
            models.add_joint_type(job, jt_name.strip(), jt_ring, jt_label.strip() or None)
            _save()
            st.rerun()
        except ValueError as e:
            st.warning(str(e))

if job.joint_types:
    with st.sidebar.expander("Remove joint type"):
        jt_to_remove = st.selectbox("Type", [t.name for t in job.joint_types], key="remove_jt_select")
        if st.button("Remove joint type") and jt_to_remove is not None:
            models.remove_joint_type(job, jt_to_remove)
            _save()
            st.rerun()

# ---------------------------------------------------------------- main area
st.title(job.name)
st.caption(f"Rig: {job.rig_name}")
for flash_message in st.session_state.pop("flash", []):
    st.warning(flash_message)

if not job.bays:
    st.info("This job's rig has no bays configured — edit rigs.py to add some.")
else:
    st.subheader("Add / remove joints")
    bay_names = [b.name for b in job.bays]
    selected_bay_name = st.selectbox("Bay", bay_names, key="selected_bay")
    bay = job.get_bay(selected_bay_name)
    if bay is None:
        st.error(f"Bay '{selected_bay_name}' not found.")
        st.stop()

    category = st.radio("Adding", ["Pipe / casing", "Riser"], horizontal=True, key="add_category")
    adding_risers = category == "Riser"
    if adding_risers and bay.joints and not bay.holds_risers():
        st.caption(f"⚠ '{bay.name}' has pipe/casing in it — risers need a bay with nothing else in it.")
    elif not adding_risers and bay.holds_risers():
        st.caption(f"⚠ '{bay.name}' holds risers — no pipe or casing can go in the same bay.")

    type_options = list(models.RISER_TYPES) if adding_risers else [t.name for t in job.joint_types]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_type = st.selectbox("Type", type_options, key=f"add_type_{category}") if type_options else None
        if not type_options:
            st.caption("Define a joint type in the sidebar first.")
    with c2:
        if adding_risers:
            st.selectbox("Pipe size", [RISER_SIZE_LABEL], disabled=True, key="add_size_riser")
            add_category, add_diameter = pipe_sizes.RISER, models.RISER_DIAMETER_IN
        else:
            add_size_label = st.selectbox("Pipe size", PIPE_SIZE_LABELS, key="add_size")
            add_category, add_diameter = PIPE_SIZE_BY_LABEL[add_size_label]
    with c3:
        add_count = st.number_input("Count", min_value=1, value=1, step=1, key="add_count")
    with c4:
        selected_type = job.get_joint_type(sel_type) if sel_type else None
        placeholder = f'e.g. "{selected_type.label}", or leave blank to number' if selected_type and selected_type.label else "leave blank to number automatically"
        add_label = st.text_input("Label (optional)", key="add_label", placeholder=placeholder)

    mc1, mc2 = st.columns([2, 1])
    with mc1:
        current_override = bay.row_capacity_overrides.get(add_diameter)
        current_effective = models.effective_row_capacity(bay, add_diameter)
        source = "manually set" if current_override is not None else "automatic"
        max_per_layer = st.number_input(
            "Max per layer for this size (optional)",
            min_value=0,
            value=0,
            step=1,
            # Keyed to the current bay+size so switching either resets this to 0, rather than
            # silently carrying a leftover override value onto a bay/size it was never meant for.
            key=f"max_per_layer_{bay.name}_{add_diameter}",
            help=f"Currently {current_effective} per layer in '{bay.name}' ({source}). "
            "Leave at 0 to keep that; set a number to override it for this size in this bay going forward.",
        )
    with mc2:
        st.write("")
        st.write("")
        if current_override is not None:
            if st.button("Reset to automatic"):
                models.clear_row_capacity_override(bay, add_diameter)
                _save()
                st.rerun()

    if adding_risers or bay.holds_risers():
        stacking_key = f"riser_stacking_{bay.name}"
        st.session_state[stacking_key] = bay.riser_stacking
        st.radio(
            "Riser stacking",
            list(models.RISER_STACKING_MODES),
            format_func=models.RISER_STACKING_MODES.__getitem__,
            horizontal=True,
            key=stacking_key,
            on_change=_on_riser_stacking_change,
            args=(bay.name, stacking_key),
            help="Nested: each layer sits in the grooves of the one below, every other layer shifted half a "
            "riser over (one riser fewer if the bay isn't wide enough for the shift). "
            "Perfect: risers stacked straight on top of each other. No dunnage is used either way.",
        )

    ac1, ac2, ac3 = st.columns(3)
    with ac1:
        if st.button("Add joints", disabled=not type_options):
            if sel_type is None:
                st.warning("Define a joint type first.")
            else:
                try:
                    if max_per_layer > 0:
                        models.set_row_capacity_override(bay, add_diameter, int(max_per_layer))
                    models.add_joints(
                        bay, sel_type, int(add_count), add_category, float(add_diameter), add_label.strip() or None
                    )
                    _save()
                    st.rerun()
                except ValueError as e:
                    st.warning(str(e))
    with ac2:
        remove_count = st.number_input("Remove count", min_value=1, value=1, step=1, key="remove_count")
        if st.button("Remove from top", help="Removes the last joints stacked, top of the pile first."):
            try:
                models.remove_joints(bay, int(remove_count))
                _save()
                st.rerun()
            except ValueError as e:
                st.warning(str(e))
    with ac3:
        st.write("")
        st.write("")
        if st.button("Clear bay"):
            models.clear_bay(bay)
            _save()
            st.rerun()

    used_height = models.stack_height_m(models.row_layout(bay))
    st.caption(f"{bay.name}: {bay.count()} joints · {used_height:.2f}/{bay.height_m:g} m used")
    if bay.joints:
        counts = bay.count_by_type()
        st.caption(" · ".join(f"{n}: {c}" for n, c in counts.items()))
    if models.preferred_breach_row(bay) is not None:
        st.caption(
            "⚠ this bay is stacked above the preferred "
            f"{models.PREFERRED_MAX_STACK_HEIGHT_M:g} m height — only go this high if space requires it."
        )

    with st.expander(f"Numbering in {bay.name}"):
        direction_key = f"number_direction_{bay.name}"
        start_key = f"number_start_{bay.name}"
        grouping_key = f"number_grouping_{bay.name}"
        st.session_state[direction_key] = bay.number_direction
        st.session_state[start_key] = bay.number_start
        st.session_state[grouping_key] = bay.number_grouping
        numbering_args = (bay.name, direction_key, start_key, grouping_key)
        nc1, nc2, nc3 = st.columns(3)
        nc1.selectbox(
            "Count",
            list(models.NUMBERING_GROUPINGS),
            format_func=models.NUMBERING_GROUPINGS.__getitem__,
            key=grouping_key,
            on_change=_on_numbering_change,
            args=numbering_args,
            help="Separately: every type + size starts its own count (Slick 9 5/8\" 1, 2, 3… and Shoetrack 1, 2…). "
            "Together: one count through the whole bay, whatever the type.",
        )
        nc2.selectbox(
            "Direction",
            list(models.NUMBERING_DIRECTIONS),
            format_func=models.NUMBERING_DIRECTIONS.__getitem__,
            key=direction_key,
            on_change=_on_numbering_change,
            args=numbering_args,
            help="The order joints are counted in through the current stack.",
        )
        nc3.number_input(
            "Start at",
            min_value=1,
            step=1,
            key=start_key,
            on_change=_on_numbering_change,
            args=numbering_args,
            help="e.g. to continue the count from another bay. "
            "When counting separately, this is where every type + size starts unless given its own start below.",
        )
        groups = models.number_groups(bay)
        if bay.number_grouping == "per_type" and groups:
            st.markdown("**Start number per type + size**")
            group_cols = st.columns(min(len(groups), 4))
            for i, group in enumerate(groups):
                type_name, group_category, diameter = group
                gkey = f"group_start_{bay.name}_{models.group_key(*group)}"
                st.session_state[gkey] = models.group_start(bay, *group)
                group_cols[i % len(group_cols)].number_input(
                    f"{type_name} {pipe_sizes.size_label(group_category, diameter)}",
                    min_value=1,
                    step=1,
                    key=gkey,
                    on_change=_on_group_start_change,
                    args=(bay.name, group, gkey),
                )
            st.caption(f"Set a group back to {bay.number_start} to make it follow \"Start at\" again.")
        manual_count = sum(1 for j in bay.joints if j.manual_number is not None)
        st.caption(
            "To give a single joint its own number, click it in the deck plan below. "
            "The other joints in the same count skip over that number."
        )
        if manual_count and st.button(f"Clear {manual_count} manual number(s)"):
            models.clear_manual_numbers(bay)
            _save()
            st.rerun()

    st.subheader("Deck plan")
    dc1, dc2 = st.columns(2)
    with dc1:
        with st.popover("Bays to show"):
            display_bay_names = [name for name in bay_names if st.checkbox(name, value=True, key=f"show_bay_{name}")]
        st.caption("Controls both the view below and the PNG/PDF export.")
    with dc2:
        zoom_pct = st.selectbox(
            "Zoom",
            [50, 75, 100, 125, 150, 175, 200],
            index=2,
            format_func=lambda v: f"{v}%",
            key="zoom_pct",
            help="Zoom out to fit more on screen, or in to make small-diameter joints and labels easier to read "
            "(scroll sideways to pan when zoomed in).",
        )

    selected = st.session_state.get("selected_joint")
    if selected is not None:
        sel_bay = job.get_bay(selected[0])
        if sel_bay is None or sel_bay.get_joint(selected[1]) is None or selected[0] not in display_bay_names:
            _select_joint(None)  # deleted, cleared, merged away or hidden since it was clicked
            selected = None

    job_json = json.dumps(models.job_to_dict(job), sort_keys=True)
    view = _cached_view(job_json, zoom_pct / 100, tuple(display_bay_names), selected)
    st.caption("Click a joint to change or delete it.")
    click = streamlit_image_coordinates(_EncodedPng(view.png), key="deck_plan_click", cursor="pointer")
    # The component keeps returning its last click on every rerun, so only act on a new one.
    if click and click.get("unix_time") != st.session_state.get("last_click_time"):
        st.session_state.last_click_time = click.get("unix_time")
        scale = view.width / click["width"] if click.get("width") else 1.0
        hit = view.joint_at(click["x"] * scale, click["y"] * scale)
        clicked = (hit.bay_name, hit.order) if hit else None
        if clicked != selected:
            _select_joint(clicked)
            st.rerun()

    if selected is not None:
        sel_bay = job.get_bay(selected[0])
        joint = sel_bay.get_joint(selected[1]) if sel_bay else None
        if sel_bay is not None and joint is not None:
            nonce = st.session_state.get("edit_nonce", 0)
            number = models.display_numbers(sel_bay)[joint.order]
            riser = models.is_riser(joint)
            with st.container(border=True):
                st.markdown(
                    f"**Selected:** {sel_bay.name} — {joint.type_name} "
                    f"{pipe_sizes.size_label(joint.category, joint.diameter_in)} #{number}"
                    + (f' (labelled "{joint.label}")' if joint.label else "")
                )
                edit_types = list(models.RISER_TYPES) if riser else [t.name for t in job.joint_types]
                if joint.type_name not in edit_types:
                    edit_types.insert(0, joint.type_name)  # its type was since removed from the legend
                e1, e2, e3, e4 = st.columns(4)
                new_type = e1.selectbox(
                    "Type", edit_types, index=edit_types.index(joint.type_name), key=f"edit_type_{nonce}"
                )
                if riser:
                    e2.selectbox("Pipe size", [RISER_SIZE_LABEL], disabled=True, key=f"edit_size_{nonce}")
                    new_category, new_diameter = joint.size_key
                else:
                    new_size = e2.selectbox(
                        "Pipe size",
                        PIPE_SIZE_LABELS,
                        index=PIPE_SIZE_LABELS.index(PIPE_SIZE_LABEL_BY_SIZE[joint.size_key]),
                        key=f"edit_size_{nonce}",
                    )
                    new_category, new_diameter = PIPE_SIZE_BY_LABEL[new_size]
                new_label = e3.text_input("Label (optional)", value=joint.label or "", key=f"edit_label_{nonce}")
                new_number = e4.number_input(
                    "Number (0 = automatic)",
                    min_value=0,
                    value=joint.manual_number or 0,
                    step=1,
                    key=f"edit_number_{nonce}",
                    help="Pin this joint's number. The other joints in the same count skip over it.",
                )
                b1, b2, b3 = st.columns(3)
                if b1.button("Save changes", type="primary"):
                    try:
                        models.update_joint(
                            sel_bay,
                            joint.order,
                            new_type,
                            new_category,
                            float(new_diameter),
                            new_label.strip() or None,
                            int(new_number) or None,
                        )
                        _save()
                        st.rerun()
                    except ValueError as e:
                        st.warning(str(e))
                if b2.button("Delete joint"):
                    try:
                        models.delete_joint(sel_bay, joint.order)
                        _select_joint(None)
                        _save()
                        st.rerun()
                    except ValueError as e:
                        st.warning(str(e))
                if b3.button("Deselect"):
                    _select_joint(None)
                    st.rerun()

    # Exports are only drawn when a download button is actually clicked.
    export_bays = tuple(display_bay_names)
    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "Download PNG",
        lambda: _cached_export(job_json, export_bays, "png"),
        file_name=f"{job.name}.png",
        mime="image/png",
        on_click="ignore",
    )
    dl2.download_button(
        "Download PDF",
        lambda: _cached_export(job_json, export_bays, "pdf"),
        file_name=f"{job.name}.pdf",
        mime="application/pdf",
        on_click="ignore",
    )

    st.subheader("Snapshots")
    scol1, scol2 = st.columns(2)
    with scol1:
        snap_label = st.text_input("Save current state as", key="snap_label")
        if st.button("Save snapshot"):
            if snap_label.strip():
                try:
                    storage.save_snapshot(job, snap_label.strip())
                    st.success(f"Saved snapshot '{snap_label.strip()}'.")
                except Exception as e:
                    st.error(f"Couldn't save snapshot: {e}")
            else:
                st.warning("Enter a snapshot name.")
    with scol2:
        snaps = storage.list_snapshots(job.name)
        if snaps:
            snap_to_load = st.selectbox("Load / delete snapshot", snaps, key="snap_load_select")
            lcol, dcol = st.columns(2)
            if lcol.button("Load snapshot"):
                try:
                    _open_job(storage.load_snapshot(job.name, snap_to_load))
                    _save()
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't load snapshot '{snap_to_load}': {e}")
            if dcol.button("Delete snapshot"):
                try:
                    storage.delete_snapshot(job.name, snap_to_load)
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't delete snapshot '{snap_to_load}': {e}")
        else:
            st.caption("No snapshots yet.")
