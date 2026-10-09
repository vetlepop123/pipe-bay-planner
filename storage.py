"""JSON persistence for jobs and named snapshots."""

from __future__ import annotations

import json
import re
from pathlib import Path

from models import Job, job_to_dict, job_from_dict

DATA_DIR = Path(__file__).parent / "data"
JOBS_DIR = DATA_DIR / "jobs"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\- ]", "_", name).strip() or "unnamed"


def list_jobs() -> list[str]:
    if not JOBS_DIR.exists():
        return []
    return sorted(p.stem for p in JOBS_DIR.glob("*.json"))


def save_job(job: Job) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = JOBS_DIR / f"{_safe_filename(job.name)}.json"
    path.write_text(json.dumps(job_to_dict(job), indent=2), encoding="utf-8")


def load_job(name: str) -> Job:
    path = JOBS_DIR / f"{_safe_filename(name)}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return job_from_dict(data)


def delete_job(name: str) -> None:
    path = JOBS_DIR / f"{_safe_filename(name)}.json"
    if path.exists():
        path.unlink()
    snap_dir = SNAPSHOTS_DIR / _safe_filename(name)
    if snap_dir.exists():
        for p in snap_dir.glob("*.json"):
            p.unlink()
        snap_dir.rmdir()


def save_snapshot(job: Job, label: str) -> None:
    snap_dir = SNAPSHOTS_DIR / _safe_filename(job.name)
    snap_dir.mkdir(parents=True, exist_ok=True)
    path = snap_dir / f"{_safe_filename(label)}.json"
    path.write_text(json.dumps(job_to_dict(job), indent=2), encoding="utf-8")


def list_snapshots(job_name: str) -> list[str]:
    snap_dir = SNAPSHOTS_DIR / _safe_filename(job_name)
    if not snap_dir.exists():
        return []
    return sorted(p.stem for p in snap_dir.glob("*.json"))


def load_snapshot(job_name: str, label: str) -> Job:
    path = SNAPSHOTS_DIR / _safe_filename(job_name) / f"{_safe_filename(label)}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return job_from_dict(data)


def delete_snapshot(job_name: str, label: str) -> None:
    path = SNAPSHOTS_DIR / _safe_filename(job_name) / f"{_safe_filename(label)}.json"
    if path.exists():
        path.unlink()
