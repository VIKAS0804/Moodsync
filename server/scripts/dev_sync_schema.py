#!/usr/bin/env python3
"""Add columns that exist on the models but not yet in the database.

`Base.metadata.create_all()` only ever creates missing *tables*. Add a column to
a model and an existing database silently keeps the old shape, so the next query
fails with "column does not exist" -- which reads like a code bug rather than a
schema one.

This closes that gap for the common case: additive, nullable-or-defaulted
columns. It is **not** a migration tool. It never drops, renames, retypes or
reorders anything, and it has no notion of history or rollback. Alembic is the
real answer once the schema stops moving; this exists so local databases don't
have to be dropped and reseeded after every model change.

    python scripts/dev_sync_schema.py            # show what's missing
    python scripts/dev_sync_schema.py --apply    # add it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.models import User  # noqa: F401, E402  (registers every mapper)


def _render_default(column) -> str | None:
    """A literal SQL default for a scalar Python default, else None."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def plan() -> list[tuple[str, str, str]]:
    """Return (table, column, DDL fragment) for every column missing in the DB."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing: list[tuple[str, str, str]] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            # create_all handles whole new tables; nothing to patch.
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue

            type_sql = column.type.compile(dialect=engine.dialect)
            fragment = f"{column.name} {type_sql}"
            default_sql = _render_default(column)
            if default_sql is not None:
                fragment += f" DEFAULT {default_sql}"
            if not column.nullable:
                if default_sql is None:
                    # Can't add NOT NULL without a value for existing rows.
                    print(
                        f"  ! {table.name}.{column.name} is NOT NULL with no scalar "
                        "default; adding it as nullable. Backfill, then tighten by hand.",
                        file=sys.stderr,
                    )
                else:
                    fragment += " NOT NULL"
            missing.append((table.name, column.name, fragment))
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Execute the ALTER statements")
    args = parser.parse_args()

    try:
        pending = plan()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not inspect the database: {exc}", file=sys.stderr)
        return 2

    if not pending:
        print("Schema is up to date with the models.")
        return 0

    print(f"{len(pending)} column(s) missing from the database:\n")
    for table, _column, fragment in pending:
        print(f"  ALTER TABLE {table} ADD COLUMN {fragment};")

    if not args.apply:
        print("\nRe-run with --apply to add them.")
        return 0

    with engine.begin() as conn:
        for table, column, fragment in pending:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {fragment}"))
            print(f"  added {table}.{column}")

    print(f"\nAdded {len(pending)} column(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
