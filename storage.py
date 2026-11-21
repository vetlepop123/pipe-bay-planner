"""Database persistence for jobs and named snapshots.

Jobs are stored as JSON in a SQL database. Locally that's a SQLite file in data/; on a
hosted deployment (whose own disk is wiped on every restart) point it at a hosted Postgres
database instead by setting the `database_url` secret — see pipebay._database_url.
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa

from models import Job, job_to_dict, job_from_dict

LOCAL_DATABASE_URL = f"sqlite:///{(Path(__file__).parent / 'data' / 'pipebay.db').as_posix()}"

_metadata = sa.MetaData()
_jobs = sa.Table(
    "jobs",
    _metadata,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("data", sa.Text, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
)
_snapshots = sa.Table(
    "snapshots",
    _metadata,
    sa.Column("job_name", sa.Text, primary_key=True),
    sa.Column("label", sa.Text, primary_key=True),
    sa.Column("data", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
)

_engine: sa.Engine | None = None
_engine_url: str | None = None


def connect(url: str | None) -> None:
    """Use the database at `url` (or the local SQLite file if None), creating the tables if needed.

    Cheap to call on every Streamlit rerun — the engine is only created once per URL.
    """
    global _engine, _engine_url
    url = url or LOCAL_DATABASE_URL
    if url.startswith("postgres://"):  # some providers hand out this older spelling
        url = "postgresql://" + url[len("postgres://") :]
    if _engine is not None and url == _engine_url:
        return
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(url, pool_pre_ping=True)
    _metadata.create_all(engine)
    _engine, _engine_url = engine, url


def _db() -> sa.Engine:
    if _engine is None:
        raise RuntimeError("storage.connect() hasn't been called.")
    return _engine


def _dumps(job: Job) -> str:
    return json.dumps(job_to_dict(job))


def _upsert(conn: sa.Connection, table: sa.Table, key: dict, data: str) -> None:
    where = sa.and_(*(table.c[k] == v for k, v in key.items()))
    if conn.execute(sa.update(table).where(where).values(data=data)).rowcount == 0:
        conn.execute(sa.insert(table).values(**key, data=data))


def list_jobs() -> list[str]:
    with _db().connect() as conn:
        return list(conn.execute(sa.select(_jobs.c.name).order_by(_jobs.c.name)).scalars())


def save_job(job: Job) -> None:
    with _db().begin() as conn:
        _upsert(conn, _jobs, {"name": job.name}, _dumps(job))


def load_job(name: str) -> Job:
    with _db().connect() as conn:
        data = conn.execute(sa.select(_jobs.c.data).where(_jobs.c.name == name)).scalar_one()
    return job_from_dict(json.loads(data))


def rename_job(job: Job, new_name: str) -> None:
    """Rename a job in place, carrying its snapshots over to the new name."""
    with _db().begin() as conn:
        if conn.execute(sa.select(_jobs.c.name).where(_jobs.c.name == new_name)).first():
            raise ValueError(f"A job named '{new_name}' already exists.")
        data = json.dumps({**job_to_dict(job), "name": new_name})
        conn.execute(sa.delete(_jobs).where(_jobs.c.name == job.name))
        conn.execute(sa.insert(_jobs).values(name=new_name, data=data))
        conn.execute(sa.update(_snapshots).where(_snapshots.c.job_name == job.name).values(job_name=new_name))
    job.name = new_name  # only once the database has it


def delete_job(name: str) -> None:
    with _db().begin() as conn:
        conn.execute(sa.delete(_jobs).where(_jobs.c.name == name))
        conn.execute(sa.delete(_snapshots).where(_snapshots.c.job_name == name))


def save_snapshot(job: Job, label: str) -> None:
    with _db().begin() as conn:
        _upsert(conn, _snapshots, {"job_name": job.name, "label": label}, _dumps(job))


def list_snapshots(job_name: str) -> list[str]:
    with _db().connect() as conn:
        query = sa.select(_snapshots.c.label).where(_snapshots.c.job_name == job_name).order_by(_snapshots.c.label)
        return list(conn.execute(query).scalars())


def load_snapshot(job_name: str, label: str) -> Job:
    with _db().connect() as conn:
        query = sa.select(_snapshots.c.data).where(_snapshots.c.job_name == job_name, _snapshots.c.label == label)
        data = conn.execute(query).scalar_one()
    job = job_from_dict(json.loads(data))
    job.name = job_name  # the job may have been renamed since this snapshot was taken
    return job


def delete_snapshot(job_name: str, label: str) -> None:
    with _db().begin() as conn:
        conn.execute(sa.delete(_snapshots).where(_snapshots.c.job_name == job_name, _snapshots.c.label == label))
