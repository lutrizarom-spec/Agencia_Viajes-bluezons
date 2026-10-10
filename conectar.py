
import os
import sqlite3
from pathlib import Path


def crear_conexion(ruta=None, timeout=10):
    """Abre una conexión SQLite con ruta y claves foráneas configuradas."""
    ruta_bd = Path(
        ruta
        or os.environ.get("AGENCIA_DB_PATH")
        or Path(__file__).resolve().with_name("agencia.db")
    ).expanduser().resolve()

    ruta_bd.parent.mkdir(parents=True, exist_ok=True)

    conexion = sqlite3.connect(str(ruta_bd), timeout=timeout)
    conexion.execute("PRAGMA foreign_keys = ON")
    return conexion
