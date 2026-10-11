"""Casos límite de autenticación, idempotencia y persistencia transaccional."""

import concurrent.futures  # Compite varias solicitudes idempotentes sobre la misma clave.
import sqlite3  # Cuenta reservas y verifica que el reintento no descuente de nuevo.
import tempfile  # Usa una base desechable sin modificar los datos locales reales.
import time  # Espera brevemente para verificar el worker periódico de FastAPI.
import unittest  # Ejecuta pruebas de integración con aserciones estándar.
from contextlib import closing  # Cierra conexiones para permitir eliminar la base en Windows.
from datetime import UTC, date, datetime, timedelta  # Construye fecha de viaje y claims JWT de prueba.
from pathlib import Path  # Construye rutas para la base temporal.
from unittest.mock import patch  # Simula bloqueo SQLite y controla pausas de retry.

import jwt  # Firma un JWT de prueba cuyo vencimiento está en el pasado.
from fastapi.testclient import TestClient  # Invoca FastAPI dentro del proceso, sin abrir un servidor de red.

from dao.paquete_dao import PaqueteDao  # Persiste el paquete con el DAO de la aplicación.
from main_api import create_app  # Construye la API y los servicios locales para cada escenario.
from model.paquete_nacional import Paquete_Nacional  # Usa precio determinista sin modificar fórmulas.
from services.auth_service import UserRole  # Crea cuentas cliente/admin con roles reales.
from services.compra_service import (  # Accede a la compra y a los errores transitorios de SQLite.
    PAYMENT_PENDING_TTL_SECONDS,
    CompraService,
    DatabaseBusyError,
    PaymentTransitionConflictError,
    PurchasePersistenceError,
)

TRAVEL_DATE = date(2026, 12, 15)
TRAVEL_DATE_JSON = TRAVEL_DATE.isoformat()


class ApiEdgeCaseTests(unittest.TestCase):
    """Comprueba bordes que suelen producir reintentos o condiciones de carrera."""

    def setUp(self) -> None:
        """Inicializa una base aislada, usuarios, catálogo y cliente HTTP."""
        self.temp_directory = tempfile.TemporaryDirectory()  # Crea almacenamiento temporal propio para esta prueba.
        self.addCleanup(self.temp_directory.cleanup)  # Elimina los archivos temporales tras cerrar conexiones/cliente.
        self.database_path = Path(self.temp_directory.name) / "edge-cases.db"  # Define una base SQLite persistente entre reinicios simulados.
        self.jwt_secret = "test-edge-secret-which-is-at-least-32-bytes"  # Clave de prueba distinta de cualquier secreto de producción.
        self.app = create_app(  # Construye aplicación real con dependencias locales controladas.
            self.database_path,  # Comparte esta base entre API y servicios.
            jwt_secret=self.jwt_secret,  # Configura firma JWT predecible para fabricar el caso vencido.
            fx_provider=lambda: 900.0,  # No realiza solicitudes externas al probar rutas no FX.
        )  # Deja creadas las tablas del esquema actual.
        self.client = TestClient(self.app)  # Crea el cliente de integración sin servidor externo.
        self.addCleanup(self.client.close)  # Cierra TestClient antes de borrar su base.
        self.app.state.auth_service.create_user(  # Aprovisiona un usuario cliente sin endpoint público de registro.
            "10.000.013-K", "cliente-seguro-local-2026", UserRole.CLIENTE
        )  # Guarda RUT normalizado y hash bcrypt.
        self.app.state.auth_service.create_user(  # Aprovisiona el rol requerido para configurar inventario.
            "9.876.543-3", "admin-seguro-local-2026", UserRole.ADMINISTRADOR
        )  # Permite probar permisos administrativos en escenarios de inventario.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Inserta el paquete de prueba con conexión cerrada explícitamente.
            with connection:  # Confirma la transacción del fixture.
                PaqueteDao(connection).insertar_paquete(  # Reutiliza la operación DAO del catálogo.
                    Paquete_Nacional(202, "Paquete de borde", 2, 40000.0)  # Define una tarifa estable para aserciones.
                )  # Deja el paquete visible para catálogo, capacidad y compra.

    def _login(self, rut: str, password: str) -> str:
        """Autentica por HTTP y retorna el JWT emitido."""
        response = self.client.post(  # Usa la ruta real en lugar de llamar directo al servicio.
            "/auth/login", json={"rut": rut, "password": password}
        )  # Ejecuta bcrypt, rate limiter y emisión JWT.
        self.assertEqual(response.status_code, 200)  # Asegura que el fixture de credenciales está activo.
        return response.json()["access_token"]  # Entrega el Bearer token a la prueba llamadora.

    def _admin_headers(self) -> dict[str, str]:
        """Construye Authorization con una cuenta administrativa válida."""
        token = self._login("9.876.543-3", "admin-seguro-local-2026")  # Solicita token con rol administrador.
        return {"Authorization": f"Bearer {token}"}  # Devuelve el encabezado HTTP estándar.

    def _client_headers(self) -> dict[str, str]:
        """Construye Authorization con una cuenta de cliente válida."""
        token = self._login("10.000.013-K", "cliente-seguro-local-2026")  # Solicita token con rol cliente.
        return {"Authorization": f"Bearer {token}"}  # Devuelve la credencial para rutas protegidas.

    def test_expired_jwt_is_rejected_by_reservation_route(self) -> None:
        """Un JWT firmado correctamente pero vencido no autoriza una compra."""
        issued_at = datetime.now(UTC) - timedelta(hours=2)  # Sitúa la emisión antes de la fecha actual.
        expired_token = jwt.encode(  # Firma el token con el secreto válido para aislar específicamente la expiración.
            {
                "sub": "10000013-K",  # Incluye el RUT normalizado de una cuenta válida.
                "role": "cliente",  # Usa un rol permitido para no fallar por autorización.
                "iat": issued_at,  # Marca emisión en el pasado.
                "exp": issued_at + timedelta(minutes=1),  # Hace que la expiración también quede en el pasado.
            },
            self.jwt_secret,  # Usa la misma clave que valida la instancia de API.
            algorithm="HS256",  # Usa el algoritmo permitido por AuthService.
        )  # Obtiene un JWT auténtico pero fuera de su período de validez.
        response = self.client.post(  # Intenta reservar con el token expirado.
            "/reservas",  # La dependencia Bearer debe comprobar exp antes de la lógica de compra.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Envía un cuerpo válido para aislar la autenticación.
            headers={"Authorization": f"Bearer {expired_token}"},  # Presenta el token vencido.
        )  # Ejecuta la ruta protegida.
        self.assertEqual(response.status_code, 401)  # Rechaza la identidad sin tocar el inventario.

    def test_invalid_rut_format_and_verifier_are_rejected_at_login(self) -> None:
        """Login niega RUT mal formado y RUT con dígito verificador incorrecto."""
        invalid_ruts = ("12x45678-5", "10.000.013-1")  # Incluye un formato no numérico y un dígito verificador inválido.
        for invalid_rut in invalid_ruts:  # Repite la verificación para ambas clases de error de entrada.
            with self.subTest(rut=invalid_rut):  # Identifica en el reporte qué RUT inválido se está comprobando.
                response = self.client.post(  # Llama a la ruta pública con una contraseña cualquiera.
                    "/auth/login",  # La normalización ocurre dentro del servicio de autenticación.
                    json={"rut": invalid_rut, "password": "clave-no-valida"},  # No reutiliza la contraseña de una cuenta válida.
                )  # Solicita autenticación sin permitir que la entrada llegue a crear una identidad.
                self.assertEqual(response.status_code, 401)  # Devuelve error genérico de credenciales y no acepta el RUT.
                self.assertEqual(response.json()["detail"], "RUT o contraseña incorrectos.")  # No filtra detalles de validación al endpoint público.

    def test_zero_capacity_returns_conflict_without_creating_reservation(self) -> None:
        """Un inventario configurado explícitamente en cero no permite comprar."""
        self.client.put(  # Configura el paquete existente con cero cupos.
            "/admin/paquetes/202/inventario",  # Usa ruta protegida de administración.
            json={"total_capacity": 0, "travel_date": TRAVEL_DATE_JSON},  # Cero expresa agotado en esta fecha, no inventario desconocido.
            headers=self._admin_headers(),  # Autoriza la configuración con un JWT admin.
        )  # Persiste capacidad cero en package_inventory.
        response = self.client.post(  # Intenta reservar un cupo inexistente.
            "/reservas",  # Usa la ruta de compra protegida.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Pide más de los cero cupos disponibles.
            headers=self._client_headers(),  # Aporta identidad autenticada de cliente.
        )  # Ejecuta la compra que debe detectar falta de capacidad.
        self.assertEqual(response.status_code, 409)  # Informa conflicto de inventario sin reservar parcialmente.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 0)  # El intento rechazado no cambia el inventario.

    def test_repeated_idempotency_key_returns_previous_receipt_without_double_debit(self) -> None:
        """Repetir cliente, clave y cuerpo reproduce el recibo y consume cupos una vez."""
        self.client.put(  # Abre capacidad para completar la compra inicial.
            "/admin/paquetes/202/inventario",  # Configura inventario usando la API administrativa.
            json={"total_capacity": 3, "travel_date": TRAVEL_DATE_JSON},  # Provee cupos suficientes para detectar cualquier doble descuento.
            headers=self._admin_headers(),  # Permite solo al admin gestionar capacidad.
        )  # Persisten los tres cupos iniciales.
        authorization = self._client_headers()  # Usa la misma identidad para las dos solicitudes idempotentes.
        headers = {**authorization, "X-Idempotency-Key": "checkout-edge-key-001"}  # Adjunta una clave estable del cliente.
        body = {"package_code": 202, "quantity": 2, "travel_date": TRAVEL_DATE_JSON}  # Fija el paquete, cantidad y fecha asociados con esa clave.

        first = self.client.post("/reservas", json=body, headers=headers)  # Crea y confirma la primera reserva.
        replay = self.client.post("/reservas", json=body, headers=headers)  # Simula reintento al perderse la primera respuesta.
        self.assertEqual(first.status_code, 201)  # La primera ejecución produce un recurso nuevo.
        self.assertEqual(replay.status_code, 200)  # La repetición informa que se devolvió un resultado existente.
        self.assertEqual(first.json(), replay.json())  # La repetición devuelve exactamente el recibo originalmente confirmado.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # Solo dos de tres cupos fueron descontados una vez.

        with closing(sqlite3.connect(self.database_path)) as connection:  # Inspecciona la base de prueba sin dejar conexiones abiertas.
            count = connection.execute(  # Cuenta recibos del usuario y paquete de este caso.
                "SELECT COUNT(*) FROM reservas WHERE rut = ? AND package_code = ?",
                ("10000013-K", 202),
            ).fetchone()[0]  # Extrae el único valor agregado.
        self.assertEqual(count, 1)  # Confirma que el reintento no insertó otra reserva.

    def test_customer_can_list_and_cancel_own_reservation_once(self) -> None:
        """El cliente ve solo sus reservas y cancelar libera cupos exactamente una vez."""
        self.client.put(  # Prepara inventario para una compra seguida de cancelación.
            "/admin/paquetes/202/inventario",  # Configura capacidad mediante la ruta administrativa.
            json={"total_capacity": 3, "travel_date": TRAVEL_DATE_JSON},  # Reserva dos cupos y deja uno libre inicialmente.
            headers=self._admin_headers(),  # Autoriza la configuración con rol administrador.
        )  # Persiste los tres cupos disponibles.
        purchase = self.client.post(  # Crea una reserva del cliente de prueba.
            "/reservas",  # Ejecuta la ruta protegida de compra.
            json={"package_code": 202, "quantity": 2, "travel_date": TRAVEL_DATE_JSON},  # Consume dos de los tres cupos.
            headers=self._client_headers(),  # Asocia la compra al RUT del token.
        )  # Registra la reserva y obtiene su identificador.
        self.assertEqual(purchase.status_code, 201)  # Verifica que la compra inicial quedó confirmada.
        reservation_id = purchase.json()["reservation_id"]  # Captura el UUID que se usará al listar y cancelar.

        own_reservations = self.client.get("/reservas", headers=self._client_headers())  # Consulta el listado derivado de la identidad autenticada.
        self.assertEqual(own_reservations.status_code, 200)  # Permite al cliente consultar su historial.
        self.assertEqual(len(own_reservations.json()), 1)  # Devuelve solo la reserva existente del cliente.
        self.assertEqual(own_reservations.json()[0]["status"], "confirmed")  # Expone el estado de la reserva aún activa.
        self.assertEqual(own_reservations.json()[0]["payment_status"], "pending")  # La reserva retiene cupos mientras espera simulación de pago.

        cancelled = self.client.post(  # Solicita la primera transición a estado cancelado.
            f"/reservas/{reservation_id}/cancelar",  # Identifica explícitamente la reserva.
            headers=self._client_headers(),  # Solo permite cancelar si el RUT es propietario.
        )  # El servicio cambia estado y libera cupos en la misma transacción.
        repeated = self.client.post(  # Repite la cancelación como reintento seguro de cliente.
            f"/reservas/{reservation_id}/cancelar",  # Envía nuevamente el mismo identificador.
            headers=self._client_headers(),  # Mantiene la identidad propietaria.
        )  # Devuelve el estado cancelado ya persistido sin tocar inventario.
        self.assertEqual(cancelled.status_code, 200)  # La cancelación queda aceptada.
        self.assertEqual(cancelled.json()["status"], "cancelled")  # El recibo refleja la transición completada.
        self.assertIsNotNone(cancelled.json()["cancelled_at"])  # Se conserva un timestamp UTC de cancelación.
        self.assertEqual(repeated.json(), cancelled.json())  # La operación repetida devuelve exactamente el mismo resultado.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 3)  # La cancelación libera los dos cupos una sola vez.

    def test_customer_cannot_cancel_other_customers_reservation(self) -> None:
        """Un cliente no puede enumerar ni cancelar reservas de otra identidad."""
        self.app.state.auth_service.create_user(  # Crea un segundo cliente válido para comprobar aislamiento entre cuentas.
            "11.111.111-1", "segundo-cliente-seguro-2026", UserRole.CLIENTE
        )  # Persiste la cuenta con un RUT válido distinto al titular inicial.
        self.client.put(  # Habilita inventario para que el segundo usuario pueda reservar.
            "/admin/paquetes/202/inventario",  # Reutiliza la operación administrativa de capacidad.
            json={"total_capacity": 2, "travel_date": TRAVEL_DATE_JSON},  # Deja capacidad suficiente para una reserva.
            headers=self._admin_headers(),  # Autoriza al rol administrador.
        )  # La capacidad se guarda para ambos clientes.
        second_login = self.client.post(  # Obtiene un token del segundo cliente.
            "/auth/login",  # Emite JWT a través de la ruta normal.
            json={"rut": "11.111.111-1", "password": "segundo-cliente-seguro-2026"},  # Usa sus credenciales locales.
        )  # Autentica al propietario de la reserva ajena al cliente inicial.
        second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}  # Construye credenciales del segundo cliente.
        purchase = self.client.post(  # Crea reserva en nombre del segundo cliente.
            "/reservas",  # La ruta toma el RUT desde el JWT.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Reserva un cupo para fecha y paquete configurados.
            headers=second_headers,  # Asocia el recibo al segundo cliente.
        )  # Obtiene el identificador cuya privacidad se probará.
        reservation_id = purchase.json()["reservation_id"]  # Conserva UUID válido de una reserva ajena.

        own_list = self.client.get("/reservas", headers=self._client_headers())  # Consulta usando el primer cliente del fixture.
        forbidden_cancel = self.client.post(  # Trata de cancelar la reserva del segundo cliente.
            f"/reservas/{reservation_id}/cancelar",  # Usa un UUID real para verificar control de propiedad.
            headers=self._client_headers(),  # Presenta la identidad que no es propietaria.
        )  # El servicio oculta la existencia de reservas ajenas.
        self.assertEqual(own_list.status_code, 200)  # La consulta propia sigue siendo accesible.
        self.assertEqual(own_list.json(), [])  # No revela el recibo del otro cliente.
        self.assertEqual(forbidden_cancel.status_code, 404)  # Informa igual que para una reserva inexistente.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # El intento ajeno no libera cupos.
        hidden_payment = self.client.get(f"/reservas/{reservation_id}/pago", headers=self._client_headers())  # Comprueba que el estado de pago también respeta la propiedad.
        self.assertEqual(hidden_payment.status_code, 404)  # Evita revelar pagos de otros clientes.

    def test_outbox_operational_routes_are_admin_only(self) -> None:
        customer_list = self.client.get("/admin/outbox", headers=self._client_headers())
        self.assertEqual(customer_list.status_code, 403)
        admin_list = self.client.get("/admin/outbox", headers=self._admin_headers())
        self.assertEqual(admin_list.status_code, 200)
        self.assertEqual(admin_list.json(), [])
        self.assertEqual(
            self.client.post(
                "/admin/outbox/1/retry", headers=self._admin_headers()
            ).status_code,
            404,
        )

    def test_cannot_cancel_reservation_with_confirmed_payment(self) -> None:
        self.client.put(
            "/admin/paquetes/202/inventario",
            json={"total_capacity": 2, "travel_date": TRAVEL_DATE_JSON},
            headers=self._admin_headers(),
        )
        purchase = self.client.post(
            "/reservas",
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
            headers=self._client_headers(),
        )
        reservation_id = purchase.json()["reservation_id"]
        self.app.state.compra_service.transition_payment(reservation_id, "confirmed")

        cancellation = self.client.post(
            f"/reservas/{reservation_id}/cancelar",
            headers=self._client_headers(),
        )

        self.assertEqual(cancellation.status_code, 409)
        self.assertIn("sin un flujo de reembolso", cancellation.json()["detail"])
        reservation = self.client.get(
            "/reservas", headers=self._client_headers()
        ).json()[0]
        self.assertEqual(reservation["status"], "confirmed")
        self.assertEqual(reservation["payment_status"], "confirmed")
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)

    def test_payment_stays_pending_until_admin_confirms_and_replays_safely(self) -> None:
        """La compra retiene cupos; solo el admin confirma pago y los retries no duplican efectos."""
        self.client.put(  # Prepara dos plazas antes de crear la reserva pendiente de pago.
            "/admin/paquetes/202/inventario",  # Usa la ruta normal de inventario.
            json={"total_capacity": 2, "travel_date": TRAVEL_DATE_JSON},  # Un cupo se retendrá y uno quedará disponible.
            headers=self._admin_headers(),  # Autoriza el cambio como administrador.
        )  # Persiste inventario previo a la solicitud del cliente.
        client_headers = self._client_headers()  # Conserva la identidad propietaria para consultar el pago.
        purchase = self.client.post(  # Crea reserva y pago local pendiente en una sola operación.
            "/reservas",  # Usa la API integrada, no una llamada directa al servicio.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Solicita un cupo para el día indicado.
            headers={**client_headers, "X-Idempotency-Key": "payment-confirm-key"},  # Asocia reserva y pago al RUT autenticado y permite comprobar replay.
        )  # Retiene capacidad antes de conocer el resultado simulado.
        reservation_id = purchase.json()["reservation_id"]  # Obtiene el identificador para consultar y resolver el pago.
        self.assertEqual(purchase.status_code, 201)  # La reserva queda creada aunque el pago siga pendiente.
        self.assertEqual(purchase.json()["payment_status"], "pending")  # Comunica claramente el estado de pago inicial.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # Mantiene el cupo retenido.
        pending_payment = self.client.get(f"/reservas/{reservation_id}/pago", headers=client_headers)  # Permite al dueño consultar su estado.
        self.assertEqual(pending_payment.status_code, 200)  # El propietario puede consultar el pago.
        self.assertEqual(pending_payment.json()["status"], "pending")  # El resultado todavía no fue simulado.

        unauthorized = self.client.post(  # Comprueba que el cliente no pueda declararse pagado.
            f"/admin/reservas/{reservation_id}/pago",  # Ruta reservada a simulación administrativa.
            json={"status": "confirmed"},  # Solicita una confirmación no autorizada.
            headers=client_headers,  # Usa token de cliente.
        )  # La dependencia de rol debe rechazar antes de cambiar el pago.
        self.assertEqual(unauthorized.status_code, 403)  # Solo administración puede simular el resultado.

        admin_headers = self._admin_headers()  # Reutiliza una sesión administrativa dentro del límite de autenticación.
        confirmation = self.client.post(  # Simula una confirmación de proveedor local.
            f"/admin/reservas/{reservation_id}/pago",  # Resuelve el pago pendiente asociado.
            json={"status": "confirmed"},  # Marca resultado como confirmado, sin ejecutar un cobro real.
            headers=admin_headers,  # Presenta rol administrador.
        )  # La confirmación se persiste junto a la reserva.
        replay = self.client.post(  # Repite la misma transición por un posible timeout del cliente.
            f"/admin/reservas/{reservation_id}/pago",  # Reenvía el mismo resultado terminal.
            json={"status": "confirmed"},  # Mantiene la misma decisión.
            headers=admin_headers,  # Reutiliza el token ya validado.
        )  # La repetición no modifica el importe ni el inventario.
        self.assertEqual(confirmation.status_code, 200)  # Acepta la transición pendiente→confirmado.
        self.assertEqual(confirmation.json()["status"], "confirmed")  # Devuelve estado persistido.
        self.assertEqual(replay.json(), confirmation.json())  # El resultado repetido es estable e idempotente.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # Confirmar pago conserva la reserva de capacidad.
        purchase_replay = self.client.post(  # Reenvía la compra original después de que el estado de pago cambió.
            "/reservas",  # La búsqueda idempotente debe reflejar el estado actual del pago.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Conserva el cuerpo asociado a la clave.
            headers={**client_headers, "X-Idempotency-Key": "payment-confirm-key"},  # Reutiliza clave y propietario originales.
        )  # No crea otro recibo ni otra reserva de capacidad.
        self.assertEqual(purchase_replay.status_code, 200)  # La repetición devuelve el recurso previo.
        self.assertEqual(purchase_replay.json()["payment_status"], "confirmed")  # El recibo repetido refleja la confirmación más reciente.
        conflict = self.client.post(  # Un pago confirmado no puede cambiar después a fallido.
            f"/admin/reservas/{reservation_id}/pago",  # Intenta una transición opuesta.
            json={"status": "failed"},  # Estado terminal incompatible.
            headers=admin_headers,  # Usa autorización válida para aislar la regla de estado.
        )  # El servicio devuelve un conflicto sin liberar el cupo.
        self.assertEqual(conflict.status_code, 409)  # Protege la transición terminal ya confirmada.

    def test_reservation_accepts_multiple_partial_payments_until_total_is_paid(self) -> None:
        """Un anticipo confirmado admite nuevos abonos hasta completar el saldo."""
        self.client.put(
            "/admin/paquetes/202/inventario",
            json={"total_capacity": 1, "travel_date": TRAVEL_DATE_JSON},
            headers=self._admin_headers(),
        )
        client_headers = self._client_headers()
        admin_headers = self._admin_headers()
        purchase = self.client.post(
            "/reservas",
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
            headers=client_headers,
        )
        self.assertEqual(purchase.status_code, 201, purchase.text)
        receipt = purchase.json()
        initial_payment = self.client.get(
            f"/reservas/{receipt['reservation_id']}/pago",
            headers=client_headers,
        )
        self.assertEqual(initial_payment.status_code, 200, initial_payment.text)
        self.assertEqual(initial_payment.json()["amount"], receipt["total_price"] * 0.5)
        self.assertEqual(receipt["total_paid"], 0.0)

        initial_confirmation = self.client.post(
            f"/admin/reservas/{receipt['reservation_id']}/pago",
            json={"payment_id": receipt["payment_id"], "status": "confirmed"},
            headers=admin_headers,
        )
        self.assertEqual(initial_confirmation.status_code, 200, initial_confirmation.text)

        failed_attempt = self.client.post(
            f"/reservas/{receipt['reservation_id']}/pagos",
            json={"amount": 1000.0},
            headers=client_headers,
        )
        self.assertEqual(failed_attempt.status_code, 201, failed_attempt.text)
        failed_result = self.client.post(
            f"/admin/reservas/{receipt['reservation_id']}/pago",
            json={"payment_id": failed_attempt.json()["payment_id"], "status": "failed"},
            headers=admin_headers,
        )
        self.assertEqual(failed_result.status_code, 200, failed_result.text)
        self.assertEqual(
            self.client.get(f"/paquetes?travel_date={TRAVEL_DATE_JSON}").json()[0]["cupos_disponibles"],
            0,
        )

        for amount in (10000.0, 5000.0, 5000.0):
            payment = self.client.post(
                f"/reservas/{receipt['reservation_id']}/pagos",
                json={"amount": amount},
                headers=client_headers,
            )
            self.assertEqual(payment.status_code, 201, payment.text)
            confirmation = self.client.post(
                f"/admin/reservas/{receipt['reservation_id']}/pago",
                json={"payment_id": payment.json()["payment_id"], "status": "confirmed"},
                headers=admin_headers,
            )
            self.assertEqual(confirmation.status_code, 200, confirmation.text)

        payments = self.client.get(
            f"/reservas/{receipt['reservation_id']}/pagos",
            headers=client_headers,
        )
        self.assertEqual(payments.status_code, 200, payments.text)
        self.assertEqual(len(payments.json()), 5)
        confirmed_total = sum(
            payment["amount"] for payment in payments.json()
            if payment["status"] == "confirmed"
        )
        self.assertEqual(confirmed_total, receipt["total_price"])
        reservations = self.client.get("/reservas", headers=client_headers)
        self.assertEqual(reservations.status_code, 200, reservations.text)
        details = next(
            item for item in reservations.json()
            if item["reservation_id"] == receipt["reservation_id"]
        )
        self.assertEqual(details["total_paid"], receipt["total_price"])
        self.assertEqual(details["balance_due"], 0.0)

    def test_failed_payment_cancels_reservation_and_releases_capacity_once(self) -> None:
        """Un pago fallido cancela la reserva y libera los cupos de forma atómica e idempotente."""
        self.client.put(  # Configura una plaza que quedará retenida temporalmente.
            "/admin/paquetes/202/inventario",  # Prepara inventario para el flujo de pago.
            json={"total_capacity": 1, "travel_date": TRAVEL_DATE_JSON},  # La capacidad debe volver a estar libre tras el fallo.
            headers=self._admin_headers(),  # Autoriza configuración con rol administrador.
        )  # El inventario queda listo.
        purchase = self.client.post(  # Genera pago pendiente asociado a la plaza.
            "/reservas",  # Ejecuta la compra local protegida.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Retiene la única plaza de esa fecha.
            headers={**self._client_headers(), "X-Idempotency-Key": "payment-failure-key"},  # Usa cuenta cliente y conserva clave de operación.
        )  # Crea el recibo de reserva y pago.
        reservation_id = purchase.json()["reservation_id"]  # Conserva el identificador para resolver el pago.
        admin_headers = self._admin_headers()  # Autentica una vez para las transiciones de esta prueba.

        failure = self.client.post(  # Simula que el pago no pudo confirmarse.
            f"/admin/reservas/{reservation_id}/pago",  # Actualiza el pago relacionado con la reserva.
            json={"status": "failed"},  # Selecciona el resultado terminal fallido.
            headers=admin_headers,  # Solo el administrador puede simular el proveedor.
        )  # El pago y la devolución de capacidad se ejecutan en una transacción.
        repeated = self.client.post(  # Repite el resultado por seguridad ante una respuesta perdida.
            f"/admin/reservas/{reservation_id}/pago",  # Usa la misma reserva.
            json={"status": "failed"},  # Conserva el mismo resultado terminal.
            headers=admin_headers,  # Presenta el mismo token de administrador.
        )  # No debe descontar/reponer inventario otra vez.
        reservation = self.client.get("/reservas", headers=self._client_headers()).json()[0]  # Consulta estados que quedaron persistidos.
        self.assertEqual(failure.status_code, 200)  # La transición de pago fallido fue aplicada.
        self.assertEqual(failure.json()["status"], "failed")  # El pago queda marcado como fallido.
        self.assertEqual(repeated.json(), failure.json())  # Repetir el mismo fallo devuelve el registro original.
        self.assertEqual(reservation["status"], "cancelled")  # La reserva se cancela como consecuencia del fallo.
        self.assertEqual(reservation["payment_status"], "failed")  # La lista propia informa ambos estados coherentes.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # La plaza fue liberada exactamente una vez.
        failed_replay = self.client.post(  # Reintenta la compra original después del fallo terminal del pago.
            "/reservas",  # Reproduce el resultado sin volver a retener inventario.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Usa los mismos datos ligados a la clave.
            headers={**self._client_headers(), "X-Idempotency-Key": "payment-failure-key"},  # Mantiene el mismo RUT y clave idempotente.
        )  # El recibo ya existente debe seguir reportando el pago fallido.
        self.assertEqual(failed_replay.status_code, 200)  # Recupera la reserva anterior en vez de crear otra.
        self.assertEqual(failed_replay.json()["payment_status"], "failed")  # Hace visible el resultado terminal a quien repite la compra.
        self.assertEqual(failed_replay.json()["status"], "cancelled")  # Confirma que el fallo liberó y canceló la reserva original.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # El replay tampoco vuelve a alterar los cupos.
        invalid_retry = self.client.post(  # El resultado terminal fallido no se puede revertir a confirmado.
            f"/admin/reservas/{reservation_id}/pago",  # Reutiliza el pago ya resuelto.
            json={"status": "confirmed"},  # Intenta transición no permitida.
            headers=admin_headers,  # Tiene rol suficiente, pero la máquina de estados debe impedirlo.
        )  # No recupera la reserva ni vuelve a retener cupos.
        self.assertEqual(invalid_retry.status_code, 409)  # Evita una reversión inválida desde failed.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 1)  # El conflicto no altera el inventario.

    def test_expiry_releases_only_due_pending_payments_once(self) -> None:
        """El barrido vence pagos atrasados, preserva los confirmados y no libera dos veces."""
        self.client.put(  # Configura dos plazas para comparar pago pendiente y confirmado.
            "/admin/paquetes/202/inventario",  # Usa la API administrativa real.
            json={"total_capacity": 2, "travel_date": TRAVEL_DATE_JSON},  # Una plaza quedará confirmada y otra vencerá.
            headers=self._admin_headers(),  # Autoriza la configuración.
        )  # Persiste inventario.
        client_headers = self._client_headers()  # Conserva las credenciales del mismo cliente.
        confirmed_purchase = self.client.post(  # Crea el pago que después se confirmará.
            "/reservas",  # Hace una compra mediante el endpoint normal.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Retiene una plaza para la fecha.
            headers=client_headers,  # Asocia la compra al titular.
        )  # Devuelve el identificador de reserva y pago.
        pending_purchase = self.client.post(  # Crea otra reserva que quedará pendiente.
            "/reservas",  # Cada solicitud sin clave representa una compra distinta.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Retiene la segunda plaza para la misma fecha.
            headers=client_headers,  # Usa la misma cuenta, con nuevo intento.
        )  # Deja el inventario completamente retenido.
        confirmed_id = confirmed_purchase.json()["reservation_id"]  # Guarda la reserva que no debe vencerse.
        pending_id = pending_purchase.json()["reservation_id"]  # Guarda la reserva que debe expirar.
        self.assertEqual(confirmed_purchase.json()["payment_status"], "pending")  # Comprueba el estado inicial de ambos pagos.
        self.app.state.compra_service.transition_payment(confirmed_id, "confirmed")  # Simula el pago exitoso antes de barrer.

        old_deadline = "2000-01-01T00:00:00+00:00"  # Fuerza un vencimiento pasado de forma determinista, sin esperar quince minutos.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Manipula solo la fecha del fixture dentro de la base temporal.
            with connection:  # Confirma el cambio de reloj de prueba.
                connection.execute(  # Vuelve antiguo el deadline de ambos pagos para verificar su estado.
                    "UPDATE payments SET expires_at = ? WHERE reservation_id IN (?, ?)",
                    (old_deadline, confirmed_id, pending_id),  # La fecha pasada por sí sola no vence pagos ya confirmados.
                )  # La instancia de servicio leerá este deadline durante el siguiente ciclo.

        service = self.app.state.compra_service  # Accede al servicio real usado por la API.
        expired_count = service.expire_pending_payments()  # Ejecuta el barrido como si el worker periódico hubiese despertado.
        self.assertEqual(expired_count, 1)  # Solo vence el pago que seguía pendiente.
        self.assertEqual(service.expire_pending_payments(), 0)  # El siguiente ciclo no repite la transición ni la liberación.
        self.assertEqual(service.get_available_capacity(202), 1)  # La plaza confirmada permanece retenida; la vencida vuelve al inventario.
        pending_reservation = next(  # Obtiene la reserva expirada desde el listado propio.
            receipt for receipt in service.list_reservations("10.000.013-K") if receipt.reservation_id == pending_id
        )  # El resultado incluye el estado actual del pago unido desde SQLite.
        confirmed_reservation = next(  # Recupera el recibo de la compra que sí fue confirmada.
            receipt for receipt in service.list_reservations("10.000.013-K") if receipt.reservation_id == confirmed_id
        )  # Distingue ambos resultados por su identificador persistido.
        self.assertEqual(pending_reservation.status, "cancelled")  # El pago vencido cancela su reserva.
        self.assertEqual(pending_reservation.payment_status, "expired")  # El cliente puede distinguir vencimiento de fallo manual.
        self.assertEqual(confirmed_reservation.status, "confirmed")  # Una reserva pagada no se cancela por deadline antiguo.
        self.assertEqual(confirmed_reservation.payment_status, "confirmed")  # El barrido no altera pagos ya resueltos.
        self.assertEqual(pending_reservation.payment_expires_at, old_deadline)  # Conserva el deadline original como dato auditable del recibo.
        self.assertEqual(PAYMENT_PENDING_TTL_SECONDS, 900)  # Fija la política de producto en quince minutos.
        with self.assertRaises(PaymentTransitionConflictError):  # Un pago vencido no puede confirmarse tarde por una llamada administrativa.
            service.transition_payment(pending_id, "confirmed")  # El servicio conserva su estado expired y la plaza ya liberada.
        self.assertEqual(service.get_available_capacity(202), 1)  # El intento tardío no modifica inventario.

    def test_fastapi_worker_expires_payments_periodically(self) -> None:
        """Lifespan barre expirados al iniciar y continúa haciéndolo periódicamente."""
        self.client.put(  # Prepara una plaza que quedará retenida por pago pendiente.
            "/admin/paquetes/202/inventario",  # Configura inventario por la ruta administrativa existente.
            json={"total_capacity": 2, "travel_date": TRAVEL_DATE_JSON},  # Se usarán plazas independientes para startup y barrido periódico.
            headers=self._admin_headers(),  # Autoriza el administrador.
        )  # Deja la capacidad disponible.
        startup_purchase = self.client.post(  # Crea un pago que se marcará vencido antes de iniciar la nueva app.
            "/reservas",  # Usa la transacción local ya cubierta por los otros casos.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Retiene una plaza para la fecha.
            headers=self._client_headers(),  # Asocia el pago a la cuenta del fixture.
        )  # Guarda reserva y fecha normal de expiración.
        periodic_purchase = self.client.post(  # Deja otro pago pendiente para vencer después de startup.
            "/reservas",  # Crea una reserva separada con su propio pago.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Retiene la segunda plaza para la misma fecha.
            headers=self._client_headers(),  # Usa el mismo titular, con solicitud independiente.
        )  # Permite probar el worker tras su barrido inicial.
        startup_reservation_id = startup_purchase.json()["reservation_id"]  # Identificador cuyo plazo se fuerza antes del startup.
        periodic_reservation_id = periodic_purchase.json()["reservation_id"]  # Identificador que vencerá durante el lifespan.
        old_deadline = "2000-01-01T00:00:00+00:00"  # Fija una fecha pasada reproducible para la prueba.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Actualiza solo la base temporal antes de iniciar FastAPI.
            with connection:  # Confirma el deadline ya vencido para el primer pago.
                connection.execute(  # Deja un pago atrasado para el barrido inmediato de startup.
                    "UPDATE payments SET expires_at = ? WHERE reservation_id = ?",
                    (old_deadline, startup_reservation_id),  # No altera la fecha del segundo pago todavía.
                )  # Guarda la condición de arranque.
        app = create_app(  # Recrea los servicios sobre el mismo archivo para ejecutar el lifespan del servidor.
            self.database_path,  # Reutiliza la base SQLite del caso.
            jwt_secret=self.jwt_secret,  # Mantiene válido el token local del fixture.
            fx_provider=lambda: 900.0,  # Evita cualquier dependencia de red al iniciar la aplicación.
        )  # Crea worker lifespan nuevo sobre datos persistentes.
        with patch("main_api.PAYMENT_EXPIRY_SWEEP_SECONDS", 0.01):  # Acelera el reloj del worker solo durante esta prueba.
            with TestClient(app) as running_client:  # Inicia lifespan, hace el barrido inicial y mantiene activa la tarea.
                startup_payment = running_client.get(  # Confirma que el barrido inicial limpió el pago anterior a la app.
                    f"/reservas/{startup_reservation_id}/pago",  # Consulta por API el estado persistido tras el startup.
                    headers=self._client_headers(),  # Usa la identidad propietaria.
                )  # La aplicación ya pasó por su primera limpieza síncrona.
                self.assertEqual(startup_payment.json()["status"], "expired")  # Startup libera vencimientos acumulados durante una parada.
                with closing(sqlite3.connect(self.database_path)) as connection:  # Fuerza la fecha de expiración tras el primer barrido inicial.
                    with connection:  # Confirma el cambio mientras el servidor está activo.
                        connection.execute(  # Marca el pago como vencido para el siguiente ciclo periódico.
                            "UPDATE payments SET expires_at = ? WHERE reservation_id = ?",
                            (old_deadline, periodic_reservation_id),  # Usa la misma fecha claramente pasada.
                        )  # La tarea periódica detectará el deadline expirado.
                time.sleep(0.08)  # Permite que varios intervalos breves del worker transcurran en su hilo del event loop.
                payment = running_client.get(  # Consulta desde la instancia con lifespan activo.
                    f"/reservas/{periodic_reservation_id}/pago",  # La respuesta permite comprobar que el worker persistió el vencimiento.
                    headers=self._client_headers(),  # Usa el RUT propietario.
                )  # Lee el estado ya procesado, no un valor en memoria del test.
                self.assertEqual(payment.status_code, 200)  # La consulta autenticada continúa disponible durante el worker.
                self.assertEqual(payment.json()["status"], "expired")  # El barrido periódico cambió el pago en SQLite.
                self.assertEqual(app.state.compra_service.get_available_capacity(202), 2)  # Startup y worker devolvieron cada cupo una vez.

    def test_only_administrator_can_list_all_reservations(self) -> None:
        """La consulta global de reservas queda restringida al rol administrador."""
        self.client.put(  # Configura inventario para crear un recibo visible globalmente.
            "/admin/paquetes/202/inventario",  # Invoca la ruta administrativa habitual.
            json={"total_capacity": 1, "travel_date": TRAVEL_DATE_JSON},  # Provee un cupo para la fecha de viaje.
            headers=self._admin_headers(),  # Usa las credenciales administrativas del fixture.
        )  # Deja el paquete reservable.
        purchase = self.client.post(  # Registra una reserva perteneciente al cliente.
            "/reservas",  # Crea el recibo mediante la compra transaccional.
            json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},  # Consume el único cupo de esa fecha.
            headers=self._client_headers(),  # Usa identidad cliente para generar el recibo.
        )  # Finaliza antes de comprobar visibilidad administrativa.
        client_response = self.client.get("/admin/reservas", headers=self._client_headers())  # Intenta consulta global con rol cliente.
        admin_response = self.client.get("/admin/reservas", headers=self._admin_headers())  # Repite la consulta con rol administrador.
        self.assertEqual(client_response.status_code, 403)  # Autenticación válida no basta para consultar otras personas.
        self.assertEqual(admin_response.status_code, 200)  # La consulta administrativa queda autorizada.
        self.assertEqual(admin_response.json()[0]["reservation_id"], purchase.json()["reservation_id"])  # El admin recibe el recibo completo.

    def test_reusing_idempotency_key_with_different_body_is_conflict(self) -> None:
        """No permite asociar una misma clave a paquetes o cantidades distintos."""
        self.client.put(  # Prepara capacidad para la primera operación.
            "/admin/paquetes/202/inventario",  # Usa el endpoint administrativo.
            json={"total_capacity": 4, "travel_date": TRAVEL_DATE_JSON},  # Configura cupos para que la segunda solicitud no falle por inventario.
            headers=self._admin_headers(),  # Autoriza configuración con rol apropiado.
        )  # Deja cuatro plazas disponibles.
        authorization = self._client_headers()  # Mantiene la misma identidad entre ambas llamadas.
        headers = {**authorization, "X-Idempotency-Key": "checkout-conflict-key"}  # Comparte clave bajo el mismo RUT.
        first = self.client.post(  # Registra la primera combinación de clave y cuerpo.
            "/reservas", json={"package_code": 202, "quantity": 1, "travel_date": TRAVEL_DATE_JSON}, headers=headers
        )  # Consume un cupo y persiste su huella.
        conflict = self.client.post(  # Reutiliza clave con una cantidad semánticamente distinta.
            "/reservas", json={"package_code": 202, "quantity": 2, "travel_date": TRAVEL_DATE_JSON}, headers=headers
        )  # Debe comparar huellas antes de comprobar o descontar inventario.
        self.assertEqual(first.status_code, 201)  # Asegura que el caso inicial quedó guardado.
        self.assertEqual(conflict.status_code, 409)  # Señala uso ambiguo de clave en vez de cobrar/reservar de nuevo.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 3)  # Solo se consumió el cupo de la primera compra.

    def test_inventory_and_reservations_survive_application_recreation(self) -> None:
        """Al recrear servicios sobre el mismo archivo persisten cupos y recibos."""
        self.client.put(  # Configura el inventario inicial.
            "/admin/paquetes/202/inventario",  # La operación debe persistirse en SQLite.
            json={"total_capacity": 5, "travel_date": TRAVEL_DATE_JSON},  # Define cinco plazas para la fecha.
            headers=self._admin_headers(),  # Usa permiso administrativo válido.
        )  # La configuración no queda solo en memoria.
        purchase = self.client.post(  # Consume dos plazas y crea una reserva local.
            "/reservas",  # Ejecuta el servicio de compra persistente.
            json={"package_code": 202, "quantity": 2, "travel_date": TRAVEL_DATE_JSON},  # Compra dos de cinco cupos en esta fecha.
            headers={**self._client_headers(), "X-Idempotency-Key": "persistent-edge-key"},  # Vincula reserva y reintento al cliente existente.
        )  # La transacción confirma recibo y contador.
        self.assertEqual(purchase.status_code, 201)  # Verifica que existe una reserva antes de reiniciar.

        self.client.close()  # Cierra el cliente anterior para simular fin de ciclo de vida de la aplicación.
        self.app = create_app(  # Crea nuevas instancias de servicios desde el mismo archivo de datos.
            self.database_path,  # Reabre la base SQLite ya utilizada.
            jwt_secret=self.jwt_secret,  # Mantiene clave de prueba para autenticar nuevamente.
            fx_provider=lambda: 900.0,  # Conserva FX aislado de red.
        )  # Reinstancia servicios y vuelve a asegurar sus tablas.
        self.client = TestClient(self.app)  # Usa el servicio reconstruido para consultar el estado persistido.

        login = self.client.post(  # Confirma también que la cuenta sobrevive al reinicio del servicio.
            "/auth/login",
            json={"rut": "10.000.013-K", "password": "cliente-seguro-local-2026"},
        )  # Lee las credenciales guardadas en la misma base.
        catalog = self.client.get("/paquetes")  # Consulta inventario con la nueva instancia de API.
        replay = self.client.post(  # Reintenta la compra original después de reconstruir todos los servicios.
            "/reservas",  # La clave debe sobrevivir porque se guarda en la base, no en memoria.
            json={"package_code": 202, "quantity": 2, "travel_date": TRAVEL_DATE_JSON},  # Reenvía el cuerpo original sin cambios.
            headers={
                "Authorization": f"Bearer {login.json()['access_token']}",  # Usa un JWT nuevo después de reiniciar la API.
                "X-Idempotency-Key": "persistent-edge-key",  # Reutiliza exactamente la clave persistida previamente.
            },
        )  # Ejecuta lookup idempotente con la nueva instancia de CompraService.
        self.assertEqual(login.status_code, 200)  # El usuario continúa persistido.
        self.assertEqual(catalog.json()[0]["cupos_disponibles"], 3)  # Persisten los cinco cupos menos los dos reservados.
        self.assertEqual(replay.status_code, 200)  # La nueva instancia recupera el resultado confirmado.
        self.assertEqual(replay.json(), purchase.json())  # El recibo recuperado es idéntico al original.
        self.assertEqual(self.client.get("/paquetes").json()[0]["cupos_disponibles"], 3)  # El replay tras reinicio no vuelve a descontar plazas.

    def test_concurrent_requests_with_same_key_create_only_one_reservation(self) -> None:
        """SQLite serializa peticiones paralelas de la misma clave/RUT."""
        service = self.app.state.compra_service  # Usa la misma instancia de compra que la API local.
        service.configure_capacity(202, TRAVEL_DATE, 1)  # Configura exactamente el único cupo de la fecha.

        def submit_same_purchase(_: int) -> object:
            """Ejecuta la misma compra y clave desde uno de los hilos."""
            return service.purchase_idempotently(  # El primero crea y el resto debe recuperar el mismo recibo.
                "10.000.013-K", 202, 1, "concurrent-edge-idempotency-key", travel_date=TRAVEL_DATE
            )  # Todas las llamadas describen la misma operación lógica.

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:  # Simula ocho reintentos concurrentes del cliente.
            results = list(executor.map(submit_same_purchase, range(8)))  # Espera el resultado de todas las solicitudes.
        receipts = [result.receipt for result in results]  # Reúne el recibo que devolvió cada operación.
        self.assertEqual(len({receipt.reservation_id for receipt in receipts}), 1)  # Todas las llamadas comparten una sola reserva.
        self.assertEqual(sum(not result.replayed for result in results), 1)  # Exactamente una llamada crea; las demás son replays.
        self.assertEqual(service.get_available_capacity(202), 0)  # Un solo cupo queda descontado a pesar de la concurrencia.

    def test_existing_reservations_table_is_migrated_without_losing_rows(self) -> None:
        """La migración agrega columnas idempotentes sin borrar reservas históricas."""
        legacy_database = Path(self.temp_directory.name) / "legacy-reservations.db"  # Usa una base separada con esquema anterior.
        with closing(sqlite3.connect(legacy_database)) as connection:  # Abre la base de legado y cierra recursos después de preparar datos.
            with connection:  # Confirma el esquema y fila histórica como una sola transacción.
                connection.execute(  # Crea el catálogo mínimo que requiere la clave foránea del nuevo esquema.
                    """
                    CREATE TABLE paquetes (
                        codigo INTEGER PRIMARY KEY,
                        nombre TEXT NOT NULL,
                        duracion INTEGER NOT NULL,
                        precio_base REAL NOT NULL,
                        tipo TEXT NOT NULL,
                        pasaporte_valido INTEGER,
                        impuesto_puerto REAL
                    )
                    """
                )  # Simula la tabla de paquetes que ya existía en la aplicación.
                connection.execute(  # Crea reservas con las columnas de la implementación anterior.
                    """
                    CREATE TABLE reservas (
                        reservation_id TEXT PRIMARY KEY,
                        rut TEXT NOT NULL,
                        package_code INTEGER NOT NULL,
                        quantity INTEGER NOT NULL,
                        unit_price REAL NOT NULL,
                        total_price REAL NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )  # No incluye aún las columnas idempotency_key/request_hash.
                connection.execute(  # Crea la tabla de pago previa a la función de vencimiento.
                    """
                    CREATE TABLE payments (
                        payment_id TEXT PRIMARY KEY,
                        reservation_id TEXT NOT NULL UNIQUE,
                        amount REAL NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'failed')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )  # Simula una base de la versión local anterior al campo expires_at.
                connection.execute(  # Inserta un recibo previo que la migración debe conservar intacto.
                    "INSERT INTO reservas VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("old-reservation", "10000013-K", 202, 1, 40000.0, 40000.0, "2026-01-01T00:00:00+00:00"),
                )  # Deja evidencia persistida anterior a la nueva versión.
                connection.execute(  # Añade un pago pendiente legado cuya expiración debe calcularse al migrar.
                    "INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "old-pending-payment",
                        "old-reservation",
                        40000.0,
                        "pending",
                        "2000-01-01T00:00:00+00:00",
                        "2000-01-01T00:00:00+00:00",
                    ),
                )  # Deja un deadline vencido para que el startup sweep lo procese al arrancar.

        CompraService(legacy_database)  # Inicializa esquema nuevo y ejecuta ALTER TABLE de manera aditiva.
        with closing(sqlite3.connect(legacy_database)) as connection:  # Inspecciona columnas y datos tras la migración.
            columns = {  # Reúne los nombres de columna que reporta SQLite.
                column[1]
                for column in connection.execute("PRAGMA table_info(reservas)").fetchall()
            }  # El nombre se encuentra en la segunda posición del resultado del pragma.
            previous = connection.execute(  # Confirma que la fila histórica sigue almacenada.
                "SELECT reservation_id, total_price FROM reservas WHERE reservation_id = ?",
                ("old-reservation",),
            ).fetchone()  # Lee los valores de reserva previos a la migración.
            migrated_payment = connection.execute(  # Comprueba que la compra antigua recibe un estado de pago histórico.
                "SELECT amount, status FROM payments WHERE reservation_id = ?",
                ("old-reservation",),
            ).fetchone()  # Recupera el pago sintético de migración asociado.
            migrated_deadline = connection.execute(  # Lee el plazo añadido a un pago pendiente ya existente.
                "SELECT expires_at FROM payments WHERE payment_id = ?",
                ("old-pending-payment",),
            ).fetchone()[0]  # El TTL se calcula desde la fecha de creación guardada.
        self.assertIn("idempotency_key", columns)  # Confirma la nueva columna nullable.
        self.assertIn("request_hash", columns)  # Confirma la huella para validar el cuerpo repetido.
        self.assertEqual(previous, ("old-reservation", 40000.0))  # Comprueba que el recibo anterior no se eliminó ni alteró.
        self.assertEqual(migrated_payment, (40000.0, "pending"))  # Conserva el pago legado pendiente en vez de marcarlo confirmado.
        self.assertEqual(migrated_deadline, "2000-01-01T00:15:00+00:00")  # Calcula el vencimiento histórico con el TTL acordado.

    def test_failed_schema_migration_rolls_back_all_schema_changes(self) -> None:
        legacy_database = Path(self.temp_directory.name) / "broken-legacy.db"
        with closing(sqlite3.connect(legacy_database)) as connection:
            with connection:
                connection.execute("CREATE TABLE paquetes (codigo INTEGER PRIMARY KEY)")
                connection.execute(
                    """
                    CREATE TABLE reservas (
                        reservation_id TEXT PRIMARY KEY,
                        rut TEXT NOT NULL,
                        package_code INTEGER NOT NULL,
                        quantity INTEGER NOT NULL,
                        unit_price REAL NOT NULL,
                        total_price REAL NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE payments (
                        payment_id TEXT PRIMARY KEY,
                        reservation_id TEXT NOT NULL UNIQUE,
                        amount REAL NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO reservas VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("legacy-bad-date", "10000013-K", 202, 1, 100.0, 100.0, "2026-01-01"),
                )
                connection.execute(
                    "INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)",
                    ("payment-bad-date", "legacy-bad-date", 100.0, "pending", "2026-01-01", "2026-01-01"),
                )

        with self.assertRaisesRegex(ValueError, "sin zona horaria"):
            CompraService(legacy_database)

        with closing(sqlite3.connect(legacy_database)) as connection:
            reservation_columns = {
                column[1] for column in connection.execute("PRAGMA table_info(reservas)")
            }
            payment_columns = {
                column[1] for column in connection.execute("PRAGMA table_info(payments)")
            }
            inventory = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'package_inventory'"
            ).fetchone()
        self.assertNotIn("unit_price_minor", reservation_columns)
        self.assertNotIn("expires_at", payment_columns)
        self.assertIsNone(inventory)

    def test_purchase_retries_only_locked_operational_errors(self) -> None:
        """Un SQLITE_BUSY transitorio reintenta una vez antes de confirmar la compra."""
        service = CompraService(self.database_path, max_write_attempts=3, retry_delay_seconds=0.01)  # Inyecta política corta y determinista.
        service.configure_capacity(202, TRAVEL_DATE, 1)  # Deja un cupo para una compra que finalmente se confirmará.
        actual_purchase = service._purchase_once  # Conserva la operación transaccional para invocarla después del bloqueo simulado.
        attempts = [0]  # Cuenta las ejecuciones del bloque de compra.

        def locked_once(*args: object) -> tuple[object, bool]:
            """Simula un bloqueo SQLite una vez y luego ejecuta la transacción normal."""
            attempts[0] += 1  # Registra cada intento que alcanzó el límite de transacción.
            if attempts[0] == 1:  # Solo la primera ejecución representa un bloqueo recuperable.
                raise sqlite3.OperationalError("database is locked")  # Simula el mensaje transitorio BUSY de SQLite.
            return actual_purchase(*args)  # Ejecuta la compra real en el intento posterior.

        with patch.object(service, "_purchase_once", side_effect=locked_once), patch("services.compra_service.time.sleep") as sleep:  # Sustituye una falla y elimina espera real del test.
            result = service.purchase_idempotently("10.000.013-K", 202, 1, "retry-edge-key", travel_date=TRAVEL_DATE)  # Solicita compra con idempotencia durante el retry.
        self.assertEqual(attempts[0], 2)  # Comprueba que hubo un reintento y no una repetición ilimitada.
        sleep.assert_called_once_with(0.01)  # Comprueba que se aplicó la pausa breve configurada.
        self.assertFalse(result.replayed)  # La compra se confirmó en este intento, no se recuperó de una clave previa.

    def test_non_locking_operational_error_is_not_retried(self) -> None:
        """Un error SQL no transitorio se traduce y no activa backoff."""
        service = CompraService(self.database_path, max_write_attempts=3, retry_delay_seconds=0.01)  # Configura más de un intento para detectar reintentos incorrectos.
        service.configure_capacity(202, TRAVEL_DATE, 1)  # Deja inventario fechado listo para una compra.
        with patch.object(  # Simula un error de SQL que no se debe tratar como bloqueo.
            service, "_purchase_once", side_effect=sqlite3.OperationalError("near 'BROKEN': syntax error")
        ) as purchase_attempt, patch("services.compra_service.time.sleep") as sleep:  # Vigila cantidad de intentos y pausas.
            with self.assertRaises(PurchasePersistenceError):  # Espera una excepción de dominio y no sqlite3 cruda.
                service.purchase_idempotently("10.000.013-K", 202, 1, "syntax-edge-key", travel_date=TRAVEL_DATE)  # Ejecuta el camino fallido.
        purchase_attempt.assert_called_once()  # Un error de sintaxis no se repite automáticamente.
        sleep.assert_not_called()  # Un error permanente no activa espera de retry.

    def test_constraint_violation_is_not_retried(self) -> None:
        """Una violación de integridad se convierte en error de dominio una sola vez."""
        service = CompraService(self.database_path, max_write_attempts=3, retry_delay_seconds=0.01)  # Deja varios intentos posibles para detectar un retry incorrecto.
        service.configure_capacity(202, TRAVEL_DATE, 1)  # Deja el inventario fechado preparado.
        with patch.object(  # Simula que SQLite rechaza una restricción de persistencia.
            service, "_purchase_once", side_effect=sqlite3.IntegrityError("UNIQUE constraint failed")
        ) as purchase_attempt, patch("services.compra_service.time.sleep") as sleep:  # Espía reintento y pausa.
            with self.assertRaises(PurchasePersistenceError):  # Exige que el detalle SQLite se traduzca a dominio.
                service.purchase_idempotently("10.000.013-K", 202, 1, "constraint-edge-key", travel_date=TRAVEL_DATE)  # Ejecuta la compra que viola la restricción.
        purchase_attempt.assert_called_once()  # No repite errores que persistirán con la misma solicitud.
        sleep.assert_not_called()  # No espera ni reintenta una restricción incumplida.

    def test_locked_database_exhaustion_raises_domain_error(self) -> None:
        """Una base que permanece bloqueada termina en error transitorio explícito."""
        service = CompraService(self.database_path, max_write_attempts=2, retry_delay_seconds=0)  # Limita la prueba a dos intentos sin pausa.
        with patch.object(  # Simula que todas las transacciones tropiezan con el lock de SQLite.
            service, "_purchase_once", side_effect=sqlite3.OperationalError("database is locked")
        ) as purchase_attempt, patch("services.compra_service.time.sleep") as sleep:  # Espía intentos y backoff sin hacerlos reales.
            with self.assertRaises(DatabaseBusyError):  # No permite que agotamiento de lock parezca éxito.
                service.purchase_idempotently("10.000.013-K", 202, 1, travel_date=TRAVEL_DATE)  # Compra sin clave con la política de retry.
        self.assertEqual(purchase_attempt.call_count, 2)  # Respeta el máximo configurado.
        sleep.assert_called_once_with(0)  # Usa la pausa configurada entre el primer y segundo intento.


if __name__ == "__main__":  # Permite ejecutar estos casos directamente con Python.
    unittest.main()  # Ejecuta los casos de borde registrados en la clase.
