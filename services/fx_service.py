"""Consulta y cachea el tipo de cambio USD a CLP."""

from __future__ import annotations  # Permite expresar tipos diferidos y anotaciones modernas.

import json  # Convierte el cuerpo JSON de la respuesta HTTP de mindicador.cl a objetos Python.
import math  # Permite rechazar valores NaN e infinitos que no son tasas válidas.
import sqlite3  # Proporciona la persistencia local de la tasa vigente y su respaldo.
import threading  # Serializa llamadas en una instancia y evita probes simultáneos al proveedor.
import time  # Proporciona el reloj monotónico predeterminado del circuit breaker.
from collections.abc import Callable  # Describe el tipo de proveedor inyectable que devuelve una tasa.
from contextlib import closing  # Cierra explícitamente conexiones SQLite, incluso si ocurre una excepción.
from pathlib import Path  # Acepta rutas de base de datos como texto o como objetos Path.
from urllib.request import urlopen  # Consulta HTTPS sin requerir dependencias HTTP adicionales.


def mindicador_usd_clp_provider() -> float:
    """Consulta el dólar observado en mindicador.cl y retorna su valor numérico.

    Esta función puede inyectarse como provider de FxService. Si la respuesta
    HTTP o su JSON son inválidos, propaga el error para que FxService active la
    caché y el circuit breaker. El timeout evita dejar una solicitud bloqueada.
    """
    request_url = "https://mindicador.cl/api/dolar"  # Pide solamente el indicador dólar a la API pública.
    with urlopen(request_url, timeout=5) as response:  # Abre HTTPS con timeout; errores HTTP/red se propagan al servicio.
        payload = json.loads(response.read().decode("utf-8"))  # Lee el cuerpo, lo decodifica y analiza el JSON.

    series = payload.get("serie") if isinstance(payload, dict) else None  # Extrae la serie solo si la raíz JSON es un objeto.
    if not isinstance(series, list) or not series:  # Comprueba que la API haya devuelto al menos una observación.
        raise ValueError("mindicador.cl no devolvió observaciones para el dólar.")  # Informa una respuesta válida sintácticamente pero inutilizable.

    latest = series[0]  # La API entrega primero el registro más reciente del indicador.
    if not isinstance(latest, dict) or "valor" not in latest:  # Verifica la forma esperada antes de leer la tasa.
        raise ValueError("La última observación de mindicador.cl no contiene valor.")  # Evita errores opacos por estructura inesperada.
    return float(latest["valor"])  # Convierte la observación al tipo numérico que FxService valida y cachea.


class FxServiceError(RuntimeError):
    """Indica que no fue posible obtener el tipo de cambio ni desde la caché."""


class FxService:
    """Obtiene USD/CLP con caché SQLite y circuito por instancia."""

    def __init__(
        self,
        database_path: str | Path,
        provider: Callable[[], float],
        *,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configura proveedor, persistencia y parámetros del circuito.

        Args:
            database_path: Archivo SQLite donde se guarda el último valor válido.
            provider: Función externa inyectada que devuelve la tasa USD/CLP.
            failure_threshold: Fallos consecutivos que abren el circuito.
            reset_timeout_seconds: Espera antes de permitir una nueva prueba.
            clock: Reloj monotónico sustituible en pruebas deterministas.
        """
        if (
            isinstance(failure_threshold, bool)
            or not isinstance(failure_threshold, int)
            or failure_threshold < 1
        ):
            raise ValueError("El umbral de fallas debe ser un entero mayor que cero.")  # Rechaza parámetros que harían ambiguo el estado del circuito.
        if (
            isinstance(reset_timeout_seconds, bool)
            or not isinstance(reset_timeout_seconds, (int, float))
            or not math.isfinite(reset_timeout_seconds)
            or reset_timeout_seconds <= 0
        ):
            raise ValueError("El tiempo de recuperación debe ser finito y positivo.")  # Asegura un intervalo de recuperación utilizable.
        self._database_path = str(database_path)  # Normaliza la ruta para usarla consistentemente al abrir SQLite.
        self._provider = provider  # Conserva la dependencia externa sin fijar una API dentro de la lógica del servicio.
        self._failure_threshold = failure_threshold  # Guarda el número de errores necesarios para abrir el circuito.
        self._reset_timeout_seconds = float(reset_timeout_seconds)  # Normaliza el intervalo a decimal para comparaciones de tiempo.
        self._clock = clock  # Conserva el reloj inyectado para poder probar el tiempo sin esperas reales.
        self._failure_count = 0  # Inicia el contador de fallos consecutivos del proveedor.
        self._open_until = 0.0  # Marca que el circuito comienza cerrado hasta que acumule fallos.
        self._lock = threading.Lock()  # Evita que varios hilos ejecuten a la vez la prueba de recuperación.
        self._ensure_schema()  # Crea la tabla de caché si la base aún no tiene el esquema requerido.

    def get_usd_clp_rate(self) -> float:
        """Devuelve la tasa vigente o la última tasa guardada si falla el proveedor."""
        with self._lock:  # Protege el conteo y la transición entre circuito abierto y cerrado.
            current_time = self._clock()  # Captura una sola lectura temporal para esta consulta.
            if current_time < self._open_until:  # Detecta el período de enfriamiento después del umbral de fallos.
                cached_rate = self._read_cached_rate()  # Evita llamar al proveedor mientras el circuito está abierto.
                if cached_rate is None:
                    raise FxServiceError(
                        "El circuito USD/CLP está abierto y no hay una tasa en caché."
                    )  # Expone el fallo de dominio si tampoco existe el respaldo local.
                return cached_rate  # Mantiene el sistema disponible con la última tasa conocida.

            try:
                rate = self._provider()  # Ejecuta la dependencia externa; puede ser mindicador.cl u otro proveedor.
                if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                    raise ValueError("El proveedor debe retornar una tasa numérica.")  # bool se excluye aunque sea subtipo de int en Python.
                rate = float(rate)  # Normaliza int/float al tipo de retorno uniforme del servicio.
                if not math.isfinite(rate) or rate <= 0:
                    raise ValueError(
                        "La tasa debe ser un número finito mayor que cero."
                    )  # Evita cachear NaN, infinito, cero o una cotización negativa.
            except Exception as provider_error:
                self._failure_count += 1  # Cuenta la falla de proveedor o una respuesta inválida.
                if self._failure_count >= self._failure_threshold:
                    self._open_until = current_time + self._reset_timeout_seconds  # Abre el circuito hasta el momento de prueba.
                cached_rate = self._read_cached_rate()  # Busca la última tasa válida ante error externo.
                if cached_rate is None:
                    raise FxServiceError(
                        "No se pudo obtener la tasa USD/CLP y no hay una tasa en caché."
                    ) from provider_error  # Conserva la causa técnica para diagnóstico sin filtrar excepciones crudas al consumidor.
                return cached_rate  # Resuelve el error transitorio con el valor conocido previamente.

            self._failure_count = 0  # Un resultado válido rompe la secuencia de fallos consecutivos.
            self._open_until = 0.0  # Cierra el circuito después de una consulta o probe exitosa.
            with closing(sqlite3.connect(self._database_path, timeout=5)) as connection:  # Abre una conexión corta y con espera acotada.
                with connection:  # Usa el context manager para confirmar cambios o revertirlos si ocurre un error.
                    connection.execute(
                        """
                        INSERT INTO fx_cache (base_currency, target_currency, rate)
                        VALUES ('USD', 'CLP', ?)
                        ON CONFLICT (base_currency, target_currency)
                        DO UPDATE SET rate = excluded.rate,
                                      updated_at = CURRENT_TIMESTAMP
                        """,
                        (rate,),  # Parametriza la tasa en vez de concatenar datos dentro de SQL.
                    )  # Inserta la primera cotización o actualiza la existente para USD/CLP.
            return rate  # Retorna la respuesta nueva después de persistirla como último valor válido.

    def _ensure_schema(self) -> None:
        """Prepara la tabla de caché sin borrar datos previamente almacenados."""
        with closing(sqlite3.connect(self._database_path, timeout=5)) as connection:  # Abre la base y garantiza el cierre del descriptor.
            with connection:  # Hace que la creación del esquema quede confirmada.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fx_cache (
                        base_currency TEXT NOT NULL,
                        target_currency TEXT NOT NULL,
                        rate REAL NOT NULL CHECK (rate > 0),
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (base_currency, target_currency)
                    )
                    """
                )  # Identifica una cotización por moneda base/destino y valida tasas positivas.

    def _read_cached_rate(self) -> float | None:
        """Lee la última tasa USD/CLP; None indica que todavía no hay respaldo."""
        with closing(sqlite3.connect(self._database_path, timeout=5)) as connection:  # Abre una conexión solo para la lectura de respaldo.
            row = connection.execute(
                """
                SELECT rate
                FROM fx_cache
                WHERE base_currency = 'USD' AND target_currency = 'CLP'
                """  # Limita la consulta al único par de monedas que maneja este servicio.
            ).fetchone()  # Obtiene cero o un registro gracias a la clave primaria compuesta.
        if row is None:
            return None  # Señala explícitamente que no hay valor guardado que permita el fallback.

        rate = float(row[0])  # Convierte el valor SQLite al tipo documentado por el servicio.
        if not math.isfinite(rate) or rate <= 0:
            raise FxServiceError("La tasa USD/CLP guardada en caché no es válida.")  # No permite que una corrupción de datos parezca una tasa válida.
        return rate  # Devuelve el último valor persistido y validado.
