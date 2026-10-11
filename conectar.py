
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


def is_database_locked(error: sqlite3.OperationalError) -> bool:
    """Reconoce solo errores SQLite BUSY/LOCKED, nunca sintaxis u otros fallos."""
    error_code = getattr(error, "sqlite_errorcode", None)  # Obtiene el código extendido disponible en las versiones actuales de sqlite3.
    if error_code is not None:  # Prefiere el código estable cuando Python lo proporciona.
        primary_code = error_code & 0xFF  # SQLite guarda el código primario en los ocho bits inferiores.
        return primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)  # Solo bloqueos se consideran transitorios.
    message = str(error).casefold()  # Compatibilidad con runtimes antiguos que no exponen sqlite_errorcode.
    return "database is locked" in message or "database table is locked" in message  # Usa mensajes exactos conocidos como fallback limitado.
