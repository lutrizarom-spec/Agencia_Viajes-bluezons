"""Limitador de solicitudes persistido en SQLite."""

from __future__ import annotations  # Permite usar anotaciones de tipos actuales sin evaluarlas anticipadamente.

import hashlib  # Hashea la clave de usuario para no guardar RUT ni identificadores en claro.
import math  # Comprueba que las ventanas y marcas temporales sean finitas.
import sqlite3  # Persiste y actualiza de forma atómica los buckets de solicitudes.
import time  # Proporciona la hora actual cuando una prueba no inyecta una marca temporal.
from contextlib import closing  # Cierra las conexiones SQLite al concluir cada operación.
from pathlib import Path  # Acepta rutas de base de datos como texto o Path.


class RateLimiter:
    """Aplica un límite por identificador y ámbito usando una ventana fija."""

    def __init__(self, database_path: str | Path) -> None:
        """Guarda la base elegida y crea el esquema si todavía no existe."""
        self._database_path = str(database_path)  # Normaliza la ruta usada en cada transacción SQLite.
        self._ensure_schema()  # Prepara buckets e índice de expiración sin borrar datos existentes.

    def allow_request(
        self,
        identifier: str,
        *,
        limit: int,
        window_seconds: float,
        scope: str = "default",
        now: float | None = None,
    ) -> bool:
        """Registra un intento y devuelve False si excede el límite configurado.

        La ventana comienza con el primer intento del identificador y ámbito.
        El identificador se almacena como hash para no persistir RUT en claro.
        El límite o duración distintos reinician el bucket con la nueva política.
        """
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("El identificador no puede estar vacío.")  # Evita buckets compartidos accidentalmente por claves vacías.
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("El límite debe ser un entero mayor que cero.")  # Exige un cupo entero coherente, sin aceptar True como 1.
        if (
            isinstance(window_seconds, bool)
            or not isinstance(window_seconds, (int, float))
            or not math.isfinite(window_seconds)
            or window_seconds <= 0
        ):
            raise ValueError("La ventana debe ser un número finito mayor que cero.")  # Rechaza tiempos que no permitan una ventana operativa.
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("El ámbito no puede estar vacío.")  # Exige separar intenciones como autenticación y creación de reservas.

        current_time = time.time() if now is None else now  # Usa hora real en producción y tiempo controlado en pruebas.
        if isinstance(current_time, bool) or not isinstance(
            current_time, (int, float)
        ):
            raise ValueError("El instante debe ser numérico.")  # Impide cálculos de ventana con valores de tipos incompatibles.
        current_time = float(current_time)  # Normaliza la marca temporal a segundos decimales.
        if not math.isfinite(current_time):
            raise ValueError("El instante debe ser finito.")  # Rechaza NaN e infinito antes de persistirlos.

        scope_bytes = scope.strip().encode("utf-8")  # Codifica el alcance normalizado para construir una clave determinista.
        identifier_bytes = identifier.strip().encode("utf-8")  # Codifica la clave del cliente sin guardarla directamente.
        key_material = len(scope_bytes).to_bytes(4, "big") + scope_bytes  # Prefija la longitud para que ámbito y cliente no colisionen al concatenarse.
        bucket_key = hashlib.sha256(key_material + identifier_bytes).hexdigest()  # Usa un hash fijo como clave primaria anónima del bucket.

        with closing(sqlite3.connect(self._database_path, timeout=10)) as connection:
            with connection:  # Agrupa actualización y limpieza en una transacción SQLite.
                # UPSERT serializa cada clave dentro de SQLite, evitando que dos
                # solicitudes concurrentes superen el límite por leer el mismo contador.
                row = connection.execute(
                    """
                    INSERT INTO rate_limit_buckets (
                        bucket_key, window_started_at, window_seconds, expires_at,
                        request_count, last_request_allowed
                    )
                    VALUES (?, ?, ?, ?, 1, 1)
                    ON CONFLICT (bucket_key) DO UPDATE SET
                        window_started_at = CASE
                            WHEN excluded.window_seconds
                                 != rate_limit_buckets.window_seconds
                                 OR excluded.window_started_at
                                    >= rate_limit_buckets.expires_at
                            THEN excluded.window_started_at
                            ELSE rate_limit_buckets.window_started_at
                        END,
                        window_seconds = excluded.window_seconds,
                        expires_at = CASE
                            WHEN excluded.window_seconds
                                 != rate_limit_buckets.window_seconds
                                 OR excluded.window_started_at
                                    >= rate_limit_buckets.expires_at
                            THEN excluded.expires_at
                            ELSE rate_limit_buckets.expires_at
                        END,
                        request_count = CASE
                            WHEN excluded.window_seconds
                                 != rate_limit_buckets.window_seconds
                                 OR excluded.window_started_at
                                    >= rate_limit_buckets.expires_at
                            THEN 1
                            WHEN rate_limit_buckets.request_count < ?
                            THEN rate_limit_buckets.request_count + 1
                            ELSE rate_limit_buckets.request_count
                        END,
                        last_request_allowed = CASE
                            WHEN excluded.window_seconds
                                 != rate_limit_buckets.window_seconds
                                 OR excluded.window_started_at
                                    >= rate_limit_buckets.expires_at
                            THEN 1
                            WHEN rate_limit_buckets.request_count < ?
                            THEN 1
                            ELSE 0
                        END
                    RETURNING last_request_allowed
                    """,
                        # Los valores variables se enlazan como parámetros; no se insertan en el SQL.
                        (
                            bucket_key,
                            current_time,
                        window_seconds,
                        current_time + window_seconds,
                        limit,
                        limit,
                    ),
                ).fetchone()  # Lee el veredicto producido por la misma operación atómica.
                # Borra entradas vencidas para controlar el crecimiento de la tabla
                # y solo afecta buckets cuyo tiempo de expiración ya fue alcanzado.
                connection.execute(
                    "DELETE FROM rate_limit_buckets WHERE expires_at <= ?",
                    (current_time,),
                )
        # Una ausencia de RETURNING señala un fallo inesperado de la base, no un rechazo normal.
        if row is None:
            raise RuntimeError("SQLite no devolvió el resultado del limitador.")  # Evita tratar un resultado incompleto como una petición permitida.
        return bool(row[0])  # Convierte 0/1 de SQLite al veredicto booleano de la API del limitador.

    def _ensure_schema(self) -> None:
        """Crea la tabla de conteo y el índice que acelera la limpieza de vencidos."""
        with closing(sqlite3.connect(self._database_path, timeout=10)) as connection:
            with connection:  # Confirma tanto la tabla como el índice en una sola transacción.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                        bucket_key TEXT PRIMARY KEY,
                        window_started_at REAL NOT NULL,
                        window_seconds REAL NOT NULL CHECK (window_seconds > 0),
                        expires_at REAL NOT NULL,
                        request_count INTEGER NOT NULL CHECK (request_count > 0),
                        last_request_allowed INTEGER NOT NULL
                            CHECK (last_request_allowed IN (0, 1))
                    )
                    """
                )  # Una fila conserva el inicio, duración, vencimiento y resultado de cada bucket.
                # Las primeras versiones guardaban solo contador y momento de inicio;
                # estas columnas permiten actualizar una base local existente sin borrarla.
                existing_columns = {  # Lee nombres de columnas presentes en la tabla antes de aplicar migraciones aditivas.
                    column[1]
                    for column in connection.execute(
                            "PRAGMA table_info(rate_limit_buckets)"
                    ).fetchall()
                }  # SQLite devuelve el nombre de columna en la segunda posición del metadato.
                if "window_seconds" not in existing_columns:  # Detecta buckets creados por la implementación inicial.
                    connection.execute(  # Añade una ventana predeterminada solo para preservar filas antiguas.
                            "ALTER TABLE rate_limit_buckets ADD COLUMN window_seconds REAL NOT NULL DEFAULT 60"
                    )  # En el siguiente acceso, el UPSERT actualizará la fila con la duración solicitada.
                if "expires_at" not in existing_columns:  # Detecta ausencia de fecha de expiración en el esquema previo.
                    connection.execute(  # Añade una expiración inicial que provoca reinicio seguro del bucket legado.
                            "ALTER TABLE rate_limit_buckets ADD COLUMN expires_at REAL NOT NULL DEFAULT 0"
                    )  # El primer intento posterior a la actualización reinicia el contador sin bloquear tráfico legítimo.
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rate_limit_buckets_expires_at
                    ON rate_limit_buckets (expires_at)
                    """
                )  # El índice hace eficiente la eliminación periódica de buckets vencidos.
