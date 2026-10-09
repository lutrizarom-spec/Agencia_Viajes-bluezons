"""Create a consistent SQLite backup without overwriting an existing file."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def backup_database(source: str | Path, destination: str | Path) -> Path:
    """Back up a live SQLite database; fail rather than replace an existing file."""
    source_path = Path(source).resolve(strict=True)
    destination_path = Path(destination).resolve()
    if not source_path.is_file():
        raise ValueError("La ruta de origen debe ser un archivo SQLite.")
    if source_path == destination_path:
        raise ValueError("El origen y destino de la copia deben ser distintos.")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination_path.open("xb"):
            pass
    except FileExistsError as error:
        raise FileExistsError(
            f"El destino ya existe y no se sobrescribirá: {destination_path}"
        ) from error

    try:
        source_uri = f"{source_path.as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
            with closing(sqlite3.connect(destination_path)) as destination_connection:
                source_connection.backup(destination_connection)
                integrity = destination_connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()
                if integrity != ("ok",):
                    raise sqlite3.DatabaseError(
                        f"La copia no pasó integrity_check: {integrity!r}"
                    )
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise

    return destination_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Base SQLite activa")
    parser.add_argument("destination", type=Path, help="Ruta nueva para la copia")
    args = parser.parse_args()
    backup_path = backup_database(args.source, args.destination)
    print(f"Copia verificada: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
