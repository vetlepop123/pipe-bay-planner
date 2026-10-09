"""Pipe Bay Layout Planning Tool — Streamlit app."""

from __future__ import annotations

import base64
import io
import json

import streamlit as st

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


def _save() -> None:
    storage.save_job(st.session_state.job)


# ---------------------------------------------------------------- job setup
PIPE_SIZE_OPTIONS = pipe_sizes.options()
PIPE_SIZE_LABELS = [label for label, _ in PIPE_SIZE_OPTIONS]
PIPE_SIZE_BY_LABEL = dict(PIPE_SIZE_OPTIONS)

if "job" not in st.session_state:
    existing = storage.list_jobs()
    st.session_state.job = storage.load_job(existing[0]) if existing else models.new_job("New Job", rigs.rig_names()[0])

job = st.session_state.job

st.sidebar.header("Job")
existing_jobs = storage.list_jobs()
job_options = existing_jobs if job.name in existing_jobs else [job.name] + existing_jobs
selected = st.sidebar.selectbox("Current job", job_options, index=job_options.index(job.name))
if selected != job.name:
    st.session_state.job = storage.load_job(selected)
    st.rerun()

with st.sidebar.expander("New job"):
    new_name = st.text_input("Job name", key="new_job_name")
    new_rig = st.selectbox("Rig", rigs.rig_names(), key="new_job_rig")
    if st.button("Create job"):
        if not new_name.strip():
            st.warning("Enter a name.")
        elif new_name.strip() in storage.list_jobs():
            st.warning("A job with that name already exists.")
        else:
            st.session_state.job = models.new_job(new_name.strip(), new_rig)
            _save()
            st.rerun()

with st.sidebar.expander("Rename / delete job"):
    rename = st.text_input("Rename to", value=job.name, key="rename_job")
    rcol1, rcol2 = st.columns(2)
    if rcol1.button("Rename"):
        new_name = rename.strip()
        if new_name and new_name != job.name:
            old_name = job.name
            job.name = new_name
            _save()
            storage.delete_job(old_name)
            st.rerun()
    if rcol2.button("Delete job"):
        storage.delete_job(job.name)
        st.session_state.job = models.new_job("New Job", rigs.rig_names()[0])
        st.rerun()

with st.sidebar.expander("Backup / restore"):
    backup_bytes = json.dumps(models.job_to_dict(job), indent=2).encode("utf-8")
    st.download_button(
        "Download this job as a file",
        backup_bytes,
        file_name=f"{job.name}.json",
        mime="application/json",
        help="Save a local copy — especially important on a hosted deployment, where storage isn't guaranteed to persist.",
    )
    restore_file = st.file_uploader("Restore from a downloaded file", type="json", key="restore_upload")
    if restore_file is not None:
        try:
            restored = models.job_from_dict(json.loads(restore_file.read().decode("utf-8")))
            st.session_state.job = restored
            storage.save_job(restored)
            st.success(f"Restored '{restored.name}'.")
            st.rerun()
        except Exception as e:
            st.error(f"Couldn't read that file: {e}")

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
        if st.button("Remove joint type"):
            models.remove_joint_type(job, jt_to_remove)
            _save()
            st.rerun()

# ---------------------------------------------------------------- main area
st.title(job.name)
st.caption(f"Rig: {job.rig_name}")

if not job.bays:
    st.info("This job's rig has no bays configured — edit rigs.py to add some.")
else:
    st.subheader("Add / remove joints")
    bay_names = [b.name for b in job.bays]
    selected_bay_name = st.selectbox("Bay", bay_names, key="selected_bay")
    bay = job.get_bay(selected_bay_name)

    type_options = [t.name for t in job.joint_types]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_type = st.selectbox("Type", type_options, key="add_type") if type_options else None
        if not type_options:
            st.caption("Define a joint type in the sidebar first.")
    with c2:
        add_size_label = st.selectbox("Pipe size", PIPE_SIZE_LABELS, key="add_size")
        add_diameter = PIPE_SIZE_BY_LABEL[add_size_label]
    with c3:
        add_count = st.number_input("Count", min_value=1, value=1, step=1, key="add_count")
    with c4:
        selected_type = job.get_joint_type(sel_type) if sel_type else None
        placeholder = f'e.g. "{selected_type.label}", or leave blank to number' if selected_type and selected_type.label else "leave blank to number automatically"
        add_label = st.text_input("Label (optional)", key="add_label", placeholder=placeholder)

    ac1, ac2, ac3 = st.columns(3)
    with ac1:
        if st.button("Add joints", disabled=not type_options):
            try:
                models.add_joints(bay, sel_type, int(add_count), float(add_diameter), add_label.strip() or None)
                _save()
                st.rerun()
            except ValueError as e:
                st.warning(str(e))
    with ac2:
        remove_count = st.number_input("Remove count", min_value=1, value=1, step=1, key="remove_count")
        if st.button("Remove joints"):
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

    fig = render.build_figure(job, zoom=zoom_pct / 100, only_bays=display_bay_names)
    view_buf = io.BytesIO()
    fig.savefig(view_buf, format="png", bbox_inches="tight", dpi=150)
    view_b64 = base64.b64encode(view_buf.getvalue()).decode()
    st.markdown(
        f'<div style="width:100%; overflow-x:auto; border:1px solid #ddd; border-radius:4px; padding:6px;">'
        f'<img src="data:image/png;base64,{view_b64}" '
        f'style="display:block; max-width:none; width:auto; height:auto;">'
        f"</div>",
        unsafe_allow_html=True,
    )

    export_fig = fig if zoom_pct == 100 else render.build_figure(job, only_bays=display_bay_names)
    png_buf = io.BytesIO()
    export_fig.savefig(png_buf, format="png", bbox_inches="tight", dpi=200)
    pdf_buf = io.BytesIO()
    export_fig.savefig(pdf_buf, format="pdf", bbox_inches="tight")

    dl1, dl2 = st.columns(2)
    dl1.download_button("Download PNG", png_buf.getvalue(), file_name=f"{job.name}.png", mime="image/png")
    dl2.download_button(
        "Download PDF", pdf_buf.getvalue(), file_name=f"{job.name}.pdf", mime="application/pdf"
    )

    st.subheader("Snapshots")
    scol1, scol2 = st.columns(2)
    with scol1:
        snap_label = st.text_input("Save current state as", key="snap_label")
        if st.button("Save snapshot"):
            if snap_label.strip():
                storage.save_snapshot(job, snap_label.strip())
                st.success(f"Saved snapshot '{snap_label.strip()}'.")
            else:
                st.warning("Enter a snapshot name.")
    with scol2:
        snaps = storage.list_snapshots(job.name)
        if snaps:
            snap_to_load = st.selectbox("Load snapshot", snaps, key="snap_load_select")
            if st.button("Load snapshot"):
                st.session_state.job = storage.load_snapshot(job.name, snap_to_load)
                _save()
                st.rerun()
        else:
            st.caption("No snapshots yet.")
