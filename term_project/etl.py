"""Run the pipeline once: extract from Kaggle, transform, load into PostgreSQL.

Incremental behavior: every row is compared with what is already in the
database by its primary key. New keys are inserted, changed rows are updated,
and unchanged rows are skipped. Each run is recorded in the etl_runs table
(time and Kaggle dataset version), and the next run reports what changed
since then.
"""
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import Column, DateTime, Integer, MetaData, Table, inspect, insert, select
from sqlalchemy.exc import OperationalError

from extract import ExtractError, extract
from load import TABLES, get_engine, load_data
from transform import print_quality_summary, transform_data

run_log = Table(
    "etl_runs", MetaData(),
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("run_at", DateTime(timezone=True), nullable=False),
    Column("dataset_version", Integer),
    Column("rows_received", Integer, nullable=False),
    Column("rows_written", Integer, nullable=False),
)


def wait_for_db(engine, attempts=30, delay=2):
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect():
                return
        except OperationalError:
            print(f"waiting for PostgreSQL ({attempt}/{attempts})")
            time.sleep(delay)
    raise SystemExit("PostgreSQL never became reachable. Check the POSTGRES_* values in .env.")


def last_run(engine):
    if not inspect(engine).has_table(run_log.name):
        return None
    with engine.connect() as conn:
        return conn.execute(
            select(run_log).order_by(run_log.c.run_id.desc()).limit(1)
        ).mappings().first()


def _norm(value):
    # Make DB values and DataFrame values comparable (int vs float vs Int64).
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    return str(value)


def _rows_by_key(frame, columns, key):
    frame = frame[columns].astype(object).where(pd.notna(frame[columns]), None)
    return {
        tuple(_norm(row[k]) for k in key): tuple(_norm(row[c]) for c in columns)
        for row in frame.to_dict("records")
    }


def new_or_changed(engine, table, frame):
    """Rows of frame whose key is new or whose values differ from the database."""
    columns = [c.name for c in table.columns if c.name in frame.columns]
    key = [c.name for c in table.primary_key.columns]
    if not inspect(engine).has_table(table.name):
        return frame, len(frame), 0, 0

    existing = pd.read_sql(select(*(table.c[c] for c in columns)), engine)
    stored = _rows_by_key(existing, columns, key) if len(existing) else {}

    keep, new, changed = [], 0, 0
    values = frame[columns].astype(object).where(pd.notna(frame[columns]), None)
    for index, row in zip(frame.index, values.to_dict("records")):
        row_key = tuple(_norm(row[k]) for k in key)
        row_values = tuple(_norm(row[c]) for c in columns)
        if row_key not in stored:
            new += 1
            keep.append(index)
        elif stored[row_key] != row_values:
            changed += 1
            keep.append(index)
    return frame.loc[keep], new, changed, len(frame) - len(keep)


def main():
    engine = get_engine()
    wait_for_db(engine)
    previous = last_run(engine)
    started = datetime.now(timezone.utc)

    print(f"\nETL run {started:%Y-%m-%d %H:%M} UTC")
    try:
        raw, version = extract()
    except ExtractError as exc:
        print(f"  extract failed: {exc}")
        return 1

    received = sum(len(df) for df in raw.values())
    if previous is None:
        print(f"  source: Kaggle dataset version {version}, full snapshot (first load)")
    else:
        print(
            f"  source: Kaggle dataset version {version}, full snapshot; "
            f"comparing with successful run "
            f"{previous['run_at'].astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC "
            f"(version {previous['dataset_version']})"
        )
    print(
        f"  received from full snapshot: {received:,} rows ("
        + ", ".join(f"{name} {len(df):,}" for name, df in raw.items()) + ")"
    )

    try:
        cleaned = transform_data(raw["sets"], raw["themes"], raw["minifigures"])
    except ValueError as exc:
        print(f"  transform stopped the run: {exc}")
        return 1
    print_quality_summary(raw, cleaned)

    to_load, skipped = {}, {}
    for name, table in TABLES.items():
        subset, new, changed, unchanged = new_or_changed(engine, table, cleaned[name])
        to_load[name], skipped[name] = subset, unchanged

    print("\nload")
    counts = load_data(to_load, engine)
    written = sum(c["inserted"] + c["updated"] for c in counts.values())

    with engine.begin() as conn:
        run_log.create(conn, checkfirst=True)
        conn.execute(insert(run_log).values(
            run_at=started, dataset_version=version,
            rows_received=received, rows_written=written,
        ))

    print("\nsummary")
    for name, c in counts.items():
        print(
            f"  {name}: {c['inserted']:,} inserted, {c['updated']:,} updated, "
            f"{skipped[name]:,} unchanged (skipped)"
        )
    print(f"  run recorded in etl_runs; {written:,} rows written\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
