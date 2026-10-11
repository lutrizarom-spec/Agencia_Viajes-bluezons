"""Pruebas de integracion con bloqueos reales de SQLite y conexiones independientes.

Complementa las pruebas simuladas de F7: aqui la contencion la provoca una
segunda conexion que mantiene una transaccion abierta sobre el mismo archivo,
no una excepcion inyectada con mocks.
"""

import sqlite3  # Abre las conexiones independientes que mantienen los bloqueos reales.
import tempfile  # Aisla cada escenario en una base de datos descartable.
import threading  # Ejecuta la operacion del servicio mientras otra conexion retiene el lock.
import time  # Mide de forma acotada la espera observada por cada operacion.
import unittest  # Ejecuta las aserciones del contrato de persistencia y HTTP.
from contextlib import closing  # Cierra conexiones de inspeccion y permite borrar la base temporal.
from datetime import date  # Fija la fecha de viaje usada por el inventario.
from pathlib import Path  # Construye rutas portables dentro de la carpeta temporal.
from unittest.mock import patch  # Aisla solo la lectura previa de catalogo para alcanzar la lectura de cupos.

from fastapi.testclient import TestClient  # Ejecuta los endpoints reales dentro del proceso.

from dao.paquete_dao import PaqueteDao  # Crea el catalogo con el DAO real de la aplicacion.
from main_api import create_app  # Construye la API con servicios locales configurables.
from model.paquete_nacional import Paquete_Nacional  # Usa un paquete con precio determinista.
from services.auth_service import UserRole  # Aprovisiona cuentas cliente y administrador.
from services.compra_service import CompraService, DatabaseBusyError  # Servicio y error de dominio ante lock agotado.

TRAVEL_DATE = date(2026, 12, 15)  # Fecha de inventario usada por todas las pruebas.
TRAVEL_DATE_JSON = TRAVEL_DATE.isoformat()  # Representacion ISO esperada por los endpoints.
FREE_TIMEOUT = 0.0  # La conexion que toma el lock no debe esperar: falla rapido si el estado no es el previsto.


def _open_holder(database_path: Path) -> sqlite3.Connection:
    """Abre una conexion independiente con control manual de transacciones para retener el lock."""
    return sqlite3.connect(str(database_path), isolation_level=None, timeout=FREE_TIMEOUT)


class RealWriteLockServiceTests(unittest.TestCase):
    """Comprueba la politica de reintentos de CompraService contra un lock SQLite real."""

    def setUp(self) -> None:
        """Crea una base temporal con catalogo y paquete listos para inventario."""
        self.temp_directory = tempfile.TemporaryDirectory()  # Evita tocar cualquier base real del proyecto.
        self.addCleanup(self.temp_directory.cleanup)  # Elimina la carpeta despues de cerrar las conexiones.
        self.database_path = Path(self.temp_directory.name) / "service-locks.db"  # Define la base de esta prueba.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Prepara el esquema minimo necesario.
            with connection:  # Confirma catalogo y paquete como una sola unidad.
                PaqueteDao(connection).crear_tabla()  # Crea la tabla de paquetes que referencia el inventario.
                PaqueteDao(connection).insertar_paquete(  # Inserta el paquete cuyos cupos se configuraran.
                    Paquete_Nacional(101, "Paquete bloqueo", 2, 40000.0)
                )

    def test_write_lock_released_within_retry_window_completes_operation(self) -> None:
        """Un lock real liberado dentro de la ventana de reintentos deja la operacion exitosa."""
        service = CompraService(  # Configura una ventana amplia y estable medida en tiempo real.
            self.database_path,
            max_write_attempts=6,  # Permite varios intentos mientras el lock sigue activo.
            retry_delay_seconds=0.2,  # Separa los intentos lo suficiente para una prueba determinista.
        )
        holder = _open_holder(self.database_path)  # Segunda conexion independiente al mismo archivo.
        self.addCleanup(holder.close)  # Cierra la conexion aunque la prueba falle.
        holder.execute("BEGIN IMMEDIATE")  # Retiene un lock de escritura real.

        started = threading.Event()  # Senala que el hilo del servicio ya comenzo su operacion.
        outcome: dict[str, object] = {}  # Comunica el resultado del hilo sin compartir excepciones crudas.

        def attempt_capacity() -> None:
            """Ejecuta la operacion de escritura del servicio bajo el lock retenido."""
            started.set()  # Avisa al hilo principal que ya inicio la operacion.
            begin = time.monotonic()  # Marca el inicio para medir la espera observada.
            try:  # Registra exito o error de dominio del servicio.
                outcome["available"] = service.configure_capacity(101, TRAVEL_DATE, 5)  # Escritura con retry.
            except BaseException as error:  # Captura cualquier fallo para no perderlo en el hilo.
                outcome["error"] = error  # Expone el error para que la asercion lo reporte.
            finally:  # Mide siempre, incluso si algo falla en el hilo.
                outcome["elapsed"] = time.monotonic() - begin  # Conserva la duracion real de la operacion.

        thread = threading.Thread(target=attempt_capacity, daemon=True)  # Hilo de daemon evita procesos colgados.
        thread.start()  # Lanza la operacion del servicio.
        self.assertTrue(started.wait(timeout=5), "el hilo del servicio no inicio a tiempo")  # Evita esperas infinitas.
        time.sleep(0.3)  # Espera acotada: garantiza que el primer intento choque con el lock antes de liberarlo.
        holder.execute("ROLLBACK")  # Libera el lock de forma controlada dentro de la ventana de reintentos.
        thread.join(timeout=10)  # Limita el tiempo maximo de espera del hilo.
        self.assertFalse(thread.is_alive(), "la operacion del servicio quedo colgada")  # Ningun hilo abandonado.

        self.assertNotIn("error", outcome, f"la operacion fallo: {outcome.get('error')!r}")  # Exige exito.
        self.assertEqual(5, outcome["available"])  # La capacidad configurada coincide con la solicitada.
        self.assertGreaterEqual(  # Confirma que hubo contencion real antes de la liberacion.
            outcome["elapsed"], 0.2, "no se observo espera por el lock real"
        )
        self.assertEqual(5, service.get_available_capacity(101, TRAVEL_DATE))  # El estado final es consistente.

    def test_persistent_write_lock_exhausts_retries_and_raises_domain_error(self) -> None:
        """Un lock real que no se libera agota los reintentos y produce DatabaseBusyError."""
        service = CompraService(  # Politica corta para acotar el tiempo total de la prueba.
            self.database_path,
            max_write_attempts=2,  # Dos intentos bastan para agotar el presupuesto.
            retry_delay_seconds=0.02,  # Pausa minima entre intentos.
        )
        holder = _open_holder(self.database_path)  # Segunda conexion que retiene el lock todo el tiempo.
        self.addCleanup(holder.close)  # Garantiza el cierre aun si la prueba falla.
        holder.execute("BEGIN IMMEDIATE")  # Mantiene el lock de escritura durante toda la operacion.
        try:  # Libera y cierra ordenadamente incluso ante una falla de asercion.
            with self.assertRaises(DatabaseBusyError):  # El contrato espera el error de dominio, no SQL crudo.
                service.configure_capacity(101, TRAVEL_DATE, 5)  # Nunca podra adquirir el lock.
        finally:  # Restablece el estado de la conexion independiente.
            holder.execute("ROLLBACK")  # Libera el lock retenido.

        self.assertIsNone(  # Verifica que el fallo no dejo inventario parcial.
            service.get_available_capacity(101, TRAVEL_DATE),
            "el rollback total no dejo el inventario intacto",
        )


class AvailableCapacityLockTests(unittest.TestCase):
    """Comprueba que get_available_capacity aplica la politica de bloqueos del servicio."""

    def setUp(self) -> None:
        """Crea una base temporal con catalogo y paquete listos para consultar cupos."""
        self.temp_directory = tempfile.TemporaryDirectory()  # Evita tocar cualquier base real del proyecto.
        self.addCleanup(self.temp_directory.cleanup)  # Elimina la carpeta despues de cerrar las conexiones.
        self.database_path = Path(self.temp_directory.name) / "capacity-locks.db"  # Define la base de esta prueba.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Prepara el esquema minimo necesario.
            with connection:  # Confirma catalogo y paquete como una sola unidad.
                PaqueteDao(connection).crear_tabla()  # Crea la tabla de paquetes que referencia el inventario.
                PaqueteDao(connection).insertar_paquete(  # Inserta el paquete cuyos cupos se consultaran.
                    Paquete_Nacional(101, "Paquete capacidad", 2, 40000.0)
                )

    def test_available_capacity_recovers_when_exclusive_lock_is_released(self) -> None:
        """Una lectura de cupos bloqueada por un lock exclusivo real termina al liberarse."""
        service = CompraService(  # Amplia la ventana de reintentos para una prueba estable en tiempo real.
            self.database_path,
            max_write_attempts=6,  # Permite varios intentos mientras el lock sigue activo.
            retry_delay_seconds=0.2,  # Separa los intentos lo suficiente para observar la espera.
        )
        service.configure_capacity(101, TRAVEL_DATE, 4)  # Publica capacidad antes de bloquear la lectura.
        holder = _open_holder(self.database_path)  # Conexion independiente que bloquea lecturas concurrentes.
        self.addCleanup(holder.close)  # Garantiza el cierre aunque la prueba falle.
        holder.execute("BEGIN EXCLUSIVE")  # Bloquea lecturas reales del inventario.

        started = threading.Event()  # Senala que el hilo ya inicio la lectura de cupos.
        outcome: dict[str, object] = {}  # Comunica el resultado del hilo sin compartir excepciones crudas.

        def read_capacity() -> None:
            """Lee la disponibilidad bajo el lock exclusivo retenido."""
            started.set()  # Avisa al hilo principal que la operacion ya comenzo.
            begin = time.monotonic()  # Marca el inicio para medir la espera observada.
            try:  # Registra exito o error de dominio.
                outcome["available"] = service.get_available_capacity(101, TRAVEL_DATE)  # Lectura con retry.
            except BaseException as error:  # Conserva cualquier fallo del hilo.
                outcome["error"] = error  # Permite reportarlo en la asercion.
            finally:  # Mide siempre para evidenciar la contencion.
                outcome["elapsed"] = time.monotonic() - begin  # Duracion real de la lectura.

        thread = threading.Thread(target=read_capacity, daemon=True)  # Hilo daemon evita procesos colgados.
        thread.start()  # Lanza la lectura de cupos.
        self.assertTrue(started.wait(timeout=5), "el hilo de lectura no inicio a tiempo")  # Evita esperas infinitas.
        time.sleep(1.0)  # Espera acotada mayor al busy_timeout de un unico intento (0.1 s) para exigir reintentos.
        holder.execute("ROLLBACK")  # Libera el lock dentro de la ventana de reintentos.
        thread.join(timeout=10)  # Limita el tiempo maximo de espera.
        self.assertFalse(thread.is_alive(), "la lectura de cupos quedo colgada")  # Ningun hilo abandonado.

        self.assertNotIn("error", outcome, f"la lectura fallo: {outcome.get('error')!r}")  # Exige exito tras liberar.
        self.assertEqual(4, outcome["available"])  # Devuelve la capacidad real configurada.
        self.assertGreaterEqual(outcome["elapsed"], 0.5, "no se observo espera por el lock real")  # Evidencia de contencion.

    def test_available_capacity_exhausted_lock_raises_domain_error(self) -> None:
        """Un lock exclusivo persistente agota los reintentos y produce DatabaseBusyError."""
        service = CompraService(  # Politica corta para acotar el tiempo total de la prueba.
            self.database_path,
            max_write_attempts=2,  # Dos intentos bastan para agotar el presupuesto.
            retry_delay_seconds=0.02,  # Pausa minima entre intentos.
        )
        service.configure_capacity(101, TRAVEL_DATE, 3)  # Publica capacidad antes de bloquear.
        holder = _open_holder(self.database_path)  # Conexion independiente que retiene el lock todo el tiempo.
        self.addCleanup(holder.close)  # Garantiza el cierre aun si la prueba falla.
        holder.execute("BEGIN EXCLUSIVE")  # Bloquea la lectura durante toda la operacion.
        try:  # Libera el lock de forma segura incluso ante una falla.
            with self.assertRaises(DatabaseBusyError):  # El servicio debe traducir el lock, no filtrar SQL crudo.
                service.get_available_capacity(101, TRAVEL_DATE)  # Nunca podra leer bajo el lock persistente.
        finally:  # Restablece el estado de la conexion independiente.
            holder.execute("ROLLBACK")  # Libera el lock retenido.

        self.assertEqual(3, service.get_available_capacity(101, TRAVEL_DATE))  # La capacidad no se modifico.


class RealLockHttpContractTests(unittest.TestCase):
    """Comprueba la traduccion HTTP de bloqueos reales en la aplicacion autentica."""

    def setUp(self) -> None:
        """Crea una API temporal con paquete y cuentas listos para operar."""
        self.temp_directory = tempfile.TemporaryDirectory()  # Aisla la base de la aplicacion por prueba.
        self.addCleanup(self.temp_directory.cleanup)  # Borra la base al terminar las conexiones.
        self.database_path = Path(self.temp_directory.name) / "api-locks.db"  # Base exclusiva de esta clase.
        self.app = create_app(  # Construye la aplicacion real con dependencias locales.
            self.database_path,
            jwt_secret="real-lock-http-test-secret-at-least-32-bytes",  # Secreto exclusivo de pruebas.
            fx_provider=lambda: 900.0,  # Proveedor sin red para aislamiento total.
        )
        self.client = TestClient(self.app, raise_server_exceptions=False)  # Observa el contrato HTTP real.
        self.addCleanup(self.client.close)  # Cierra el cliente antes de borrar la base.
        self.app.state.auth_service.create_user(  # Aprovisiona la cuenta cliente usada por las reservas.
            "10.000.013-K", "cliente-seguro-local-2026", UserRole.CLIENTE
        )
        self.app.state.auth_service.create_user(  # Aprovisiona la cuenta administradora.
            "9.876.543-3", "admin-seguro-local-2026", UserRole.ADMINISTRADOR
        )
        with closing(sqlite3.connect(self.database_path)) as connection:  # Inserta el paquete de prueba.
            with connection:  # Confirma la escritura del fixture.
                PaqueteDao(connection).insertar_paquete(Paquete_Nacional(101, "Paquete bloqueo", 2, 40000.0))

    def _login(self, rut: str, password: str) -> str:
        """Obtiene un JWT autentico mediante el endpoint real."""
        response = self.client.post("/auth/login", json={"rut": rut, "password": password})  # Login real.
        self.assertEqual(200, response.status_code, response.text)  # Exige credenciales validas.
        return response.json()["access_token"]  # Retorna el bearer para rutas protegidas.

    def _admin_headers(self) -> dict[str, str]:
        """Construye el encabezado Authorization administrativo."""
        return {"Authorization": f"Bearer {self._login('9.876.543-3', 'admin-seguro-local-2026')}"}

    def _client_headers(self) -> dict[str, str]:
        """Construye el encabezado Authorization del cliente titular."""
        return {"Authorization": f"Bearer {self._login('10.000.013-K', 'cliente-seguro-local-2026')}"}

    def test_post_reservas_with_real_write_lock_returns_503_without_partial_reservation(self) -> None:
        """Un lock real durante POST /reservas responde 503 con Retry-After y no crea reserva.

        El orden del endpoint hace que la contencion se detecte primero en el
        RateLimiter (primer escritor sobre la misma base compartida); el contrato
        HTTP observado es el mismo 503 + Retry-After que exige la documentacion.
        """
        admin_headers = self._admin_headers()  # Autentica al administrador antes de bloquear.
        client_headers = self._client_headers()  # Autentica al cliente antes de bloquear.
        configured = self.client.put(  # Configura cupos sin lock para medir el estado inicial.
            "/admin/paquetes/101/inventario",
            json={"total_capacity": 5, "travel_date": TRAVEL_DATE_JSON},
            headers=admin_headers,
        )
        self.assertEqual(200, configured.status_code, configured.text)  # Exige inventario configurado.

        holder = _open_holder(self.database_path)  # Conexion independiente que retiene el lock.
        self.addCleanup(holder.close)  # Cierra la conexion aun ante fallo.
        holder.execute("BEGIN IMMEDIATE")  # Bloquea la escritura durante la peticion HTTP.
        try:  # Asegura liberar el lock despues de la peticion.
            response = self.client.post(  # Peticion real de reserva bajo contencion.
                "/reservas",
                json={"package_code": 101, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
                headers=client_headers,
            )
        finally:  # Libera el lock retenido.
            holder.execute("ROLLBACK")

        self.assertEqual(503, response.status_code, response.text)  # Indisponibilidad temporal documentada.
        self.assertEqual("1", response.headers.get("Retry-After"))  # Conserva la indicacion de reintento.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Inspecciona el estado final.
            reservations = connection.execute("SELECT COUNT(*) FROM reservas").fetchone()[0]  # Ninguna reserva.
            inventory = connection.execute(  # Inventario sin cupos consumidos.
                "SELECT total_capacity, reserved_capacity FROM package_inventory WHERE package_code = 101 AND travel_date = ?",
                (TRAVEL_DATE_JSON,),
            ).fetchone()
        self.assertEqual(0, reservations)  # El rechazo no creo una reserva parcial.
        self.assertEqual((5, 0), inventory)  # Los cupos no se alteraron indebidamente.

    def test_admin_reservations_under_write_lock_follows_documented_503_contract(self) -> None:
        """Un lock real en GET /admin/reservas deberia seguir el contrato 503 + Retry-After.

        Contrato documentado en README (commit a9f34a2). Estado reproducido hoy:
        `list_all_reservations` no envuelve `list_reservations`, por lo que
        DatabaseBusyError escapa y la API responde 500 sin Retry-After. Esta
        prueba de regresion debe pasar cuando el endpoint se alinee al contrato.
        """
        admin_headers = self._admin_headers()  # Autentica antes de tomar el lock exclusivo.
        holder = _open_holder(self.database_path)  # Conexion independiente que retiene el lock.
        self.addCleanup(holder.close)  # Cierra la conexion aun si la prueba falla.
        holder.execute("BEGIN EXCLUSIVE")  # El lock exclusivo tambien bloquea lecturas concurrentes.
        try:  # Asegura liberar el lock tras la peticion.
            response = self.client.get("/admin/reservas", headers=admin_headers)  # Lectura global administrativa.
        finally:  # Libera el lock retenido.
            holder.execute("ROLLBACK")

        self.assertEqual(503, response.status_code, response.text)  # Contrato esperado para un lock transitorio.
        self.assertEqual("1", response.headers.get("Retry-After"))  # Indicacion de reintento documentada.

    def test_public_catalog_capacity_read_with_real_lock_returns_503(self) -> None:
        """Un lock real en la lectura de cupos de GET /paquetes conserva 503 + Retry-After.

        Un unico lock real bloquearia antes la lectura previa del catalogo, que usa
        otra conexion y otro timeout. Para alcanzar de verdad la lectura de
        disponibilidad se aísla solo esa lectura previa (condicion auxiliar) y se
        mantiene el lock real exclusivo sobre la consulta de cupos, que es la
        operacion bajo prueba.
        """
        synthetic_row = (101, "Paquete bloqueo", 2, 40000.0, "nacional", None, None)  # Fila con el formato del catalogo.
        holder = _open_holder(self.database_path)  # Conexion independiente que retiene el lock.
        self.addCleanup(holder.close)  # Cierra la conexion aun si la prueba falla.
        holder.execute("BEGIN EXCLUSIVE")  # Bloquea las lecturas reales de inventario.
        try:  # Asegura liberar el lock tras la peticion.
            with patch.object(PaqueteDao, "obtener_paquetes", return_value=[synthetic_row]):  # Aisla solo la lectura previa.
                response = self.client.get(f"/paquetes?travel_date={TRAVEL_DATE_JSON}")  # Alcanza la lectura de cupos bajo lock.
        finally:  # Libera el lock retenido.
            holder.execute("ROLLBACK")

        self.assertEqual(503, response.status_code, response.text)  # Indisponibilidad temporal documentada.
        self.assertEqual("1", response.headers.get("Retry-After"))  # Conserva la indicacion de reintento.
        self.assertNotIn("database is locked", response.text)  # No expone el mensaje interno del motor.


if __name__ == "__main__":  # Permite ejecutar el archivo directamente con Python.
    unittest.main()  # Descubre y ejecuta las clases de prueba definidas.
