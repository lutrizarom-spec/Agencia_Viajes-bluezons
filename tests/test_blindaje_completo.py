import concurrent.futures  # Permite simular muchas solicitudes simultáneas al mismo bucket.
import io  # Construye una respuesta HTTP en memoria para probar el proveedor sin red.
import sqlite3  # Prepara esquemas legados y comprueba la evolución compatible del bucket.
import tempfile  # Aísla cada prueba en una base SQLite temporal que se elimina al final.
import unittest  # Proporciona las clases de prueba y las aserciones del proyecto.
from contextlib import closing  # Cierra explícitamente bases temporales antes de eliminarlas en Windows.
from pathlib import Path  # Construye rutas de base portables en todos los entornos.
from unittest.mock import patch  # Sustituye urlopen por una respuesta controlada durante el test.

from services.fx_service import (  # Importa las piezas del servicio FX que se verifican.
    FxService,  # Servicio que valida y guarda tasas recibidas del proveedor.
    FxServiceError,  # Excepción de dominio al fallar tanto proveedor como caché.
    mindicador_usd_clp_provider,  # Adaptador HTTP de ejemplo para el indicador público.
)
from services.rate_limiter import RateLimiter  # Importa el limitador persistente que verifican las pruebas.


class FxServiceTests(unittest.TestCase):
    """Comprueba el fallback local, la excepción de dominio y el circuito FX."""

    def test_mindicador_provider_parses_latest_usd_clp_value(self) -> None:
        """El adaptador lee el valor de la respuesta JSON sin depender de internet."""
        response_body = io.BytesIO(  # Simula un cuerpo HTTP legible por urlopen.
            b'{"serie":[{"fecha":"2026-10-05","valor":987.65}]}'  # Imita la estructura documentada de mindicador.cl.
        )  # El objeto también implementa el context manager de la respuesta HTTP.
        with patch("services.fx_service.urlopen", return_value=response_body) as mocked_urlopen:  # Aísla el test de la disponibilidad de la API pública.
            rate = mindicador_usd_clp_provider()  # Ejecuta el adaptador real contra la respuesta simulada.

        self.assertEqual(rate, 987.65)  # Verifica que el adaptador retorna el valor observado como float.
        mocked_urlopen.assert_called_once_with(  # Confirma el endpoint concreto y que se configuró timeout.
            "https://mindicador.cl/api/dolar", timeout=5
        )  # Un timeout finito limita cuánto puede bloquear la llamada en producción.

    def test_provider_failure_uses_last_cached_rate(self) -> None:
        """Una cotización previa debe seguir disponible cuando falla el proveedor."""
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "agency.db"  # Usa un archivo temporal para aislar la caché del resto de pruebas.
            service = FxService(database_path, provider=lambda: 950.5)  # Inyecta un proveedor local determinista, sin red.
            self.assertEqual(service.get_usd_clp_rate(), 950.5)  # La primera respuesta válida debe guardarse y devolverse.

            def failed_provider() -> float:
                """Simula una indisponibilidad externa para ejercitar el fallback."""
                raise OSError("Proveedor no disponible")  # Representa timeout, desconexión o fallo HTTP del proveedor real.

            fallback_service = FxService(database_path, provider=failed_provider)  # Reutiliza la base que contiene la tasa previamente persistida.
            self.assertEqual(fallback_service.get_usd_clp_rate(), 950.5)  # Verifica que el servicio responda con caché y no propague el error externo.

    def test_provider_failure_without_cache_raises_domain_error(self) -> None:
        """Sin proveedor disponible ni valor previo, se informa un fallo controlado."""
        with tempfile.TemporaryDirectory() as directory:
            service = FxService(
                Path(directory) / "agency.db",
                provider=lambda: (_ for _ in ()).throw(OSError("offline")),  # Hace fallar el proveedor sin necesitar una función auxiliar.
            )  # La base temporal inicia vacía, por lo que tampoco hay fallback.
            with self.assertRaises(FxServiceError):  # Exige la excepción de dominio documentada y evita errores sin manejar.
                service.get_usd_clp_rate()  # Ejecuta el camino en el que fallan proveedor y caché.

    def test_circuit_opens_and_allows_a_probe_after_timeout(self) -> None:
        """El circuito debe pausar consultas repetidas y probar tras el timeout."""
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "agency.db"  # Aísla el almacenamiento del valor que servirá como fallback.
            current_time = [0.0]  # Usa un reloj mutable para avanzar sin dormir durante la prueba.
            provider_calls = [0]  # Cuenta invocaciones y demuestra que el circuito evita llamadas mientras está abierto.

            def provider() -> float:
                """Devuelve una tasa, falla dos veces y luego simula recuperación."""
                provider_calls[0] += 1  # Actualiza el conteo mutable capturado por el proveedor simulado.
                if provider_calls[0] == 1:  # La primera consulta representa el estado saludable inicial.
                    return 950.0  # Proporciona una tasa para llenar la caché de respaldo.
                if provider_calls[0] in (2, 3):  # Las dos llamadas siguientes alcanzan el umbral configurado.
                    raise OSError("Proveedor no disponible")  # Simula el error que activa el circuit breaker.
                return 960.0  # La llamada posterior al timeout simula que el proveedor se recuperó.

            service = FxService(
                database_path,  # Comparte la base temporal donde se persistirá el valor de fallback.
                provider,  # Inyecta el escenario de fallas y recuperación, sin solicitar a mindicador.cl.
                failure_threshold=2,  # Abre el circuito después de dos fallos consecutivos.
                reset_timeout_seconds=10,  # Mantiene abierto el circuito durante diez segundos simulados.
                clock=lambda: current_time[0],  # Inyecta el reloj controlable de esta prueba.
            )  # Construye el servicio con opciones pequeñas y repetibles para el test.
            self.assertEqual(service.get_usd_clp_rate(), 950.0)  # Guarda y retorna la tasa inicial saludable.
            self.assertEqual(service.get_usd_clp_rate(), 950.0)  # El primer fallo usa caché y todavía no abre circuito.
            self.assertEqual(service.get_usd_clp_rate(), 950.0)  # El segundo fallo alcanza el umbral y también usa caché.
            self.assertEqual(provider_calls[0], 3)  # Confirma que se hicieron exactamente esas tres llamadas.

            self.assertEqual(service.get_usd_clp_rate(), 950.0)  # Con circuito abierto, usa la caché sin llamar al proveedor.
            self.assertEqual(provider_calls[0], 3)  # Confirma que la llamada previa quedó bloqueada por el circuito.
            current_time[0] = 10  # Avanza exactamente hasta el instante en que se permite el probe.
            self.assertEqual(service.get_usd_clp_rate(), 960.0)  # El probe exitoso devuelve y persiste la nueva tasa.
            self.assertEqual(provider_calls[0], 4)  # Confirma que el proveedor volvió a invocarse una vez vencido el timeout.


class RateLimiterTests(unittest.TestCase):
    """Comprueba los límites por ventana, ámbito y concurrencia."""

    def test_requests_are_rejected_after_configured_limit(self) -> None:
        """El bucket rechaza el intento excedente y se renueva al vencer la ventana."""
        with tempfile.TemporaryDirectory() as directory:
            limiter = RateLimiter(Path(directory) / "agency.db")  # Crea una tabla de buckets en una base aislada.
            self.assertTrue(
                limiter.allow_request(
                    "rut-test", limit=2, window_seconds=60, now=1000
                )  # Registra el primer intento permitido dentro de la ventana.
            )  # Comprueba el veredicto booleano de la solicitud.
            self.assertTrue(
                limiter.allow_request(
                    "rut-test", limit=2, window_seconds=60, now=1001
                )  # Registra el segundo intento, que completa el cupo configurado.
            )  # El segundo intento también debe estar permitido.
            self.assertFalse(
                limiter.allow_request(
                    "rut-test", limit=2, window_seconds=60, now=1002
                )  # El tercer intento ocurre antes de vencer los 60 segundos y excede el cupo.
            )  # Confirma que el rate limiter niega el acceso, no que lance una excepción.
            self.assertTrue(
                limiter.allow_request(
                    "rut-test", limit=2, window_seconds=60, now=1060
                )  # A los 60 segundos exactos empieza una ventana nueva.
            )  # El primer intento de la ventana renovada vuelve a permitirse.

    def test_concurrent_requests_do_not_exceed_limit(self) -> None:
        """La operación atómica en SQLite debe respetar el cupo entre varios hilos."""
        with tempfile.TemporaryDirectory() as directory:
            limiter = RateLimiter(Path(directory) / "agency.db")  # Comparte una única base SQLite entre todos los hilos.
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                results = list(
                    executor.map(
                        lambda _: limiter.allow_request(
                            "same-client",
                            limit=4,
                            window_seconds=60,
                            now=2000,
                        ),  # Cada hilo intenta consumir el mismo bucket y límite.
                        range(20),
                    )  # Lanza veinte llamadas de forma concurrente.
                )  # Reúne los booleanos devueltos por todas las solicitudes.
            self.assertEqual(sum(results), 4)  # Solo cuatro respuestas True prueban que no hubo sobreconsumo concurrente.

    def test_scopes_have_independent_limits(self) -> None:
        """Un mismo cliente puede tener buckets distintos por operación sensible."""
        with tempfile.TemporaryDirectory() as directory:
            limiter = RateLimiter(Path(directory) / "agency.db")  # Usa una base vacía y compartida por los dos ámbitos.
            self.assertTrue(
                limiter.allow_request(
                    "same-client",
                    scope="reservation",
                    limit=1,
                    window_seconds=60,
                    now=3000,
                )  # Consume el único permiso de creación de reserva para este cliente.
            )  # Verifica que el primer ámbito permite el intento.
            self.assertTrue(
                limiter.allow_request(
                    "same-client",
                    scope="authentication",
                    limit=1,
                    window_seconds=60,
                    now=3000,
                )  # Autenticación tiene otro bucket aunque comparta identificador.
            )  # Comprueba que los scopes no se interfieren entre sí.

    def test_existing_legacy_bucket_schema_is_migrated(self) -> None:
        """Una base local de la primera versión recibe columnas de expiración."""
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"  # Aísla el esquema antiguo del archivo de datos de la aplicación.
            with closing(sqlite3.connect(database_path)) as connection:  # Abre la base antes de que exista una instancia del limitador.
                with connection:  # Confirma la creación del esquema legado.
                    connection.execute(  # Crea la forma previa de rate_limit_buckets sin datos de expiración.
                        """
                        CREATE TABLE rate_limit_buckets (
                            bucket_key TEXT PRIMARY KEY,
                            window_started_at REAL NOT NULL,
                            request_count INTEGER NOT NULL,
                            last_request_allowed INTEGER NOT NULL
                        )
                        """
                    )  # Simula una base local generada por la versión anterior del servicio.

            limiter = RateLimiter(database_path)  # La inicialización añade las columnas nuevas sin borrar la tabla.
            with closing(sqlite3.connect(database_path)) as connection:  # Inspecciona el esquema ya migrado.
                columns = {  # Reúne los nombres que SQLite reporta para la tabla actual.
                    column[1]
                    for column in connection.execute(
                        "PRAGMA table_info(rate_limit_buckets)"
                    ).fetchall()
                }  # El nombre está en la segunda columna del resultado del pragma.
            self.assertIn("window_seconds", columns)  # Comprueba que el esquema nuevo registra duración de ventana.
            self.assertIn("expires_at", columns)  # Comprueba que el esquema nuevo registra vencimiento de bucket.
            self.assertTrue(  # Confirma que el limitador opera después de migrar la base anterior.
                limiter.allow_request(
                    "legacy-client", limit=1, window_seconds=60, now=4000
                )  # Crea una fila nueva usando el esquema actualizado.
            )  # La primera solicitud del cliente migrado debe permitirse.


if __name__ == "__main__":  # Permite ejecutar este archivo directamente con Python.
    unittest.main()  # Descubre y ejecuta las pruebas declaradas arriba.
