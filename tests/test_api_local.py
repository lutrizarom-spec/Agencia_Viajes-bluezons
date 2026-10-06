"""Pruebas de integración locales para API, autenticación y compra protegida."""

import concurrent.futures  # Ejecuta compras paralelas para comprobar que no se sobrevende inventario.
import sqlite3  # Comprueba persistencia de contraseñas y prepara paquetes de prueba.
import tempfile  # Mantiene cada escenario aislado en una base SQLite descartable.
import unittest  # Ejecuta aserciones de integración sin servicios externos.
from datetime import date  # Configura cupos y reservas para una fecha de viaje concreta.
from contextlib import closing  # Cierra conexiones de fixture en Windows al finalizar cada preparación.
from pathlib import Path  # Construye rutas portables para la base temporal.

from fastapi.testclient import TestClient  # Envía solicitudes ASGI dentro del mismo proceso, sin abrir puertos.

from dao.paquete_dao import PaqueteDao  # Inserta paquetes usando el DAO real que usa la aplicación.
from main_api import create_app  # Construye la API local con servicios configurables para las pruebas.
from model.paquete_internacional import Paquete_Internacional  # Crea un paquete cuya tarifa usa la cotización FX.
from model.paquete_nacional import Paquete_Nacional  # Crea un paquete con precio determinista para verificar el recibo.
from services.auth_service import UserRole  # Aprovisiona usuarios locales con los roles soportados.
from services.compra_service import InsufficientCapacityError  # Reconoce el rechazo esperado al agotarse el inventario.

TRAVEL_DATE = date(2026, 12, 15)
TRAVEL_DATE_JSON = TRAVEL_DATE.isoformat()


class LocalApiIntegrationTests(unittest.TestCase):
    """Verifica rutas públicas, JWT, autorización y compra atómica."""

    def setUp(self) -> None:
        """Crea una API temporal y aprovisiona cuentas para cada caso."""
        self.temp_directory = tempfile.TemporaryDirectory()  # Aísla las tablas de cada prueba y evita tocar agencia.db.
        self.addCleanup(self.temp_directory.cleanup)  # Elimina la carpeta temporal después de cerrar el cliente.
        self.database_path = Path(self.temp_directory.name) / "api-test.db"  # Define la ubicación de SQLite de la prueba.
        self.app = create_app(  # Construye la aplicación con secretos y proveedor explícitos, sin depender del entorno.
            self.database_path,  # Comparte una única base temporal entre API y servicios.
            jwt_secret="local-api-test-secret-that-is-at-least-32-bytes",  # Usa un secreto exclusivo de test, nunca de producción.
            fx_provider=lambda: 987.65,  # Simula el proveedor externo para que las pruebas no requieran red.
        )  # Prepara catálogo, autenticación, rate limiter, FX y compra.
        self.client = TestClient(self.app)  # Crea cliente ASGI para llamar endpoints de forma integrada.
        self.addCleanup(self.client.close)  # Cierra el cliente antes de borrar la base temporal.
        auth = self.app.state.auth_service  # Recupera el servicio real solo para aprovisionamiento local de fixtures.
        auth.create_user("10.000.013-K", "cliente-seguro-local-2026", UserRole.CLIENTE)  # Registra una cuenta cliente con dígito verificador K.
        auth.create_user("9.876.543-3", "admin-seguro-local-2026", UserRole.ADMINISTRADOR)  # Registra una cuenta admin para la ruta de inventario.
        self._insert_package()  # Inserta un paquete nacional con precio fijo conocido para la prueba de compra.

    def _insert_package(self) -> None:
        """Inserta el paquete de prueba a través de PaqueteDao."""
        with closing(sqlite3.connect(self.database_path)) as connection:  # Abre la misma base y garantiza su cierre explícito.
            with connection:  # Confirma la inserción y revierte si el DAO genera un error.
                PaqueteDao(connection).insertar_paquete(  # Conserva la operación real de DAO y su persistencia.
                    Paquete_Nacional(101, "Escapada local", 3, 50000.0)  # Precio nacional calculable sin API externa.
                )  # Inserta el paquete en el catálogo consultado por GET /paquetes.

    def _login(self, rut: str, password: str) -> str:
        """Autentica por HTTP y devuelve el bearer token de la respuesta."""
        response = self.client.post("/auth/login", json={"rut": rut, "password": password})  # Llama al endpoint público de login.
        self.assertEqual(response.status_code, 200)  # Verifica que el backend aceptó las credenciales provistas.
        self.assertEqual(response.json()["token_type"], "bearer")  # Verifica el esquema de autenticación documentado.
        return response.json()["access_token"]  # Retorna solo el token para usarlo en los siguientes requests.

    def test_login_normalizes_rut_and_returns_jwt(self) -> None:
        """El login admite formatos del mismo RUT y entrega un token utilizable."""
        token = self._login("10000013-k", "cliente-seguro-local-2026")  # Usa formato compacto y k minúscula para una cuenta con puntuación.
        response = self.client.post(  # Llama una ruta protegida para verificar el JWT retornado por login.
            "/reservas",  # Reservar sin cupos configurados falla por negocio, no por autenticación.
            json={"package_code": 101, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Envía el contrato JSON de reserva.
            headers={"Authorization": f"Bearer {token}"},  # Presenta el JWT emitido por el endpoint.
        )  # Ejecuta la solicitud usando la credencial recién obtenida.
        self.assertEqual(response.status_code, 409)  # El token fue aceptado y el conflicto corresponde al inventario aún no configurado.

    def test_invalid_credentials_do_not_reveal_account_existence(self) -> None:
        """RUT y contraseña incorrectos reciben el mismo error genérico."""
        response = self.client.post(  # Envía una contraseña incorrecta para una cuenta existente.
            "/auth/login",  # Usa el endpoint de autenticación real.
            json={"rut": "10.000.013-K", "password": "clave-incorrecta"},  # El RUT es válido, la clave no coincide.
        )  # Ejecuta bcrypt y la respuesta de login.
        self.assertEqual(response.status_code, 401)  # Credenciales inválidas nunca generan token.
        self.assertEqual(response.json()["detail"], "RUT o contraseña incorrectos.")  # No revela si el usuario existe.

    def test_login_rate_limit_blocks_the_sixth_attempt(self) -> None:
        """El limitador conectado a login responde 429 tras cinco intentos."""
        for _ in range(5):  # Consume el cupo de cinco intentos configurado por RUT y ventana.
            response = self.client.post(  # Usa la ruta real para comprobar su integración con RateLimiter.
                "/auth/login",  # Todos los intentos se asocian al mismo RUT.
                json={"rut": "10.000.013-K", "password": "clave-incorrecta"},  # Credencial fallida sin emitir token.
            )  # Ejecuta el intento y actualiza el bucket persistente.
            self.assertEqual(response.status_code, 401)  # Los cinco primeros errores siguen siendo fallos de credenciales.

        blocked = self.client.post(  # Repite el intento antes de que venza el minuto.
            "/auth/login",  # La ruta debe detenerlo antes de invocar bcrypt.
            json={"rut": "10000013k", "password": "cliente-seguro-local-2026"},  # Mismo RUT en formato distinto para evitar bypass por formato.
        )  # El sexto request llega al RateLimiter con el mismo identificador normalizado.
        self.assertEqual(blocked.status_code, 429)  # El backend aplica el límite de cinco por minuto.

    def test_package_catalog_is_public_and_uses_existing_price_rule(self) -> None:
        """El catálogo puede consultarse sin token y conserva el cálculo nacional."""
        response = self.client.get("/paquetes")  # Consulta el catálogo por el endpoint público.
        self.assertEqual(response.status_code, 200)  # La ruta pública no exige Authorization.
        self.assertEqual(response.json()[0]["precio_por_persona"], 50000.0)  # Confirma que el precio proviene del modelo nacional existente.
        self.assertIsNone(response.json()[0]["cupos_disponibles"])  # Aún no hay disponibilidad hasta configurarla como administrador.

    def test_reservation_requires_a_valid_bearer_token(self) -> None:
        """La ruta de compra rechaza solicitudes anónimas y tokens inválidos."""
        payload = {"package_code": 101, "quantity": 1}  # Define el cuerpo mínimo válido de la solicitud.
        missing_token = self.client.post("/reservas", json=payload)  # Omite deliberadamente el header Authorization.
        invalid_token = self.client.post(  # Envía un bearer que no fue firmado por el servicio.
            "/reservas", json=payload, headers={"Authorization": "Bearer invalid-token"}
        )  # La dependencia de seguridad debe detener el flujo antes de comprar.
        self.assertEqual(missing_token.status_code, 401)  # Una reserva anónima no puede ser procesada.
        self.assertEqual(invalid_token.status_code, 401)  # Un JWT mal formado tampoco puede acceder.

    def test_administrator_configures_capacity_and_purchase_is_atomic(self) -> None:
        """Un admin configura cupos y la compra impide vender más que la capacidad."""
        admin_token = self._login("9.876.543-3", "admin-seguro-local-2026")  # Obtiene un JWT con rol administrador.
        client_token = self._login("10.000.013-k", "cliente-seguro-local-2026")  # Comprueba además que k minúscula se normaliza.
        inventory_response = self.client.put(  # Configura tres cupos antes de aceptar reservas.
            "/admin/paquetes/101/inventario",  # Ruta administrativa protegida por rol.
            json={"total_capacity": 3, "travel_date": TRAVEL_DATE_JSON},  # Declara tres cupos vendibles para este paquete y fecha.
            headers={"Authorization": f"Bearer {admin_token}"},  # Presenta el JWT del administrador.
        )  # Guarda capacidad en la tabla local de inventario.
        self.assertEqual(inventory_response.status_code, 200)  # Un admin autenticado puede preparar el inventario.
        self.assertEqual(inventory_response.json()["available_capacity"], 3)  # La capacidad completa está disponible inicialmente.

        purchase = self.client.post(  # Consume dos cupos en una sola transacción.
            "/reservas",  # Ejecuta CompraService detrás de la ruta protegida.
            json={"package_code": 101, "quantity": 2, "travel_date": TRAVEL_DATE_JSON},  # Solicita dos plazas del paquete.
            headers={"Authorization": f"Bearer {client_token}"},  # La reserva queda asociada al RUT del cliente del token.
        )  # Persiste recibo y descuento de capacidad atómicamente.
        self.assertEqual(purchase.status_code, 201)  # Confirma una reserva recién creada.
        self.assertEqual(purchase.json()["rut"], "10000013-K")  # Confirma que el RUT se toma del token y está normalizado.
        self.assertEqual(purchase.json()["total_price"], 100000.0)  # Dos cupos aplican el precio nacional de 50000 por persona.
        self.assertEqual(purchase.json()["payment_status"], "pending")  # La nueva reserva retiene cupos mientras espera el pago local.

        excess_purchase = self.client.post(  # Intenta reservar dos plazas cuando queda solo una.
            "/reservas",  # El inventario insuficiente debe ser rechazado por el servicio de compra.
            json={"package_code": 101, "quantity": 2, "travel_date": TRAVEL_DATE_JSON},  # Excede la capacidad remanente.
            headers={"Authorization": f"Bearer {client_token}"},  # Mantiene la autenticación válida para aislar la regla de inventario.
        )  # Ejecuta la segunda solicitud tras la compra confirmada.
        self.assertEqual(excess_purchase.status_code, 409)  # No permite sobreventa.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # Verifica que el rechazo no descontó el último cupo.

    def test_client_cannot_configure_inventory(self) -> None:
        """Un cliente autenticado no adquiere permisos administrativos."""
        client_token = self._login("10.000.013-K", "cliente-seguro-local-2026")  # Obtiene un JWT de rol cliente.
        response = self.client.put(  # Intenta llamar a la operación administrativa.
            "/admin/paquetes/101/inventario",  # La ruta requiere rol administrador.
            json={"total_capacity": 10, "travel_date": TRAVEL_DATE_JSON},  # Solicita capacidad para un paquete existente y fecha.
            headers={"Authorization": f"Bearer {client_token}"},  # Presenta una identidad válida pero de menor privilegio.
        )  # La dependencia de rol debe denegar la operación.
        self.assertEqual(response.status_code, 403)  # Distingue falta de permisos de falta de autenticación.

    def test_concurrent_purchases_cannot_oversell_last_capacity(self) -> None:
        """Varias compras simultáneas compiten por el último cupo sin duplicarlo."""
        compra_service = self.app.state.compra_service  # Obtiene la instancia real de CompraService usada por la aplicación.
        compra_service.configure_capacity(101, TRAVEL_DATE, 1)  # Deja un único cupo disponible antes de iniciar los hilos.

        def attempt_purchase(_: int) -> bool:
            """Retorna True solo para la solicitud que logra confirmar el último cupo."""
            try:  # Permite que las solicitudes perdedoras reporten el conflicto de inventario esperado.
                compra_service.purchase("10.000.013-K", 101, 1, travel_date=TRAVEL_DATE)  # Todas las operaciones compiten por la misma fecha.
            except InsufficientCapacityError:  # Las solicitudes que llegan después del primer commit ya no encuentran cupos.
                return False  # Expone resultado denegado para comparar las compras confirmadas.
            return True  # Solo una transacción debe consumir el cupo disponible.

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:  # Simula ocho compradores en paralelo.
            outcomes = list(executor.map(attempt_purchase, range(8)))  # Espera a que cada operación independiente termine.
        self.assertEqual(sum(outcomes), 1)  # Comprueba que exactamente una transacción confirmó la última plaza.
        self.assertEqual(compra_service.get_available_capacity(101), 0)  # Confirma que el inventario no quedó negativo ni duplicado.

    def test_exchange_rate_endpoint_uses_injected_local_provider(self) -> None:
        """La ruta FX usa el proveedor inyectado y no necesita conectividad."""
        response = self.client.get("/tipo-cambio")  # Consulta la ruta pública de tasa de cambio.
        self.assertEqual(response.status_code, 200)  # El proveedor simulado devuelve una cotización válida.
        self.assertEqual(response.json(), {"base_currency": "USD", "target_currency": "CLP", "rate": 987.65})  # Verifica contrato y tasa devuelta.

    def test_international_reservation_freezes_fx_rate_in_price_and_database(self) -> None:
        """La reserva calcula y persiste la misma cotización USD/CLP obtenida por FxService."""
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                PaqueteDao(connection).insertar_paquete(
                    Paquete_Internacional(102, "Ruta internacional", 5, 100.0, True)
                )
        admin_headers = {"Authorization": f"Bearer {self._login('9.876.543-3', 'admin-seguro-local-2026')}"}
        inventory = self.client.put(
            "/admin/paquetes/102/inventario",
            json={"total_capacity": 2, "travel_date": TRAVEL_DATE_JSON},
            headers=admin_headers,
        )
        self.assertEqual(inventory.status_code, 200, inventory.text)
        purchase = self.client.post(
            "/reservas",
            json={"package_code": 102, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
            headers={"Authorization": f"Bearer {self._login('10.000.013-K', 'cliente-seguro-local-2026')}"},
        )
        self.assertEqual(purchase.status_code, 201, purchase.text)
        self.assertEqual(purchase.json()["exchange_rate_applied"], 987.65)
        self.assertEqual(purchase.json()["unit_price"], 98765.0)
        with closing(sqlite3.connect(self.database_path)) as connection:
            persisted = connection.execute(
                "SELECT exchange_rate_applied, unit_price, travel_date FROM reservas WHERE reservation_id = ?",
                (purchase.json()["reservation_id"],),
            ).fetchone()
        self.assertEqual(persisted, (987.65, 98765.0, TRAVEL_DATE_JSON))

    def test_capacity_is_independent_for_each_travel_date(self) -> None:
        """Un descuento de una fecha no reduce la disponibilidad de otro día."""
        other_date = date(2026, 12, 16)
        admin_headers = {"Authorization": f"Bearer {self._login('9.876.543-3', 'admin-seguro-local-2026')}"}
        for travel_date, capacity in ((TRAVEL_DATE, 1), (other_date, 2)):
            response = self.client.put(
                "/admin/paquetes/101/inventario",
                json={"total_capacity": capacity, "travel_date": travel_date.isoformat()},
                headers=admin_headers,
            )
            self.assertEqual(response.status_code, 200, response.text)
        purchase = self.client.post(
            "/reservas",
            json={"package_code": 101, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
            headers={"Authorization": f"Bearer {self._login('10.000.013-K', 'cliente-seguro-local-2026')}"},
        )
        self.assertEqual(purchase.status_code, 201, purchase.text)
        first_date = self.client.get(f"/paquetes?travel_date={TRAVEL_DATE_JSON}").json()[0]
        second_date = self.client.get(f"/paquetes?travel_date={other_date.isoformat()}").json()[0]
        self.assertEqual(first_date["cupos_disponibles"], 0)
        self.assertEqual(second_date["cupos_disponibles"], 2)

    def test_openapi_contains_requested_routes(self) -> None:
        """FastAPI publica los contratos de rutas y métodos configurados."""
        schema = self.app.openapi()  # Genera el OpenAPI que consume Swagger UI y otros clientes.
        paths = schema["paths"]  # Extrae el mapa público de rutas documentadas.
        self.assertIn("/auth/login", paths)  # Documenta autenticación por RUT.
        self.assertIn("/paquetes", paths)  # Documenta consulta pública de catálogo.
        self.assertIn("/reservas", paths)  # Documenta compra protegida.
        self.assertIn("/reservas/{reservation_id}/pago", paths)  # Documenta la consulta del estado de pago del propietario.
        self.assertIn("/admin/reservas/{reservation_id}/pago", paths)  # Documenta la simulación administrativa de resultados de pago.
        self.assertIn("/tipo-cambio", paths)  # Documenta consulta del servicio FX.
        self.assertIn("/admin/paquetes/{package_code}/inventario", paths)  # Documenta la gestión de cupos para administradores.


if __name__ == "__main__":  # Permite ejecutar estas pruebas de integración directamente.
    unittest.main()  # Descubre y ejecuta los escenarios declarados en esta clase.
