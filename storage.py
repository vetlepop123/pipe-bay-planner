"""Database persistence for jobs and named snapshots.

Jobs are stored as JSON in a SQL database. Locally that's a SQLite file in data/; on a
hosted deployment (whose own disk is wiped on every restart) point it at a hosted Postgres
database instead by setting the `database_url` secret — see pipebay._database_url.
"""

from __future__ import annotations

import json
import time
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
    # Providers hand out plain postgres:// or postgresql:// URLs; pin the driver to psycopg (v3,
    # see requirements.txt) rather than whatever the installed SQLAlchemy defaults to.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix) :]
    if _engine is not None and url == _engine_url:
        return
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(url, pool_pre_ping=True)
    _metadata.create_all(engine)
    _engine, _engine_url = engine, url
    _forget_lists()


def _db() -> sa.Engine:
    if _engine is None:
        raise RuntimeError("storage.connect() hasn't been called.")
    return _engine


# The sidebar asks for the job and snapshot lists on every Streamlit rerun (every click), and
# a hosted database is a network round trip away, so the lists are kept in memory. Writes made
# through this module update or drop them right away; writes from anywhere else (another copy
# of the app on the same database) show up once the lists are older than LIST_CACHE_SECONDS.
LIST_CACHE_SECONDS = 60
_list_cache: dict[str, tuple[float, list[str]]] = {}  # "jobs" or "snapshots:<job>" -> (fetched at, names)


def _cached_list(key: str, query: sa.Select) -> list[str]:
    hit = _list_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < LIST_CACHE_SECONDS:
        return list(hit[1])
    with _db().connect() as conn:
        names = list(conn.execute(query).scalars())
    _list_cache[key] = (time.monotonic(), names)
    return list(names)


def _forget_lists() -> None:
    _list_cache.clear()


def _dumps(job: Job) -> str:
    return json.dumps(job_to_dict(job))


def _upsert(conn: sa.Connection, table: sa.Table, key: dict, data: str) -> None:
    where = sa.and_(*(table.c[k] == v for k, v in key.items()))
    if conn.execute(sa.update(table).where(where).values(data=data)).rowcount == 0:
        conn.execute(sa.insert(table).values(**key, data=data))


def list_jobs() -> list[str]:
    return _cached_list("jobs", sa.select(_jobs.c.name).order_by(_jobs.c.name))


# Several people can have the same job open. To stop one of them silently overwriting the
# other's work, every load/save hands back a version token (the exact stored JSON), and a
# save only goes through if the database still holds the version that session last saw.
class JobConflict(Exception):
    """The job was changed, renamed or deleted by someone else since this session last loaded or saved it."""


def _conflict(conn: sa.Connection, name: str) -> JobConflict:
    if conn.execute(sa.select(_jobs.c.name).where(_jobs.c.name == name)).first() is None:
        return JobConflict(f"'{name}' has been renamed or deleted by someone else.")
    return JobConflict(f"Someone else has saved changes to '{name}' since you opened it.")


def create_job(job: Job) -> str:
    """Save a brand-new job; returns its version token. Refuses to replace an existing job."""
    data = _dumps(job)
    with _db().begin() as conn:
        if conn.execute(sa.select(_jobs.c.name).where(_jobs.c.name == job.name)).first():
            raise ValueError(f"A job named '{job.name}' already exists.")
        conn.execute(sa.insert(_jobs).values(name=job.name, data=data))
    _forget_lists()
    return data


def save_job(job: Job, expected: str | None) -> str:
    """Save `job` over the version `expected` (a token from load/create/save); returns the new token.

    Raises JobConflict if the stored job is no longer `expected`. With expected=None the job
    is written regardless (restoring a backup, or deliberately overwriting someone's changes).
    """
    data = _dumps(job)
    with _db().begin() as conn:
        update = sa.update(_jobs).where(_jobs.c.name == job.name)
        if expected is not None:
            update = update.where(_jobs.c.data == expected)
        if conn.execute(update.values(data=data)).rowcount == 0:
            if expected is not None:
                raise _conflict(conn, job.name)
            conn.execute(sa.insert(_jobs).values(name=job.name, data=data))
            _forget_lists()
    return data


def load_job(name: str) -> tuple[Job, str]:
    """The job and its version token."""
    with _db().connect() as conn:
        data = conn.execute(sa.select(_jobs.c.data).where(_jobs.c.name == name)).scalar_one()
    return job_from_dict(json.loads(data)), data


def rename_job(job: Job, new_name: str, expected: str) -> str:
    """Rename a job in place, carrying its snapshots over to the new name; returns the new token."""
    data = json.dumps({**job_to_dict(job), "name": new_name})
    with _db().begin() as conn:
        if conn.execute(sa.select(_jobs.c.name).where(_jobs.c.name == new_name)).first():
            raise ValueError(f"A job named '{new_name}' already exists.")
        if conn.execute(sa.delete(_jobs).where(_jobs.c.name == job.name, _jobs.c.data == expected)).rowcount == 0:
            raise _conflict(conn, job.name)
        conn.execute(sa.insert(_jobs).values(name=new_name, data=data))
        conn.execute(sa.update(_snapshots).where(_snapshots.c.job_name == job.name).values(job_name=new_name))
    _forget_lists()
    job.name = new_name  # only once the database has it
    return data


def delete_job(name: str) -> None:
    with _db().begin() as conn:
        conn.execute(sa.delete(_jobs).where(_jobs.c.name == name))
        conn.execute(sa.delete(_snapshots).where(_snapshots.c.job_name == name))
    _forget_lists()


def save_snapshot(job: Job, label: str) -> None:
    with _db().begin() as conn:
        _upsert(conn, _snapshots, {"job_name": job.name, "label": label}, _dumps(job))
    _forget_lists()


def list_snapshots(job_name: str) -> list[str]:
    query = sa.select(_snapshots.c.label).where(_snapshots.c.job_name == job_name).order_by(_snapshots.c.label)
    return _cached_list(f"snapshots:{job_name}", query)


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
    _forget_lists()
