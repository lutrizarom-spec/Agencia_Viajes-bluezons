"""Pruebas de integración del CRUD administrativo y la baja lógica de paquetes."""

import sqlite3  # Inspecciona los registros conservados después de una baja lógica.
import tempfile  # Mantiene los cambios de cada prueba fuera de la base local real.
import unittest  # Ejecuta escenarios integrados con la biblioteca estándar.
from contextlib import closing  # Cierra conexiones SQLite de fixtures y verificaciones.
from datetime import date  # Da a la configuración de inventario y reservas un día explícito.
from pathlib import Path  # Construye rutas independientes del directorio actual.
from unittest.mock import patch  # Simula bloqueos SQLite en las operaciones de paquetes.

from fastapi.testclient import TestClient  # Invoca la API ASGI sin abrir puertos ni servicios externos.

from dao.paquete_dao import PaqueteDao  # Verifica migración aditiva del esquema de paquetes.
from main_api import create_app  # Construye los endpoints y servicios reales sobre una base temporal.
from services.auth_service import UserRole  # Aprovisiona identidades de cliente y administrador.

TRAVEL_DATE_JSON = date(2026, 12, 15).isoformat()


class PackageAdminApiTests(unittest.TestCase):
    """Comprueba autorización, edición, publicación y preservación de historial."""

    def setUp(self) -> None:
        """Prepara una API aislada con cuentas locales de ambos roles."""
        self.temp_directory = tempfile.TemporaryDirectory()  # Aísla los datos de una ejecución de prueba.
        self.addCleanup(self.temp_directory.cleanup)  # Borra la base temporal cuando el cliente ya esté cerrado.
        self.database_path = Path(self.temp_directory.name) / "package-admin.db"  # Define el archivo SQLite de prueba.
        self.app = create_app(  # Construye la aplicación con dependencias totalmente locales.
            self.database_path,  # Comparte esta base con API y servicios.
            jwt_secret="package-admin-test-secret-at-least-32-bytes",  # Usa una clave exclusiva para pruebas.
            fx_provider=lambda: 900.0,  # Impide llamadas externas en rutas no relacionadas con FX.
        )  # Crea/migra el catálogo y prepara servicios dependientes.
        self.client = TestClient(self.app)  # Permite realizar requests HTTP integrados.
        self.addCleanup(self.client.close)  # Libera la aplicación antes de borrar la base.
        self.app.state.auth_service.create_user(  # Registra cuenta de cliente para probar denegaciones.
            "10.000.013-K", "cliente-admin-test-2026", UserRole.CLIENTE
        )  # Persiste hash de contraseña y rol de cliente.
        self.app.state.auth_service.create_user(  # Registra cuenta administrativa para CRUD.
            "9.876.543-3", "admin-paquetes-test-2026", UserRole.ADMINISTRADOR
        )  # Persiste hash de contraseña y rol administrador.
        self._headers_by_role: dict[str, dict[str, str]] = {}  # Reutiliza los JWT de fixture y evita consumir el rate limit de login.

    def _login(self, rut: str, password: str) -> dict[str, str]:
        """Obtiene encabezados Bearer llamando el endpoint de login real."""
        response = self.client.post("/auth/login", json={"rut": rut, "password": password})  # Autentica la cuenta por RUT.
        self.assertEqual(response.status_code, 200)  # Asegura que las credenciales del fixture son correctas.
        token = response.json()["access_token"]  # Recupera el JWT firmado por AuthService.
        return {"Authorization": f"Bearer {token}"}  # Construye el header requerido en las rutas protegidas.

    def _admin_headers(self) -> dict[str, str]:
        """Autentica una cuenta de administrador para operar el catálogo."""
        if "admin" not in self._headers_by_role:  # Obtiene el JWT solo en el primer uso de cada escenario.
            self._headers_by_role["admin"] = self._login("9.876.543-3", "admin-paquetes-test-2026")  # Guarda la credencial administrativa reutilizable.
        return self._headers_by_role["admin"]  # Devuelve token con privilegios administrativos.

    def _client_headers(self) -> dict[str, str]:
        """Autentica una cuenta cliente para comprobar aislamiento de rol."""
        if "client" not in self._headers_by_role:  # Evita superar el límite en escenarios con varias solicitudes.
            self._headers_by_role["client"] = self._login("10.000.013-K", "cliente-admin-test-2026")  # Guarda el JWT cliente para el caso actual.
        return self._headers_by_role["client"]  # Devuelve token sin permisos administrativos.

    def _create_national_package(self, code: int = 303) -> dict[str, object]:
        """Crea un paquete nacional por la API y retorna su respuesta."""
        response = self.client.post(  # Ejecuta el endpoint administrativo de creación.
            "/admin/paquetes",  # Usa el recurso de catálogo protegido.
            headers=self._admin_headers(),  # Autoriza la operación con rol administrador.
            json={  # Proporciona solo campos pertinentes al subtipo nacional.
                "codigo": code,  # Define la clave única del nuevo paquete.
                "nombre": "Escapada cordillerana",  # Nombre legible para el catálogo.
                "duracion": 4,  # Duración positiva en días.
                "precio_base": 75000,  # Precio base positivo validado por el modelo.
                "tipo": "nacional",  # Selecciona la fórmula nacional existente.
            },  # Cierra el documento de creación.
        )  # Persiste el registro en SQLite.
        self.assertEqual(response.status_code, 201, response.text)  # Confirma la creación y facilita diagnóstico ante error.
        return response.json()  # Devuelve el paquete persistido para las comprobaciones.

    def test_admin_can_create_edit_and_list_packages(self) -> None:
        """CRUD crea y edita campos sin alterar el precio polimórfico."""
        created = self._create_national_package()  # Crea un registro publicado mediante HTTP.
        self.assertEqual(created["precio_por_persona"], 75000.0)  # Comprueba el cálculo nacional actual.
        updated = self.client.put(  # Actualiza los campos editables manteniendo el código fijo.
            "/admin/paquetes/303",  # Identifica el recurso por su código.
            headers=self._admin_headers(),  # Exige el rol administrativo en la edición.
            json={  # Entrega un nuevo conjunto coherente de campos nacionales.
                "nombre": "Nueva escapada",  # Cambia el nombre visible.
                "duracion": 5,  # Cambia duración por un entero válido.
                "precio_base": 82000,  # Cambia precio base sin modificar fórmula.
                "tipo": "nacional",  # Mantiene el subtipo nacional.
            },  # Cierra el JSON de actualización.
        )  # Ejecuta la escritura y respuesta administrativa.
        self.assertEqual(updated.status_code, 200, updated.text)  # Confirma que PUT actualizó el paquete.
        self.assertEqual(updated.json()["codigo"], 303)  # Verifica que la clave original no cambió.
        self.assertEqual(updated.json()["precio_por_persona"], 82000.0)  # Verifica precio producido por el mismo modelo.

        admin_list = self.client.get("/admin/paquetes", headers=self._admin_headers())  # Solicita la vista completa con estado.
        public_list = self.client.get("/paquetes")  # Solicita el catálogo visible al público.
        self.assertEqual(admin_list.status_code, 200)  # Confirma acceso administrativo.
        self.assertEqual(admin_list.json()[0]["activo"], True)  # Comprueba bandera de publicación.
        self.assertEqual(public_list.json()[0]["nombre"], "Nueva escapada")  # Comprueba que la edición aparece públicamente.

    def test_clients_cannot_use_admin_package_routes(self) -> None:
        """Las operaciones administrativas requieren rol, no solo un JWT válido."""
        response = self.client.post(  # Intenta crear un paquete con una cuenta cliente autenticada.
            "/admin/paquetes",  # Usa la ruta que solo corresponde a administración.
            headers=self._client_headers(),  # Envía un JWT válido con rol de cliente.
            json={  # El cuerpo válido aísla autorización de validación.
                "codigo": 304,  # Código que no debe persistirse por el rechazo.
                "nombre": "No autorizado",  # Nombre de fixture.
                "duracion": 2,  # Duración válida.
                "precio_base": 10000,  # Precio válido.
                "tipo": "nacional",  # Tipo válido.
            },  # Cierra el cuerpo de solicitud.
        )  # Ejecuta autenticación y autorización de FastAPI.
        self.assertEqual(response.status_code, 403)  # Deniega acceso administrativo al cliente.
        self.assertEqual(self.client.get("/admin/paquetes", headers=self._client_headers()).status_code, 403)  # Protege también el listado completo.

    def test_duplicate_package_code_returns_conflict(self) -> None:
        """Un alta con código duplicado responde 409 sin sobrescribir el original."""
        self._create_national_package(305)  # Persiste el código que se usará en el conflicto.
        response = self.client.post(  # Repite el alta para la misma clave primaria.
            "/admin/paquetes",  # Usa la ruta administrativa de creación.
            headers=self._admin_headers(),  # Autoriza la solicitud duplicada.
            json={  # Envía datos distintos que no deben reemplazar los existentes.
                "codigo": 305,  # Reutiliza intencionalmente la clave.
                "nombre": "Nombre duplicado",  # Valor que no debe llegar a persistirse.
                "duracion": 2,  # Dato válido.
                "precio_base": 10,  # Dato válido.
                "tipo": "nacional",  # Dato válido.
            },  # Cierra el cuerpo de prueba.
        )  # Ejecuta la inserción conflictiva.
        self.assertEqual(response.status_code, 409)  # Expone el conflicto de unicidad como HTTP 409.
        self.assertEqual(self.client.get("/paquetes").json()[0]["nombre"], "Escapada cordillerana")  # Confirma que la fila original no se sustituyó.

    def test_subtype_fields_are_required_and_returned_only_for_matching_type(self) -> None:
        """El contrato administrativo valida y serializa los campos de cada subtipo."""
        missing_passport = self.client.post(  # Intenta construir un paquete internacional incompleto.
            "/admin/paquetes",  # Usa la ruta de alta con privilegios.
            headers=self._admin_headers(),  # Autoriza la operación.
            json={"codigo": 308, "nombre": "Internacional", "duracion": 7, "precio_base": 50000, "tipo": "internacional"},  # Omite su campo obligatorio específico.
        )  # Pydantic aplica la validación entre campos del subtipo.
        self.assertEqual(missing_passport.status_code, 422)  # Rechaza el cuerpo semánticamente incompleto.

        international = self.client.post(  # Crea un paquete internacional con pasaporte explícito.
            "/admin/paquetes",  # Mantiene el endpoint común de creación.
            headers=self._admin_headers(),  # Reutiliza la credencial administrativa del escenario.
            json={"codigo": 308, "nombre": "Internacional", "duracion": 7, "precio_base": 50000, "tipo": "internacional", "pasaporte_valido": False},  # False es un valor explícito y válido.
        )  # Persiste la instancia internacional.
        cruise = self.client.post(  # Crea un crucero con recargo de puerto.
            "/admin/paquetes",  # Usa el mismo recurso para otro subtipo.
            headers=self._admin_headers(),  # Reutiliza el JWT para no gastar otro intento de login.
            json={"codigo": 309, "nombre": "Crucero", "duracion": 5, "precio_base": 60000, "tipo": "crucero", "impuesto_puerto": 12500},  # Proporciona su impuesto obligatorio.
        )  # Persiste la instancia de crucero.
        self.assertEqual(international.status_code, 201, international.text)  # Confirma que false no se trata como dato ausente.
        self.assertIs(international.json()["pasaporte_valido"], False)  # Verifica la conversión booleana del campo persistido.
        self.assertEqual(international.json()["impuesto_puerto"], None)  # Evita exponer un campo ajeno al subtipo.
        self.assertEqual(cruise.status_code, 201, cruise.text)  # Confirma creación del subtipo crucero.
        self.assertEqual(cruise.json()["impuesto_puerto"], 12500.0)  # Verifica que se conservó el recargo específico.
        self.assertIsNone(cruise.json()["pasaporte_valido"])  # Evita exponer atributo internacional en un crucero.

    def test_logical_deletion_hides_package_and_preserves_reservation_history(self) -> None:
        """La baja oculta del catálogo, conserva reservas y bloquea ventas futuras."""
        self._create_national_package(306)  # Crea el paquete que conservará el historial.
        inventory = self.client.put(  # Configura cupos antes de realizar la reserva.
            "/admin/paquetes/306/inventario",  # Usa el endpoint de inventario preexistente.
            headers=self._admin_headers(),  # Configura cupos como administrador.
            json={"total_capacity": 2, "travel_date": TRAVEL_DATE_JSON},  # Deja inventario suficiente para esa fecha.
        )  # Persiste la capacidad.
        self.assertEqual(inventory.status_code, 200)  # Confirma que el paquete está disponible para venta.
        reservation = self.client.post(  # Crea una reserva que debe sobrevivir a la baja del producto.
            "/reservas",  # Usa la ruta de compra transaccional.
            headers=self._client_headers(),  # Vincula la compra al RUT del cliente autenticado.
            json={"package_code": 306, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Retiene cupo fechado y crea historial.
        )  # Completa la transacción de compra.
        self.assertEqual(reservation.status_code, 201)  # Confirma la reserva previa a la baja.

        deleted = self.client.delete("/admin/paquetes/306", headers=self._admin_headers())  # Solicita baja lógica.
        repeated = self.client.delete("/admin/paquetes/306", headers=self._admin_headers())  # Repite la baja para verificar idempotencia.
        self.assertEqual(deleted.status_code, 200)  # Confirma la operación administrativa.
        self.assertFalse(deleted.json()["activo"])  # Verifica que dejó de estar publicado.
        self.assertEqual(repeated.status_code, 200)  # Repetir la baja no produce un error ni borra más datos.
        self.assertEqual(self.client.get("/paquetes").json(), [])  # No aparece en el catálogo público.
        admin_rows = self.client.get("/admin/paquetes", headers=self._admin_headers()).json()  # El administrador conserva visibilidad del registro.
        self.assertFalse(admin_rows[0]["activo"])  # La lista administrativa permite identificar la baja.
        reservations = self.client.get("/reservas", headers=self._client_headers()).json()  # Consulta el historial con identidad propietaria.
        self.assertEqual(reservations[0]["reservation_id"], reservation.json()["reservation_id"])  # La reserva permanece asociada al paquete.
        self.assertEqual(reservations[0]["payment_id"], reservation.json()["payment_id"])  # El pago histórico tampoco se elimina.

        blocked_purchase = self.client.post(  # Comprueba que la baja no permite ventas nuevas.
            "/reservas",  # Ejecuta el mismo flujo normal de compra.
            headers=self._client_headers(),  # Presenta credenciales válidas del cliente.
            json={"package_code": 306, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Intenta comprar el paquete ya inactivo.
        )  # El servicio transaccional debe tratarlo como no disponible.
        blocked_inventory = self.client.put(  # Comprueba que no se puede volver a publicar inventario por esa vía.
            "/admin/paquetes/306/inventario",  # Solicita modificar capacidad del paquete inactivo.
            headers=self._admin_headers(),  # Usa rol autorizado para aislar el estado del recurso.
            json={"total_capacity": 3, "travel_date": TRAVEL_DATE_JSON},  # Capacidad válida para la fecha pedida.
        )  # El servicio rechaza configurar nuevos cupos.
        self.assertEqual(blocked_purchase.status_code, 404)  # Un paquete oculto se considera ausente en la compra pública.
        self.assertEqual(blocked_inventory.status_code, 404)  # Un producto dado de baja no admite nuevas operaciones de inventario.

        with closing(sqlite3.connect(self.database_path)) as connection:  # Inspecciona directamente el registro conservado.
            package_row = connection.execute("SELECT activo FROM paquetes WHERE codigo = ?", (306,)).fetchone()  # Consulta solo la bandera del paquete.
            reservation_count = connection.execute("SELECT COUNT(*) FROM reservas WHERE package_code = ?", (306,)).fetchone()[0]  # Cuenta referencias históricas que deben seguir existiendo.
        self.assertEqual(package_row, (0,))  # Confirma que la fila existe y está inactiva.
        self.assertEqual(reservation_count, 1)  # Confirma que la baja no borró la reserva relacionada.

    def test_legacy_package_table_is_migrated_without_losing_rows(self) -> None:
        """La migración añade activo y deja publicados los datos existentes."""
        with tempfile.TemporaryDirectory() as directory:  # Aísla una base de una versión anterior.
            database_path = Path(directory) / "legacy.db"  # Define archivo exclusivo para el esquema legado.
            with closing(sqlite3.connect(database_path)) as connection:  # Crea la tabla antigua sin la columna activo.
                connection.execute(  # Reproduce el esquema previo a la baja lógica.
                    "CREATE TABLE paquetes (codigo INTEGER PRIMARY KEY, nombre TEXT NOT NULL, duracion INTEGER NOT NULL, precio_base REAL NOT NULL, tipo TEXT NOT NULL, pasaporte_valido INTEGER, impuesto_puerto REAL)"
                )  # Deja una tabla válida que aún no conoce bajas.
                connection.execute(  # Inserta un producto legado que debe seguir disponible tras migrar.
                    "INSERT INTO paquetes VALUES (?, ?, ?, ?, ?, ?, ?)", (307, "Paquete antiguo", 3, 40000, "nacional", None, None)
                )  # Conserva un ejemplo de fila histórica.
                PaqueteDao(connection).crear_tabla()  # Ejecuta el cambio aditivo y confirma la migración.
                migrated = connection.execute("SELECT codigo, activo FROM paquetes").fetchone()  # Comprueba clave y valor predeterminado.
            self.assertEqual(migrated, (307, 1))  # Verifica que la fila sigue intacta y publicada.

    def _create_body(self, code: int) -> dict[str, object]:
        """Devuelve un cuerpo válido de paquete nacional para las pruebas de persistencia."""
        return {"codigo": code, "nombre": "Paquete de borde", "duracion": 3, "precio_base": 50000, "tipo": "nacional"}

    def _update_body(self) -> dict[str, object]:
        """Devuelve un cuerpo válido de edición para las pruebas de persistencia."""
        return {"nombre": "Paquete editado", "duracion": 4, "precio_base": 60000, "tipo": "nacional"}

    def test_create_package_returns_503_when_write_locked(self) -> None:
        """Un lock de SQLite en el alta responde 503 con Retry-After, no 500."""
        with patch.object(PaqueteDao, "insertar_paquete", side_effect=sqlite3.OperationalError("database is locked")):  # Simula la escritura bloqueada.
            response = self.client.post(  # Invoca el alta administrativa real.
                "/admin/paquetes",  # Ruta que escribe el catálogo.
                headers=self._admin_headers(),  # Autoriza con rol administrador.
                json=self._create_body(401),  # Cuerpo válido del subtipo nacional.
            )  # El bloqueo debe clasificarse como indisponibilidad transitoria.
        self.assertEqual(response.status_code, 503, response.text)  # Exige 503 en lugar de 500.
        self.assertEqual(response.headers.get("Retry-After"), "1")  # Indica cuándo reintentar.

    def test_update_package_returns_503_when_write_locked(self) -> None:
        """Un lock de SQLite en la edición responde 503 con Retry-After."""
        self._create_national_package(402)  # Deja un paquete activo editable.
        with patch.object(PaqueteDao, "actualizar_paquete", side_effect=sqlite3.OperationalError("database is locked")):  # Simula la escritura bloqueada.
            response = self.client.put(  # Invoca la edición administrativa real.
                "/admin/paquetes/402",  # Identifica el paquete activo.
                headers=self._admin_headers(),  # Autoriza con rol administrador.
                json=self._update_body(),  # Cuerpo válido de edición.
            )  # El bloqueo debe clasificarse como indisponibilidad transitoria.
        self.assertEqual(response.status_code, 503, response.text)  # Exige 503 en lugar de 500.
        self.assertEqual(response.headers.get("Retry-After"), "1")  # Indica cuándo reintentar.

    def test_delete_package_returns_503_when_write_locked(self) -> None:
        """Un lock de SQLite en la baja responde 503 con Retry-After."""
        self._create_national_package(403)  # Deja un paquete activo que será dado de baja.
        with patch.object(PaqueteDao, "eliminar_paquete", side_effect=sqlite3.OperationalError("database is locked")):  # Simula la baja bloqueada.
            response = self.client.delete(  # Invoca la baja lógica real.
                "/admin/paquetes/403",  # Identifica el paquete activo.
                headers=self._admin_headers(),  # Autoriza con rol administrador.
            )  # El bloqueo debe clasificarse como indisponibilidad transitoria.
        self.assertEqual(response.status_code, 503, response.text)  # Exige 503 en lugar de 500.
        self.assertEqual(response.headers.get("Retry-After"), "1")  # Indica cuándo reintentar.

    def test_public_catalog_returns_503_when_read_locked(self) -> None:
        """Un lock de SQLite en la lectura pública del catálogo responde 503."""
        with patch.object(PaqueteDao, "obtener_paquetes", side_effect=sqlite3.OperationalError("database is locked")):  # Simula la lectura bloqueada.
            response = self.client.get("/paquetes")  # Invoca el catálogo público real.
        self.assertEqual(response.status_code, 503, response.text)  # Exige 503 en lugar de 500.
        self.assertEqual(response.headers.get("Retry-After"), "1")  # Indica cuándo reintentar.

    def test_admin_catalog_returns_503_when_read_locked(self) -> None:
        """Un lock de SQLite en la lectura administrativa responde 503."""
        headers = self._admin_headers()  # Obtiene el JWT antes de simular el bloqueo.
        with patch.object(PaqueteDao, "obtener_paquetes_admin", side_effect=sqlite3.OperationalError("database is locked")):  # Simula la lectura bloqueada.
            response = self.client.get("/admin/paquetes", headers=headers)  # Invoca el listado administrativo real.
        self.assertEqual(response.status_code, 503, response.text)  # Exige 503 en lugar de 500.
        self.assertEqual(response.headers.get("Retry-After"), "1")  # Indica cuándo reintentar.

    def test_create_package_returns_500_when_sql_error_is_permanent(self) -> None:
        """Un error SQLite no transitorio conserva el 500 y su detalle actual."""
        with patch.object(PaqueteDao, "insertar_paquete", side_effect=sqlite3.OperationalError("near 'BROKEN': syntax error")):  # Simula un error que no es lock.
            response = self.client.post(  # Invoca el alta administrativa real.
                "/admin/paquetes",  # Ruta que escribe el catálogo.
                headers=self._admin_headers(),  # Autoriza con rol administrador.
                json=self._create_body(404),  # Cuerpo válido del subtipo nacional.
            )  # Un error permanente no debe presentarse como indisponibilidad.
        self.assertEqual(response.status_code, 500, response.text)  # Conserva el 500 actual.
        self.assertEqual(response.json()["detail"], "No se pudo persistir el paquete.")  # Mensaje estable sin SQL.

    def test_create_package_post_commit_read_failure_returns_500(self) -> None:
        """Si la lectura posterior al commit falla, el alta responde 500 y no 503."""
        with patch.object(PaqueteDao, "obtener_paquete", side_effect=sqlite3.OperationalError("database is locked")):  # Simula bloqueo solo en la relectura.
            response = self.client.post(  # La escritura se confirma antes de la lectura posterior.
                "/admin/paquetes",  # Ruta que escribe y relee el catálogo.
                headers=self._admin_headers(),  # Autoriza con rol administrador.
                json=self._create_body(405),  # Cuerpo válido del subtipo nacional.
            )  # La escritura ya se confirmó: repetir la baja duplicaría el alta.
        self.assertEqual(response.status_code, 500, response.text)  # No debe ser 503 tras el commit.
        self.assertEqual(response.json()["detail"], "El paquete se creó, pero no pudo recuperarse.")  # Mensaje existente del caso.

    def test_delete_package_post_commit_read_failure_returns_500(self) -> None:
        """Si la lectura posterior a la baja falla, responde 500 y no 503."""
        self._create_national_package(406)  # Deja un paquete activo que será dado de baja.
        original = PaqueteDao.obtener_paquete  # Conserva la lectura real para la relectura previa.
        calls = {"count": 0}  # Distingue la lectura previa de la posterior al commit.

        def flaky_read(self: PaqueteDao, codigo: int, *, incluir_inactivo: bool = False) -> tuple | None:
            """Falla solo en la segunda lectura, ya con la baja confirmada."""
            calls["count"] += 1  # Cuenta cada lectura del endpoint.
            if calls["count"] >= 2:  # La segunda lectura es la posterior al commit.
                raise sqlite3.OperationalError("database is locked")  # Simula el bloqueo en la relectura.
            return original(self, codigo, incluir_inactivo=incluir_inactivo)  # La lectura previa funciona normalmente.

        with patch.object(PaqueteDao, "obtener_paquete", flaky_read):  # Simula bloqueo solo tras el commit.
            response = self.client.delete(  # Invoca la baja lógica real.
                "/admin/paquetes/406",  # Identifica el paquete activo.
                headers=self._admin_headers(),  # Autoriza con rol administrador.
            )  # La baja ya se confirmó: repetirla sería idempotente, pero no debe devolver 503.
        self.assertEqual(response.status_code, 500, response.text)  # No debe ser 503 tras el commit.
        self.assertEqual(response.json()["detail"], "No se pudo recuperar el paquete desactivado.")  # Mensaje existente del caso.

    def test_update_package_post_commit_read_failure_returns_500(self) -> None:
        """Si la lectura posterior al commit falla, la edición responde 500 y no 503."""
        self._create_national_package(407)  # Deja un paquete activo editable.
        with patch.object(PaqueteDao, "obtener_paquete", side_effect=sqlite3.OperationalError("database is locked")):  # Simula bloqueo solo en la relectura.
            response = self.client.put(  # La edición se confirma antes de la lectura posterior.
                "/admin/paquetes/407",  # Ruta que edita y relee el catálogo.
                headers=self._admin_headers(),  # Autoriza con rol administrador.
                json=self._update_body(),  # Cuerpo válido de edición.
            )  # La edición ya se confirmó: no debe presentarse como fallo de escritura ni como 503.
        self.assertEqual(response.status_code, 500, response.text)  # No debe ser 503 tras el commit.
        self.assertEqual(response.json()["detail"], "El paquete se actualizó, pero no pudo recuperarse.")  # Mensaje que refleja el commit confirmado.

    def test_public_catalog_returns_503_when_capacity_read_locked(self) -> None:
        """Un lock de SQLite al leer cupos responde 503 con Retry-After."""
        self._create_national_package(408)  # Garantiza al menos un paquete en el catálogo público.
        with patch.object(self.app.state.compra_service, "get_available_capacity", side_effect=sqlite3.OperationalError("database is locked")):  # Simula bloqueo solo en la lectura de disponibilidad.
            response = self.client.get("/paquetes")  # Invoca el catálogo público real.
        self.assertEqual(response.status_code, 503, response.text)  # Un lock de cupos es transitorio.
        self.assertEqual(response.headers.get("Retry-After"), "1")  # Indica cuándo reintentar.

    def test_public_catalog_returns_500_when_capacity_read_sql_error_is_permanent(self) -> None:
        """Un error SQLite no transitorio al leer cupos conserva el 500 previsto."""
        self._create_national_package(409)  # Garantiza al menos un paquete en el catálogo público.
        with patch.object(self.app.state.compra_service, "get_available_capacity", side_effect=sqlite3.OperationalError("near 'BROKEN': syntax error")):  # Simula un error que no es lock.
            response = self.client.get("/paquetes")  # Invoca el catálogo público real.
        self.assertEqual(response.status_code, 500, response.text)  # Un error permanente no es indisponibilidad.
        self.assertEqual(response.json()["detail"], "No se pudo leer la disponibilidad del paquete.")  # Mensaje estable sin SQL.


if __name__ == "__main__":  # Permite ejecutar este módulo de forma directa.
    unittest.main()  # Descubre y ejecuta los casos definidos en la clase.
