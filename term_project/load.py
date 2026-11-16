"""Load the cleaned LEGO tables into PostgreSQL.

Usage from the ETL script:
    from load import load_data
    counts = load_data(transform_data(sets, themes, minifigures))

Connection comes from DATABASE_URL or POSTGRES_* environment variables.
"""

import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKeyConstraint, Integer, MetaData,
    String, Table, UniqueConstraint, create_engine, select, func,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL

load_dotenv()

metadata = MetaData()

theme_groups = Table(
    "theme_groups", metadata,
    Column("theme_name", String(255), primary_key=True),
    Column("parent_theme", String(255), primary_key=True),
)

themes = Table(
    "themes", metadata,
    Column("id", BigInteger, primary_key=True),
    Column("theme_name", String(255), nullable=False),
    Column("parent_theme", String(255), nullable=False),
    Column("total_sets", BigInteger),
    ForeignKeyConstraint(
        ["theme_name", "parent_theme"],
        ["theme_groups.theme_name", "theme_groups.parent_theme"],
    ),
)

sets = Table(
    "sets", metadata,
    Column("set_num", String(100), primary_key=True),
    Column("name", String(1000)),
    Column("year", Integer),
    Column("theme_name", String(255), nullable=False),
    Column("parent_theme", String(255), nullable=False),
    Column("num_parts", BigInteger),
    Column("num_minifigs", BigInteger),
    Column("num_unique_colors", BigInteger),
    Column("has_minifigs", Boolean),
    Column("decade", Integer),
    ForeignKeyConstraint(
        ["theme_name", "parent_theme"],
        ["theme_groups.theme_name", "theme_groups.parent_theme"],
    ),
)

minifigures = Table(
    "minifigures", metadata,
    Column("fig_num", String(100), primary_key=True),
    Column("name", String(1000)),
    Column("num_parts", BigInteger),
    Column("total_sets_appeared", BigInteger),
)

TABLES = {
    "theme_groups": theme_groups,
    "themes": themes,
    "sets": sets,
    "minifigures": minifigures,
}


def get_engine():
    """Create the database connection without hardcoding credentials."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return create_engine(database_url, pool_pre_ping=True)
    return create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")),
            password=os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD")),
            host=os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")),
            port=int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", "5432"))),
            database=os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "lego")),
        ),
        pool_pre_ping=True,
    )


def _records(frame, table):
    """Validate expected columns and convert pandas missing values to SQL NULL."""
    columns = [col.name for col in table.columns]
    required = [col.name for col in table.primary_key.columns]
    required += [col.name for col in table.columns if not col.nullable and col.name not in required]
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"{table.name}: missing required columns: {missing}")
    frame = frame[[col for col in columns if col in frame.columns]].copy()
    frame = frame.astype(object).where(pd.notna(frame), None)
    return frame.to_dict("records")


def _upsert(conn, table, records, batch_size=1000):
    """Insert new records and update changed records; safe to run twice."""
    inserted = updated = 0
    pk_names = [col.name for col in table.primary_key.columns]
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        # Determine existing keys before the upsert for an informative log.
        keys = [tuple(row[k] for k in pk_names) for row in batch]
        if len(pk_names) == 1:
            existing = set(conn.execute(
                select(table.c[pk_names[0]]).where(table.c[pk_names[0]].in_([k[0] for k in keys]))
            ).scalars())
            n_updated = sum(key[0] in existing for key in keys)
        else:
            from sqlalchemy import tuple_
            existing = set(tuple(r) for r in conn.execute(
                select(*(table.c[k] for k in pk_names)).where(
                    tuple_(*(table.c[k] for k in pk_names)).in_(keys)
                )
            ))
            n_updated = sum(key in existing for key in keys)
        stmt = pg_insert(table).values(batch)
        update_columns = {
            col: getattr(stmt.excluded, col)
            for col in batch[0] if col not in pk_names
        }
        if update_columns:
            stmt = stmt.on_conflict_do_update(index_elements=pk_names, set_=update_columns)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_names)
        conn.execute(stmt)
        updated += n_updated
        inserted += len(batch) - n_updated
    return {"inserted": inserted, "updated": updated}


def load_data(cleaned_tables, engine=None):
    """Create tables and load transform_data() output in foreign-key order.

    Returns per-table insert/update counts. All inserts use one transaction.
    """
    missing = set(TABLES) - set(cleaned_tables)
    if missing:
        raise ValueError(f"Missing cleaned tables: {sorted(missing)}")
    engine = engine or get_engine()
    counts = {}
    with engine.begin() as conn:
        metadata.create_all(conn)
        for name, table in TABLES.items():
            records = _records(cleaned_tables[name], table)
            pk = [col.name for col in table.primary_key.columns]
            if any(any(row[k] is None for k in pk) for row in records):
                raise ValueError(f"{name}: null primary key")
            if len({tuple(row[k] for k in pk) for row in records}) != len(records):
                raise ValueError(f"{name}: duplicate primary keys in cleaned data")
            counts[name] = _upsert(conn, table, records)
            print(f"{name}: {counts[name]['inserted']} inserted, {counts[name]['updated']} updated")
    return counts
