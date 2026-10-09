from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.backup_database import backup_database


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name)
        self.source = self.root / "active.db"
        with closing(sqlite3.connect(self.source)) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE reservations (id TEXT PRIMARY KEY, amount INTEGER)"
                )
                connection.execute(
                    "INSERT INTO reservations VALUES ('reservation-1', 1299)"
                )

    def test_backup_is_consistent_and_can_restore_the_data(self) -> None:
        destination = self.root / "backups" / "snapshot.db"
        result = backup_database(self.source, destination)

        self.assertEqual(result, destination.resolve())
        with closing(sqlite3.connect(destination)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone(), ("ok",)
            )
            self.assertEqual(
                connection.execute(
                    "SELECT id, amount FROM reservations"
                ).fetchone(),
                ("reservation-1", 1299),
            )

    def test_backup_never_overwrites_an_existing_destination(self) -> None:
        destination = self.root / "snapshot.db"
        destination.write_bytes(b"keep existing backup")

        with self.assertRaisesRegex(FileExistsError, "no se sobrescribirá"):
            backup_database(self.source, destination)

        self.assertEqual(destination.read_bytes(), b"keep existing backup")

    def test_source_and_destination_must_be_distinct(self) -> None:
        with self.assertRaisesRegex(ValueError, "deben ser distintos"):
            backup_database(self.source, self.source)


if __name__ == "__main__":
    unittest.main()
