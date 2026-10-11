"""Compra atómica de cupos de paquetes turísticos con SQLite local."""

from __future__ import annotations

import hashlib  # Crea una huella compacta del cuerpo asociado a una clave idempotente.
import math  # Valida que el precio calculado sea un número finito y positivo.
import sqlite3  # Ejecuta el bloqueo y las escrituras transaccionales locales.
import time  # Espera brevemente antes de reintentar una transacción bloqueada.
import uuid  # Genera identificadores no predecibles para cada reserva.
from collections.abc import Callable  # Tipifica el proveedor de tasa de cambio inyectado desde FxService.
from contextlib import closing  # Garantiza el cierre de conexiones SQLite.
from dataclasses import dataclass  # Define el resultado de compra como un valor inmutable.
from datetime import UTC, date, datetime, timedelta  # Registra fecha de viaje y timestamps de pagos en UTC.
from decimal import Decimal
from pathlib import Path  # Acepta rutas locales de base de datos.
from typing import Any  # Anota las filas crudas de SQLite, que no exponen tipos estáticos.

from model.cliente import pasaporte_registrado  # Reutiliza la regla de pasaporte del dominio.
from model.money import MAX_MINOR_UNITS, from_minor_units, half_up_minor_units, to_minor_units
from model.paquete_factory import paquete_desde_columnas  # Fuente única del mapeo tipo→modelo.
from model.paquete_turistico import Paquete_Turistico  # Proporciona el tipo de retorno del reconstruidor.
from services.auth_service import normalize_rut  # Almacena el mismo RUT canónico que usa el sistema de login.
from services.notification_outbox import OutboxRepository

SQLITE_INTEGER_MAX = 2**63 - 1  # Límite superior del entero que SQLite puede almacenar en una columna INTEGER.
PAYMENT_PENDING_TTL_SECONDS = 15 * 60  # Retiene cupos por quince minutos mientras el pago permanece pendiente.


class PackageNotFoundError(LookupError):
    """Señala que el paquete solicitado no existe en el catálogo."""


class InventoryNotConfiguredError(LookupError):
    """Señala que el administrador aún no ha configurado cupos para el paquete."""


class InsufficientCapacityError(ValueError):
    """Señala que la cantidad solicitada excede los cupos restantes."""


class CapacityBelowReservedError(ValueError):
    """Señala que no se puede reducir el inventario por debajo de lo ya vendido."""


class InvalidPackagePriceError(ValueError):
    """Señala que el paquete produciría un precio total no válido para una compra."""


class ExchangeRateUnavailableError(RuntimeError):
    """Señala que una compra internacional no tiene proveedor de tasa configurado."""


class PassportRequiredError(ValueError):
    """Señala que una compra internacional no incluye un pasaporte válido."""


class IdempotencyConflictError(ValueError):
    """Señala que una clave idempotente ya se usó con otra solicitud."""


class DatabaseBusyError(RuntimeError):
    """Señala que SQLite siguió bloqueada después de agotar los reintentos."""


class PurchasePersistenceError(RuntimeError):
    """Señala un error SQL no transitorio al persistir la compra."""


class ReservationNotFoundError(LookupError):
    """Señala que la reserva no existe o no pertenece al cliente autenticado."""


class PaymentTransitionConflictError(ValueError):
    """Señala que el estado de pago ya no permite la transición solicitada."""


class PaymentIdempotencyConflictError(ValueError):
    """Señala que una clave de abono se reutilizó con otro monto."""


@dataclass(frozen=True)
class ReservationReceipt:
    """Contiene el recibo de cupos retenidos y su pago local asociado."""

    reservation_id: str  # UUID asignado a la reserva confirmada.
    rut: str  # RUT normalizado del usuario autenticado que compró.
    package_code: int  # Código del paquete comprado.
    quantity: int  # Cantidad de cupos consumidos por la compra.
    travel_date: str  # Fecha de viaje que identifica la fila de inventario reservada.
    unit_price: float  # Precio por persona según la fórmula actual del subtipo.
    total_price: float  # Precio unitario multiplicado por la cantidad reservada.
    exchange_rate_applied: float | None  # Tasa USD/CLP congelada para paquetes que convierten moneda.
    total_paid: float  # Suma de los pagos confirmados de esta reserva.
    balance_due: float  # Saldo que aún puede pagarse sin exceder el precio acordado.
    created_at: str  # Fecha UTC serializada de creación de la reserva.
    status: str = "confirmed"  # Estado persistido del ciclo de vida de la reserva.
    cancelled_at: str | None = None  # Fecha UTC de cancelación o None mientras siga confirmada.
    payment_id: str = ""  # Identificador del pago más reciente, si la reserva tiene alguno.
    payment_status: str = "none"  # Estado local del pago más reciente o none si no tiene pagos.
    payment_expires_at: str | None = None  # Fecha UTC límite para completar el pago pendiente.


@dataclass(frozen=True)
class PaymentReceipt:
    """Representa el estado de pago local asociado a una reserva."""

    payment_id: str  # Identificador único local; no representa una transacción bancaria.
    reservation_id: str  # Reserva a la que pertenece el intento de pago.
    amount: float  # Monto congelado al precio total de la reserva.
    status: str  # Estado pendiente, confirmado o fallido.
    created_at: str  # Fecha UTC en que se creó el pago local.
    updated_at: str  # Fecha UTC de la última transición de estado.
    expires_at: str | None  # Fecha UTC límite para resolver el pago.


@dataclass(frozen=True)
class IdempotentPurchaseResult:
    """Empaqueta el recibo y si se recuperó de una compra anterior."""

    receipt: ReservationReceipt  # Resultado persistido que se devuelve al cliente.
    replayed: bool  # True cuando la clave encontró una reserva confirmada previamente.


class CompraService:
    """Coordina reserva, inventario, precio y pago local mediante transacciones SQLite."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        exchange_rate_provider: Callable[[], float] | None = None,
        max_write_attempts: int = 3,
        retry_delay_seconds: float = 0.05,
    ) -> None:
        """Prepara el servicio y configura una política acotada de reintentos."""
        if isinstance(max_write_attempts, bool) or not isinstance(max_write_attempts, int) or max_write_attempts < 1:  # Evita una política vacía o no entera.
            raise ValueError("max_write_attempts debe ser un entero positivo.")  # Informa el parámetro de reintentos esperado.
        if isinstance(retry_delay_seconds, bool) or not isinstance(retry_delay_seconds, (int, float)) or not math.isfinite(retry_delay_seconds) or retry_delay_seconds < 0:  # Acepta espera cero para pruebas, pero no tiempos negativos/no finitos.
            raise ValueError("retry_delay_seconds debe ser finito y no negativo.")  # Evita pausas no válidas.
        self._database_path = str(database_path)  # Conserva la ruta usada para cada operación independiente.
        self._exchange_rate_provider = exchange_rate_provider  # Inyecta FxService sin acoplar este servicio a su implementación.
        self._max_write_attempts = max_write_attempts  # Limita la cantidad máxima de transacciones intentadas.
        self._retry_delay_seconds = float(retry_delay_seconds)  # Normaliza la pausa entre intentos.
        self._ensure_schema()  # Crea las tablas auxiliares después de que exista paquetes.

    def configure_capacity(self, package_code: int, travel_date: date, total_capacity: int) -> int:
        """Define cupos de un paquete para una fecha sin rebajar reservas existentes."""
        if isinstance(package_code, bool) or not isinstance(package_code, int) or not 1 <= package_code <= SQLITE_INTEGER_MAX:  # Rechaza códigos fuera del rango numérico de SQLite.
            raise ValueError("El código de paquete debe ser un entero positivo válido para SQLite.")  # Explica qué identificador se espera.
        travel_date_iso = self._normalize_travel_date(travel_date)  # Normaliza la fecha ISO antes de usarla como clave de inventario.
        if isinstance(total_capacity, bool) or not isinstance(total_capacity, int) or not 0 <= total_capacity <= SQLITE_INTEGER_MAX:  # Permite cero cupos, pero limita el entero a SQLite.
            raise ValueError("La capacidad debe ser un entero no negativo válido para SQLite.")  # Evita inventario inválido o imposible de persistir.

        with closing(self._connect()) as connection:  # Abre la conexión con claves foráneas habilitadas y asegura su cierre.
            connection.execute("BEGIN IMMEDIATE")  # Toma el bloqueo de escritura antes de comprobar y cambiar la capacidad.
            try:  # Garantiza rollback si el paquete no existe o la capacidad es incompatible.
                package_exists = connection.execute(  # Comprueba que el catálogo contiene el código solicitado.
                    "SELECT 1 FROM paquetes WHERE codigo = ? AND activo = 1", (package_code,)  # Solo permite administrar inventario de paquetes publicados.
                ).fetchone()  # Retorna None cuando el paquete no está registrado.
                if package_exists is None:  # Evita inventario huérfano sin un paquete correspondiente.
                    raise PackageNotFoundError("No existe el paquete solicitado.")  # Permite a la API responder 404.

                connection.execute(  # Inserta cupos iniciales o actualiza una capacidad ya configurada.
                    """
                    INSERT INTO package_inventory (package_code, travel_date, total_capacity, reserved_capacity)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT (package_code, travel_date) DO UPDATE SET
                        total_capacity = excluded.total_capacity
                    WHERE excluded.total_capacity >= package_inventory.reserved_capacity
                    """,
                    (package_code, travel_date_iso, total_capacity),  # Parametriza paquete, fecha y capacidad.
                )  # El WHERE impide rebajar la capacidad por debajo de las ventas existentes.
                updated = connection.execute("SELECT changes()").fetchone()[0]  # Lee el conteo que SQLite devuelve para la última escritura.
                if updated == 0:  # Distingue el conflicto de capacidad del caso de inserción/actualización exitosa.
                    raise CapacityBelowReservedError("La capacidad no puede ser menor a los cupos ya reservados.")  # Protege consistencia de inventario.

                available = connection.execute(  # Calcula los cupos disponibles posteriores a la configuración.
                    "SELECT total_capacity - reserved_capacity FROM package_inventory WHERE package_code = ? AND travel_date = ?",  # Lee el inventario exacto que se configuró.
                    (package_code, travel_date_iso),  # Limita la consulta al paquete y fecha solicitados.
                ).fetchone()[0]  # Obtiene el entero de cupos que puede venderse.
                connection.commit()  # Confirma la nueva capacidad tras completar sus validaciones.
                return int(available)  # Devuelve al administrador el inventario vendible actual.
            except Exception:  # Captura errores solo para revertir y volver a propagar el fallo original.
                connection.rollback()  # Deshace cambios parciales para no dejar capacidad en estado incierto.
                raise  # No oculta la causa ni la convierte en un resultado de éxito.

    def purchase(
        self,
        rut: str,
        package_code: int,
        quantity: int,
        *,
        travel_date: date,
        notification_email: str | None = None,
        passport: str | None = None,
    ) -> ReservationReceipt:
        """Confirma una compra no idempotente y conserva la firma pública existente."""
        result = self.purchase_idempotently(rut, package_code, quantity, travel_date=travel_date, notification_email=notification_email, passport=passport)  # Delega a la operación transaccional común sin clave idempotente.
        return result.receipt  # Conserva el tipo de retorno usado por los consumidores existentes.

    def purchase_idempotently(
        self,
        rut: str,
        package_code: int,
        quantity: int,
        idempotency_key: str | None = None,
        *,
        travel_date: date,
        notification_email: str | None = None,
        passport: str | None = None,
    ) -> IdempotentPurchaseResult:
        """Compra una sola vez por RUT/clave y reproduce el recibo en reintentos."""
        normalized_rut = normalize_rut(rut)  # Asegura que el comprador quede vinculado al RUT autenticado normalizado.
        if isinstance(package_code, bool) or not isinstance(package_code, int) or not 1 <= package_code <= SQLITE_INTEGER_MAX:  # Valida el código dentro del rango SQLite.
            raise ValueError("El código de paquete debe ser un entero positivo válido para SQLite.")  # Rechaza valores que no son códigos persistibles.
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= SQLITE_INTEGER_MAX:  # Exige cupos positivos que quepan en SQLite.
            raise ValueError("La cantidad debe ser un entero positivo válido para SQLite.")  # Impide reservas vacías, negativas o fuera de rango.
        normalized_key = self._normalize_idempotency_key(idempotency_key)  # Valida y canoniza la clave opcional antes de acceder a SQLite.
        normalized_travel_date = self._normalize_travel_date(travel_date)  # Hace que la fecha de viaje forme parte del contrato persistido.
        request_material = f"{package_code}:{quantity}:{normalized_travel_date}"  # Conserva la huella histórica cuando no se solicita notificación.
        if notification_email is not None:
            if not isinstance(notification_email, str) or not notification_email.strip():
                raise ValueError("notification_email debe ser una dirección no vacía.")
            request_material += f":{notification_email.strip().casefold()}"
        referencia_pasaporte = passport.strip() if isinstance(passport, str) else ""  # Normaliza el pasaporte opcional para la huella idempotente.
        request_material += f":passport={referencia_pasaporte}"  # Distingue reintentos con otro pasaporte bajo la misma clave.
        request_hash = hashlib.sha256(request_material.encode("utf-8")).hexdigest() if normalized_key else None  # Vincula la clave al cuerpo completo, incluido el pasaporte.

        if normalized_key is not None:
            previous = self._find_reservation_by_key(normalized_rut, normalized_key)  # Evita consultar FX en reintentos ya completados.
            if previous is not None:
                if previous[9] != request_hash:
                    raise IdempotencyConflictError("La clave de idempotencia ya se usó con otra solicitud.")
                return IdempotentPurchaseResult(receipt=self._receipt_from_row(previous), replayed=True)

        exchange_rate = self._get_package_exchange_rate(package_code)  # Se obtiene antes del BEGIN IMMEDIATE para no mantener el lock de escritura durante una llamada de red; el precio se congela al confirmar.

        for attempt in range(1, self._max_write_attempts + 1):  # Ejecuta el número de intentos acotado por configuración.
            try:  # Repite la transacción completa solo para bloqueos SQLite transitorios.
                receipt, replayed = self._purchase_once(normalized_rut, package_code, quantity, normalized_travel_date, exchange_rate, normalized_key, request_hash, notification_email, passport)  # Ejecuta una transacción indivisible o recupera el resultado anterior.
                return IdempotentPurchaseResult(receipt=receipt, replayed=replayed)  # Devuelve el recibo junto a su estado de repetición.
            except sqlite3.OperationalError as error:  # Distingue los bloqueos transitorios de otros errores de SQL.
                if not self._is_database_locked(error):  # No reintenta sintaxis SQL ni otros OperationalError permanentes.
                    raise PurchasePersistenceError("SQLite rechazó la operación de compra.") from error  # Informa el fallo SQL sin mostrar detalles internos.
                if attempt == self._max_write_attempts:  # Comprueba si ya se consumió el presupuesto de reintentos.
                    raise DatabaseBusyError("La base de datos sigue ocupada; reintente la compra.") from error  # Devuelve un error de dominio recuperable por el cliente.
                time.sleep(self._retry_delay_seconds * attempt)  # Aplica backoff lineal breve antes de repetir la transacción completa.
            except sqlite3.IntegrityError as error:  # Captura restricciones incumplidas sin considerarlas errores de bloqueo.
                raise PurchasePersistenceError("La compra incumplió una restricción de persistencia.") from error  # No repite una operación que no puede tener éxito igual.

        raise DatabaseBusyError("No fue posible adquirir la base de datos para la compra.")  # Salvaguarda defensiva: el bucle siempre retorna o lanza.

    def _purchase_once(
        self,
        normalized_rut: str,
        package_code: int,
        quantity: int,
        travel_date: str,
        exchange_rate: float | None,
        idempotency_key: str | None,
        request_hash: str | None,
        notification_email: str | None,
        passport: str | None,
    ) -> tuple[ReservationReceipt, bool]:
        """Realiza una transacción de compra o lee la reserva ya confirmada."""
        reservation_id = str(uuid.uuid4())  # Genera un UUID distinto para una nueva compra; no se usa al reproducir.
        payment_id = str(uuid.uuid4())  # Crea el identificador del registro de pago simulado asociado a la reserva.
        created_datetime = datetime.now(UTC)  # Toma una sola hora base para evitar pequeñas diferencias entre campos.
        created_at = created_datetime.isoformat()  # Registra creación en UTC para la nueva reserva.
        expires_at = (created_datetime + timedelta(seconds=PAYMENT_PENDING_TTL_SECONDS)).isoformat()  # Define el plazo máximo para resolver el pago.
        with closing(self._connect()) as connection:  # Abre una conexión nueva por intento para no reutilizar una transacción abortada.
            connection.execute("BEGIN IMMEDIATE")  # Toma el bloqueo de escritura antes de consultar clave o inventario.
            try:  # Agrupa lookup de idempotencia, descuento de cupos y recibo en la misma transacción.
                if idempotency_key is not None:  # Omite lookup cuando el cliente no envía clave.
                    previous = connection.execute(  # Busca si ese usuario ya confirmó una operación con la misma clave.
                        """
                        SELECT r.reservation_id, r.rut, r.package_code, r.quantity,
                               r.unit_price, r.total_price, r.created_at, r.status,
                               r.cancelled_at, r.request_hash, r.travel_date,
                               r.exchange_rate_applied,
                               (SELECT p.payment_id FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                               (SELECT CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                               (SELECT p.expires_at FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                               COALESCE((SELECT SUM(p.amount_minor) FROM payments AS p WHERE p.reservation_id = r.reservation_id AND p.status = 'confirmed'), 0),
                               r.total_price_minor, r.unit_price_minor
                        FROM reservas AS r
                        WHERE r.rut = ? AND r.idempotency_key = ?
                        """,
                        (normalized_rut, idempotency_key),  # Acota la unicidad a la identidad autenticada y su clave.
                    ).fetchone()  # Lee recibo y huella previamente confirmados, si existen.
                    if previous is not None:  # Una coincidencia indica que la petición pudo ser un reintento tras perder respuesta.
                        if previous[9] != request_hash:  # Impide reutilizar la clave para comprar otro paquete o cantidad.
                            raise IdempotencyConflictError("La clave de idempotencia ya se usó con otra solicitud.")  # Informa conflicto sin modificar inventario.
                        connection.commit()  # Finaliza la transacción de lectura antes de devolver el resultado existente.
                        return self._receipt_from_row(previous), True  # Reproduce recibo y estado de pago sin descontar cupos.

                package_row = connection.execute(  # Recupera paquete y capacidad de la fecha bajo el bloqueo transaccional.
                    """
                    SELECT p.codigo, p.nombre, p.duracion, p.precio_base, p.tipo,
                           p.pasaporte_valido, p.impuesto_puerto,
                           i.total_capacity, i.reserved_capacity
                    FROM paquetes AS p
                    LEFT JOIN package_inventory AS i
                      ON i.package_code = p.codigo AND i.travel_date = ?
                    WHERE p.codigo = ? AND p.activo = 1
                    """,
                    (travel_date, package_code),  # Busca paquete y disponibilidad para la fecha solicitada.
                ).fetchone()  # Lee producto e inventario en una sola consulta.
                if package_row is None:  # Distingue un código inexistente de una falta de cupos.
                    raise PackageNotFoundError("No existe el paquete solicitado.")  # La API traducirá esta condición a HTTP 404.
                if package_row[4] == "internacional" and not pasaporte_registrado(passport):  # Solo los paquetes internacionales exigen pasaporte.
                    raise PassportRequiredError("El paquete internacional requiere un pasaporte válido para confirmar la compra.")  # Evita confirmar sin pasaporte antes de tocar inventario.
                if package_row[7] is None:  # Una fila NULL indica que un administrador aún no configuró capacidad.
                    raise InventoryNotConfiguredError("El paquete todavía no tiene cupos configurados.")  # Impide vender disponibilidad desconocida.

                available = int(package_row[7]) - int(package_row[8])  # Calcula cupos restantes en la fecha solicitada.
                if quantity > available:  # Rechaza una cantidad superior al inventario.
                    raise InsufficientCapacityError("No hay cupos suficientes para la cantidad solicitada.")  # No genera recibo ni descuento.

                package = self._build_package(package_row)  # Reconstruye subtipo y conserva la regla de precio actual.
                raw_unit_price = package.calcular_precio(exchange_rate) if exchange_rate is not None else package.calcular_precio()  # Congela la tasa FX en el precio unitario cuando el subtipo la necesita.
                if not math.isfinite(raw_unit_price) or raw_unit_price <= 0:  # Evita almacenar tasas/valores no finitos o no positivos.
                    raise InvalidPackagePriceError("El paquete tiene un precio inválido para la compra.")  # No cambia fórmulas de dominio.
                unit_price_minor = to_minor_units(raw_unit_price)
                total_price_minor = unit_price_minor * quantity
                if unit_price_minor <= 0 or total_price_minor > MAX_MINOR_UNITS:
                    raise InvalidPackagePriceError("El precio total de la compra no es válido.")  # Evita persistir un importe corrupto.
                unit_price = float(from_minor_units(unit_price_minor))
                total_price = float(from_minor_units(total_price_minor))

                updated = connection.execute(  # Descuenta cupos mediante una condición atómica de capacidad disponible.
                    """
                    UPDATE package_inventory
                    SET reserved_capacity = reserved_capacity + ?
                    WHERE package_code = ? AND travel_date = ?
                      AND total_capacity - reserved_capacity >= ?
                    """,
                    (quantity, package_code, travel_date, quantity),  # Revalida atómicamente la misma fecha que se leyó.
                ).rowcount  # Cuenta cuántas filas afectó la actualización condicional.
                if updated != 1:  # Protege contra una condición que cambiase entre lectura y actualización.
                    raise InsufficientCapacityError("No hay cupos suficientes para la cantidad solicitada.")  # Previene sobreventa incluso ante escrituras concurrentes.

                connection.execute(  # Guarda el recibo asociado al descuento de cupos.
                    """
                    INSERT INTO reservas (
                        reservation_id, rut, package_code, quantity,
                        unit_price, total_price, unit_price_minor, total_price_minor,
                        travel_date, exchange_rate_applied, created_at,
                        idempotency_key, request_hash, notification_email
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (reservation_id, normalized_rut, package_code, quantity, unit_price, total_price, unit_price_minor, total_price_minor, travel_date, exchange_rate, created_at, idempotency_key, request_hash, notification_email),  # Conserva importes exactos, viaje y notificación para auditoría.
                )  # La referencia al catálogo impide reservas de paquetes inexistentes.
                connection.execute(  # Abre un pago local pendiente en la misma transacción que retiene los cupos.
                    """
                    INSERT INTO payments (
                        payment_id, reservation_id, amount, amount_minor, status,
                        created_at, updated_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (payment_id, reservation_id, float(from_minor_units(half_up_minor_units(total_price_minor))), half_up_minor_units(total_price_minor), created_at, created_at, expires_at),  # Abre el anticipo mínimo del 50 % con redondeo decimal explícito.
                )  # Un fallo al insertar el pago revierte también la reserva y la retención de inventario.
                if notification_email is not None:
                    OutboxRepository.enqueue(
                        connection,
                        event_key=f"reservation-created:{reservation_id}",
                        payload={
                            "recipient": notification_email,
                            "subject": f"Reserva {reservation_id} recibida",
                            "body": (
                                f"Tu reserva {reservation_id} fue recibida. "
                                f"El pago inicial está pendiente hasta {expires_at}."
                            ),
                        },
                        created_at=created_at,
                    )
                connection.commit()  # Confirma cupos, recibo y clave idempotente como una sola unidad.
            except Exception:  # Revierte dominio, persistencia u otros errores ocurridos antes del commit.
                connection.rollback()  # Evita reservar cupos sin un recibo/clave confirmados.
                raise  # Mantiene intacta la excepción original para su traducción posterior.

        return self._receipt_from_values(reservation_id, normalized_rut, package_code, quantity, travel_date, unit_price, total_price, exchange_rate, created_at, payment_id), False  # Devuelve reserva, tasa aplicada y anticipo pendiente.

    @staticmethod
    def _normalize_idempotency_key(idempotency_key: str | None) -> str | None:
        """Valida longitud y caracteres imprimibles de la clave del cliente."""
        if idempotency_key is None:  # Preserva compatibilidad de clientes que no envían clave.
            return None  # Indica que la compra seguirá siendo una llamada sin idempotencia.
        normalized = idempotency_key.strip()  # Elimina espacios accidentales al principio o final.
        if not normalized or len(normalized) > 128 or any(ord(character) < 33 or ord(character) > 126 for character in normalized):  # Limita la clave a ASCII imprimible sin espacios internos.
            raise ValueError("X-Idempotency-Key debe tener entre 1 y 128 caracteres ASCII imprimibles.")  # Rechaza claves ambiguas o excesivas.
        return normalized  # Devuelve la representación que se indexará en la base.

    @staticmethod
    def _normalize_travel_date(travel_date: date) -> str:
        """Valida y normaliza la fecha local solicitada para el viaje."""
        if isinstance(travel_date, datetime) or not isinstance(travel_date, date):
            raise ValueError("travel_date debe ser una fecha válida.")
        return travel_date.isoformat()

    def _get_package_exchange_rate(self, package_code: int) -> float | None:
        """Obtiene FX solo para paquetes internacionales y cruceros."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT tipo FROM paquetes WHERE codigo = ? AND activo = 1",
                (package_code,),
            ).fetchone()
        if row is None:
            raise PackageNotFoundError("No existe el paquete solicitado.")
        if row[0] not in ("internacional", "crucero"):
            return None
        if self._exchange_rate_provider is None:
            raise ExchangeRateUnavailableError("No hay un servicio USD/CLP configurado para esta compra.")
        rate = self._exchange_rate_provider()
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
            raise InvalidPackagePriceError("El proveedor devolvió una tasa USD/CLP inválida.")
        return float(rate)

    def _find_reservation_by_key(
        self,
        normalized_rut: str,
        idempotency_key: str,
    ) -> tuple[Any, ...] | None:
        """Lee el recibo anterior para repetir una compra sin volver a consultar FX."""
        query = """
            SELECT r.reservation_id, r.rut, r.package_code, r.quantity,
                   r.unit_price, r.total_price, r.created_at, r.status,
                   r.cancelled_at, r.request_hash, r.travel_date,
                   r.exchange_rate_applied,
                   (SELECT p.payment_id FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   (SELECT CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   (SELECT p.expires_at FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   COALESCE((SELECT SUM(p.amount_minor) FROM payments AS p WHERE p.reservation_id = r.reservation_id AND p.status = 'confirmed'), 0),
                   r.total_price_minor, r.unit_price_minor
            FROM reservas AS r
            WHERE r.rut = ? AND r.idempotency_key = ?
        """
        with closing(self._connect()) as connection:
            return connection.execute(query, (normalized_rut, idempotency_key)).fetchone()

    @staticmethod
    def _find_reservation_by_id(
        connection: sqlite3.Connection,
        reservation_id: str,
    ) -> ReservationReceipt:
        """Lee el recibo actualizado con el último pago y el saldo acumulado."""
        row = connection.execute(
            """
            SELECT r.reservation_id, r.rut, r.package_code, r.quantity,
                   r.unit_price, r.total_price, r.created_at, r.status,
                   r.cancelled_at, r.request_hash, r.travel_date,
                   r.exchange_rate_applied,
                   (SELECT p.payment_id FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   (SELECT CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   (SELECT p.expires_at FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   COALESCE((SELECT SUM(p.amount_minor) FROM payments AS p WHERE p.reservation_id = r.reservation_id AND p.status = 'confirmed'), 0),
                   r.total_price_minor, r.unit_price_minor
            FROM reservas AS r
            WHERE r.reservation_id = ?
            """,
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise ReservationNotFoundError("No se encontró la reserva solicitada.")
        return CompraService._receipt_from_row(row)

    @staticmethod
    def _is_database_locked(error: sqlite3.OperationalError) -> bool:
        """Reconoce solo errores SQLite BUSY/LOCKED, nunca sintaxis u otros fallos."""
        error_code = getattr(error, "sqlite_errorcode", None)  # Obtiene el código extendido disponible en las versiones actuales de sqlite3.
        if error_code is not None:  # Prefiere el código estable cuando Python lo proporciona.
            primary_code = error_code & 0xFF  # SQLite guarda el código primario en los ocho bits inferiores.
            return primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)  # Solo bloqueos se consideran transitorios.
        message = str(error).casefold()  # Compatibilidad con runtimes antiguos que no exponen sqlite_errorcode.
        return "database is locked" in message or "database table is locked" in message  # Usa mensajes exactos conocidos como fallback limitado.

    @staticmethod
    def _receipt_from_row(row: tuple[Any, ...]) -> ReservationReceipt:
        """Reconstruye un recibo persistido desde las columnas de reserva."""
        return ReservationReceipt(  # Normaliza los valores SQLite al contrato inmutable del servicio.
            reservation_id=str(row[0]),  # Recupera la clave única de la reserva.
            rut=str(row[1]),  # Recupera el RUT canónico persistido.
            package_code=int(row[2]),  # Recupera el código de catálogo.
            quantity=int(row[3]),  # Recupera la cantidad original comprada.
            travel_date=str(row[10]),  # Recupera la fecha de viaje usada para retener cupos.
            unit_price=float(from_minor_units(int(row[17]))),  # Recupera el precio unitario desde centésimos enteros.
            total_price=float(from_minor_units(int(row[16]))),  # Recupera el total original sin cálculos binarios.
            exchange_rate_applied=None if row[11] is None else float(row[11]),  # Recupera la tasa aplicada, si correspondía.
            total_paid=float(from_minor_units(int(row[15]))),  # Suma únicamente pagos confirmados en centésimos.
            balance_due=float(from_minor_units(max(0, int(row[16]) - int(row[15])))),  # Calcula el saldo con enteros, sin tolerancias flotantes.
            created_at=str(row[6]),  # Recupera la fecha de confirmación original.
            status=str(row[7]),  # Recupera estado actual: confirmado o cancelado.
            cancelled_at=None if row[8] is None else str(row[8]),  # Conserva fecha de cancelación cuando existe.
            payment_id="" if row[12] is None else str(row[12]),  # Recupera el pago más reciente cuando existe.
            payment_status="none" if row[13] is None else str(row[13]),  # Expone none para reservas sin pagos.
            payment_expires_at=None if row[14] is None else str(row[14]),  # Recupera el vencimiento del pago más reciente.
        )  # Entrega exactamente el recibo de la primera ejecución.

    @staticmethod
    def _receipt_from_values(
        reservation_id: str,
        rut: str,
        package_code: int,
        quantity: int,
        travel_date: str,
        unit_price: float,
        total_price: float,
        exchange_rate: float | None,
        created_at: str,
        payment_id: str,
    ) -> ReservationReceipt:
        """Construye el recibo inmutable de una reserva con pago pendiente."""
        return ReservationReceipt(  # Usa los mismos campos que se insertaron en SQLite.
            reservation_id=reservation_id,  # Incluye el identificador generado para esta compra.
            rut=rut,  # Vincula el recibo al RUT normalizado autenticado.
            package_code=package_code,  # Incluye el paquete comprado.
            quantity=quantity,  # Incluye los cupos descontados.
            travel_date=travel_date,  # Conserva la fecha de disponibilidad reservada.
            unit_price=unit_price,  # Incluye precio unitario aplicado.
            total_price=total_price,  # Incluye el importe total confirmado.
            exchange_rate_applied=exchange_rate,  # Conserva la cotización congelada en esta compra.
            total_paid=0.0,  # El pago inicial todavía está pendiente.
            balance_due=total_price,  # El saldo se reduce al confirmar cada pago.
            created_at=created_at,  # Incluye timestamp UTC guardado.
            payment_id=payment_id,  # Asocia al recibo el pago local creado con la reserva.
            payment_status="pending",  # La simulación comienza sin afirmar que hubo un cobro real.
            payment_expires_at=(datetime.fromisoformat(created_at) + timedelta(seconds=PAYMENT_PENDING_TTL_SECONDS)).isoformat(),  # Expone la fecha límite del pago creado.
        )  # Retorna el resultado de negocio recién confirmado.

    def get_available_capacity(self, package_code: int, travel_date: date | None = None) -> int | None:
        """Devuelve cupos para una fecha, o el total agregado si no se indica fecha."""
        with closing(self._connect()) as connection:  # Usa una conexión acotada solo para leer el inventario.
            if travel_date is None:
                row = connection.execute(
                    "SELECT SUM(total_capacity - reserved_capacity) FROM package_inventory WHERE package_code = ? AND travel_date != 'legacy'",
                    (package_code,),
                ).fetchone()
            else:
                normalized_date = self._normalize_travel_date(travel_date)
                row = connection.execute(
                    "SELECT total_capacity - reserved_capacity FROM package_inventory WHERE package_code = ? AND travel_date = ?",
                    (package_code, normalized_date),
                ).fetchone()
        return None if row is None or row[0] is None else int(row[0])  # Distingue inventario desconocido de una disponibilidad explícita de cero.

    def list_reservations(self, rut: str | None = None) -> list[ReservationReceipt]:
        """Lista todas las reservas para administración o solo las de un RUT."""
        query = """
            SELECT r.reservation_id, r.rut, r.package_code, r.quantity,
                   r.unit_price, r.total_price, r.created_at, r.status,
                   r.cancelled_at, r.request_hash, r.travel_date,
                   r.exchange_rate_applied,
                   (SELECT p.payment_id FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   (SELECT CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   (SELECT p.expires_at FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                   COALESCE((SELECT SUM(p.amount_minor) FROM payments AS p WHERE p.reservation_id = r.reservation_id AND p.status = 'confirmed'), 0),
                   r.total_price_minor, r.unit_price_minor
            FROM reservas AS r
        """
        if rut is None:  # None se reserva para el endpoint administrativo que ya validó rol.
            query += " ORDER BY r.created_at DESC, r.reservation_id DESC"  # Conserva orden estable sin duplicar reservas por cada pago.
            parameters: tuple[object, ...] = ()  # La consulta administrativa no agrega parámetros.
        else:  # Una consulta de cliente siempre queda acotada a la identidad del token.
            normalized_rut = normalize_rut(rut)  # Canonicaliza para que el filtro coincida con el dato persistido.
            query += " WHERE r.rut = ? ORDER BY r.created_at DESC, r.reservation_id DESC"  # La condición por RUT corre antes de devolver datos.
            parameters = (normalized_rut,)  # Parametriza el filtro de identidad.

        with closing(self._connect()) as connection:  # Abre una lectura corta sobre el archivo SQLite compartido.
            rows = connection.execute(query, parameters).fetchall()  # Obtiene recibos con su estado actual.
        return [self._receipt_from_row(row) for row in rows]  # Convierte todas las filas al modelo inmutable del servicio.

    def cancel_reservation(
        self,
        reservation_id: str,
        rut: str,
        *,
        is_admin: bool = False,
    ) -> ReservationReceipt:
        """Cancela una reserva y libera sus cupos una sola vez en una transacción."""
        normalized_rut = normalize_rut(rut)  # Normaliza el RUT de la identidad autenticada.
        if not isinstance(reservation_id, str) or not reservation_id.strip() or len(reservation_id) > 64:  # Limita el identificador recibido por la ruta.
            raise ValueError("El identificador de reserva no es válido.")  # Evita buscar con identificadores vacíos o excesivos.

        for attempt in range(1, self._max_write_attempts + 1):  # Aplica la misma política de retry acotado que la compra.
            try:  # Repite la transacción completa solo cuando SQLite reporta lock transitorio.
                return self._cancel_once(reservation_id.strip(), normalized_rut, is_admin)  # Ejecuta cancelación o reproduce el estado ya cancelado.
            except sqlite3.OperationalError as error:  # Clasifica errores de operación provenientes de SQLite.
                if not self._is_database_locked(error):  # No reintenta sintaxis, disco lleno ni otros errores permanentes.
                    raise PurchasePersistenceError("SQLite rechazó la cancelación de la reserva.") from error  # Expone un error de dominio y conserva la causa.
                if attempt == self._max_write_attempts:  # Comprueba si se consumieron todos los intentos configurados.
                    raise DatabaseBusyError("La base de datos sigue ocupada; reintente la cancelación.") from error  # Permite reintentar la operación de forma segura.
                time.sleep(self._retry_delay_seconds * attempt)  # Aplica espera incremental breve antes de abrir otra conexión.
            except sqlite3.IntegrityError as error:  # Trata restricciones incumplidas como fallos no transitorios.
                raise PurchasePersistenceError("La cancelación incumplió una restricción de persistencia.") from error  # No repite una cancelación estructuralmente inválida.

        raise DatabaseBusyError("No fue posible adquirir la base de datos para cancelar.")  # Salvaguarda defensiva para el bucle de reintentos.

    def _cancel_once(
        self,
        reservation_id: str,
        normalized_rut: str,
        is_admin: bool,
    ) -> ReservationReceipt:
        """Realiza la transición confirmada→cancelada y devuelve la capacidad."""
        with closing(self._connect()) as connection:  # Abre una conexión por intento y la cierra tras el commit/rollback.
            connection.execute("BEGIN IMMEDIATE")  # Serializa cancelación con compras y otras cancelaciones.
            try:  # Hace que cambio de estado y devolución de cupos sean atómicos.
                row = connection.execute(  # Recupera la reserva bajo bloqueo de escritura.
                    """
                    SELECT r.reservation_id, r.rut, r.package_code, r.quantity,
                           r.unit_price, r.total_price, r.created_at, r.status,
                           r.cancelled_at, r.request_hash, r.travel_date,
                           r.exchange_rate_applied,
                           (SELECT p.payment_id FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                           (SELECT CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                           (SELECT p.expires_at FROM payments AS p WHERE p.reservation_id = r.reservation_id ORDER BY p.created_at DESC, p.payment_id DESC LIMIT 1),
                           COALESCE((SELECT SUM(p.amount_minor) FROM payments AS p WHERE p.reservation_id = r.reservation_id AND p.status = 'confirmed'), 0),
                           r.total_price_minor, r.unit_price_minor
                    FROM reservas AS r
                    WHERE r.reservation_id = ?
                    """,
                    (reservation_id,),  # Busca únicamente el UUID solicitado.
                ).fetchone()  # Retorna None para UUID inexistente.
                if row is None or (not is_admin and row[1] != normalized_rut):  # Oculta existencia de reservas ajenas a clientes.
                    raise ReservationNotFoundError("No se encontró una reserva accesible.")  # Evita filtrar identificadores de otras personas.

                if row[7] == "cancelled":  # Un reintento de cancelación no debe liberar el inventario una segunda vez.
                    connection.commit()  # Finaliza la transacción de solo lectura.
                    return self._receipt_from_row(row)  # Devuelve el estado cancelado original como operación idempotente.
                if row[7] != "confirmed":  # Evita cambios implícitos desde estados futuros desconocidos.
                    raise PurchasePersistenceError("La reserva tiene un estado no cancelable.")  # Exige transiciones explícitas para estados nuevos.
                if int(row[15]) > 0:
                    raise PaymentTransitionConflictError(
                        "La reserva tiene pagos confirmados; no se puede cancelar sin un flujo de reembolso."
                    )

                cancelled_at = datetime.now(UTC).isoformat()  # Registra el instante UTC en que el estado cambia.
                changed = connection.execute(  # Cambia el estado con condición para garantizar una transición única.
                    """
                    UPDATE reservas
                    SET status = 'cancelled', cancelled_at = ?
                    WHERE reservation_id = ? AND status = 'confirmed'
                    """,
                    (cancelled_at, reservation_id),  # Guarda fecha y UUID como parámetros SQLite.
                ).rowcount  # Cuenta la transición confirmada→cancelada.
                if changed != 1:  # Protege contra cualquier estado inesperado aunque BEGIN IMMEDIATE serialice escritores.
                    raise PurchasePersistenceError("No se pudo cambiar el estado de la reserva.")  # No libera cupos si no hay transición.

                connection.execute(  # Cierra todos los intentos de pago pendientes de esta reserva.
                    """
                    UPDATE payments
                    SET status = 'failed', updated_at = ?
                    WHERE reservation_id = ? AND status = 'pending'
                    """,
                    (cancelled_at, reservation_id),  # Solo un pago pendiente cambia como efecto de una cancelación.
                )  # Un pago ya confirmado permanece como registro histórico; esta ruta no emite reembolsos.

                released = connection.execute(  # Devuelve solo la cantidad reservada de esa reserva.
                    """
                    UPDATE package_inventory
                    SET reserved_capacity = reserved_capacity - ?
                    WHERE package_code = ? AND travel_date = ? AND reserved_capacity >= ?
                    """,
                    (row[3], row[2], row[10], row[3]),  # Libera solo la fecha reservada y evita capacidad negativa.
                ).rowcount  # Comprueba que el inventario de ese paquete exista y tenía cupos suficientes reservados.
                if released != 1:  # Una discrepancia sería una corrupción entre recibo e inventario.
                    raise PurchasePersistenceError("El inventario no coincide con los cupos de la reserva.")  # Lanza antes del commit para revertir el estado.

                connection.commit()  # Confirma estado y recuperación de capacidad conjuntamente.
                return self._find_reservation_by_id(connection, reservation_id)  # Devuelve la reserva con todos sus pagos y saldos actualizados.
            except (ReservationNotFoundError, PaymentTransitionConflictError, PurchasePersistenceError):  # Revierte antes de propagar errores de dominio explícitos.
                connection.rollback()  # Conserva la reserva y los cupos si no pudo completar ambas operaciones.
                raise  # Propaga el error original para que la API lo traduzca explícitamente.
            except sqlite3.Error:  # Revierte explícitamente errores SQLite no traducidos antes de que el wrapper los clasifique.
                connection.rollback()  # Evita conservar escrituras parciales si falla una operación de persistencia.
                raise  # Deja que cancel_reservation aplique su política de retry/error.

    def get_payment(
        self,
        reservation_id: str,
        rut: str,
        *,
        payment_id: str | None = None,
        is_admin: bool = False,
    ) -> PaymentReceipt:
        """Devuelve un pago específico o el más reciente al dueño o administrador."""
        normalized_rut = normalize_rut(rut)  # Normaliza la identidad autenticada antes de comprobar propiedad.
        with closing(self._connect()) as connection:  # Abre una conexión de solo lectura para el estado persistido.
            row = connection.execute(  # Obtiene pago y propietario en una consulta para no filtrar reservas ajenas.
                """
                SELECT p.payment_id, p.reservation_id, p.amount_minor,
                       CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END,
                       p.created_at, p.updated_at, r.rut, p.expires_at
                FROM payments AS p
                JOIN reservas AS r ON r.reservation_id = p.reservation_id
                WHERE p.reservation_id = ?
                  AND (? IS NULL OR p.payment_id = ?)
                ORDER BY p.created_at DESC, p.payment_id DESC
                LIMIT 1
                """,
                (reservation_id, payment_id, payment_id),  # Filtra opcionalmente por el identificador preciso del pago.
            ).fetchone()  # Retorna None cuando reserva/pago no existe.
        if row is None or (not is_admin and row[6] != normalized_rut):  # Oculta existencia y titularidad ante usuarios no autorizados.
            raise ReservationNotFoundError("No se encontró un pago accesible.")  # Usa el mismo error para inexistencia y propiedad ajena.
        return self._payment_from_row((*row[:6], row[7]))  # Omite el RUT interno y entrega los campos públicos del pago local.

    def list_payments(
        self,
        reservation_id: str,
        rut: str,
        *,
        is_admin: bool = False,
    ) -> list[PaymentReceipt]:
        """Lista todos los pagos de una reserva al titular o a un administrador."""
        normalized_rut = normalize_rut(rut)
        with closing(self._connect()) as connection:
            reservation = connection.execute(
                "SELECT rut FROM reservas WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if reservation is None or (not is_admin and reservation[0] != normalized_rut):
                raise ReservationNotFoundError("No se encontró una reserva accesible.")
            rows = connection.execute(
                """
                SELECT payment_id, reservation_id, amount_minor,
                       CASE WHEN expired_at IS NOT NULL THEN 'expired' ELSE status END,
                       created_at, updated_at, expires_at
                FROM payments
                WHERE reservation_id = ?
                ORDER BY created_at, payment_id
                """,
                (reservation_id,),
            ).fetchall()
        return [self._payment_from_row(row) for row in rows]

    def create_payment(
        self,
        reservation_id: str,
        rut: str,
        amount: float | Decimal,
        *,
        is_admin: bool = False,
        idempotency_key: str | None = None,
    ) -> PaymentReceipt:
        """Crea un pago parcial dentro del saldo, con un único intento pendiente."""
        self.expire_pending_payments()
        normalized_rut = normalize_rut(rut)
        if isinstance(amount, bool) or not isinstance(amount, (int, float, Decimal)):
            raise ValueError("El monto del pago debe ser finito y mayor que cero.")
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        amount_minor = to_minor_units(amount)
        if amount_minor == 0:
            raise ValueError("El monto del pago debe ser al menos un centésimo.")
        request_hash = (
            hashlib.sha256(format(float(amount), ".17g").encode("ascii")).hexdigest()
            if normalized_key is not None
            else None
        )
        now = datetime.now(UTC)
        expires_at = (now + timedelta(seconds=PAYMENT_PENDING_TTL_SECONDS)).isoformat()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                reservation = connection.execute(
                    """
                    SELECT rut, total_price_minor, status,
                           COALESCE((SELECT SUM(amount_minor) FROM payments
                                     WHERE reservation_id = reservas.reservation_id
                                       AND status = 'confirmed'), 0),
                           EXISTS(SELECT 1 FROM payments
                                  WHERE reservation_id = reservas.reservation_id
                                    AND status = 'pending' AND expired_at IS NULL)
                    FROM reservas
                    WHERE reservation_id = ?
                    """,
                    (reservation_id,),
                ).fetchone()
                if reservation is None or (not is_admin and reservation[0] != normalized_rut):
                    raise ReservationNotFoundError("No se encontró una reserva accesible.")
                if normalized_key is not None:
                    previous = connection.execute(
                        """
                        SELECT payment_id, reservation_id, amount_minor, status,
                               created_at, updated_at, expires_at, request_hash
                        FROM payments
                        WHERE reservation_id = ? AND idempotency_key = ?
                        """,
                        (reservation_id, normalized_key),
                    ).fetchone()
                    if previous is not None:
                        if previous[7] != request_hash:
                            raise PaymentIdempotencyConflictError(
                                "La clave de idempotencia del pago ya se usó con otro monto."
                            )
                        connection.commit()
                        return self._payment_from_row(previous[:7])
                if reservation[2] != "confirmed":
                    raise PaymentTransitionConflictError("La reserva no está activa para recibir pagos.")
                if reservation[4]:
                    raise PaymentTransitionConflictError("La reserva ya tiene un pago pendiente.")
                balance_due_minor = int(reservation[1]) - int(reservation[3])
                if amount_minor > balance_due_minor:
                    raise ValueError("El monto supera el saldo pendiente de la reserva.")
                payment_id = str(uuid.uuid4())
                timestamp = now.isoformat()
                connection.execute(
                    """
                    INSERT INTO payments (
                        payment_id, reservation_id, amount, amount_minor, status,
                        created_at, updated_at, expires_at,
                        idempotency_key, request_hash
                    )
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                    """,
                    (
                        payment_id,
                        reservation_id,
                        float(from_minor_units(amount_minor)),
                        amount_minor,
                        timestamp,
                        timestamp,
                        expires_at,
                        normalized_key,
                        request_hash,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return PaymentReceipt(
            payment_id=payment_id,
            reservation_id=reservation_id,
            amount=float(from_minor_units(amount_minor)),
            status="pending",
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=expires_at,
        )

    def expire_pending_payments(self, *, now: datetime | None = None) -> int:
        """Marca vencidos pagos pendientes y libera sus reservas en una transacción."""
        current_time = now or datetime.now(UTC)  # Permite controlar el reloj en pruebas y usa UTC en la ejecución normal.
        if current_time.tzinfo is None or current_time.utcoffset() is None:  # Exige una hora consciente de zona para comparar fechas confiablemente.
            raise ValueError("now debe incluir zona horaria.")  # Evita interpretaciones ambiguas de vencimiento.
        current_time = current_time.astimezone(UTC)  # Normaliza fechas equivalentes al formato UTC persistido.
        current_iso = current_time.isoformat()  # Serializa una frontera estable para las consultas SQLite.

        for attempt in range(1, self._max_write_attempts + 1):  # Aplica reintentos breves ante contención con compras/confirmaciones.
            try:  # Ejecuta la expiración completa con reglas de retry consistentes con otras escrituras.
                return self._expire_pending_once(current_iso)  # Libera capacidad solamente para pagos que aún estaban pendientes.
            except sqlite3.OperationalError as error:  # Clasifica locks transitorios de SQLite.
                if not self._is_database_locked(error):  # No repite errores permanentes ni SQL inválido.
                    raise PurchasePersistenceError("SQLite rechazó la expiración de pagos.") from error  # Informa explícitamente el fallo técnico.
                if attempt == self._max_write_attempts:  # Comprueba el límite de intentos configurado.
                    raise DatabaseBusyError("La base de datos sigue ocupada al expirar pagos.") from error  # Permite que el ciclo siguiente vuelva a intentarlo.
                time.sleep(self._retry_delay_seconds * attempt)  # Espera incremental antes de reintentar el barrido.
            except sqlite3.IntegrityError as error:  # Trata restricciones fallidas como persistencia inválida, no como lock.
                raise PurchasePersistenceError("La expiración incumplió una restricción de persistencia.") from error  # Conserva el error técnico como causa.

        raise DatabaseBusyError("No fue posible adquirir la base para expirar pagos.")  # Salvaguarda defensiva del bucle acotado.

    def _expire_pending_once(self, current_iso: str) -> int:
        """Ejecuta un barrido atómico de pagos vencidos y cupos asociados."""
        with closing(self._connect()) as connection:  # Abre una conexión dedicada para esta transacción del worker.
            try:  # El rollback incluye los cambios de todos los pagos del mismo barrido.
                connection.execute("BEGIN IMMEDIATE")  # Serializa vencimientos con confirmaciones, cancelaciones y nuevas reservas.
                expired_rows = connection.execute(  # Captura solo pagos pendientes cuyo plazo ya terminó.
                    """
                    SELECT p.payment_id, p.reservation_id, r.package_code, r.quantity,
                           r.travel_date,
                           COALESCE((SELECT SUM(paid.amount_minor) FROM payments AS paid
                                     WHERE paid.reservation_id = r.reservation_id
                                       AND paid.status = 'confirmed'), 0)
                    FROM payments AS p
                    JOIN reservas AS r ON r.reservation_id = p.reservation_id
                    WHERE p.status = 'pending'
                      AND p.expired_at IS NULL
                      AND p.expires_at <= ?
                      AND r.status = 'confirmed'
                    ORDER BY p.expires_at, p.reservation_id
                    """,
                    (current_iso,),  # Limita el barrido a vencimientos hasta el instante UTC recibido.
                ).fetchall()  # El bloqueo evita que estos estados cambien antes de actualizarse.
                for payment_id, reservation_id, package_code, quantity, travel_date, total_paid in expired_rows:  # Resuelve el intento y la retención que aún dependa de él.
                    changed = connection.execute(  # Marca el pago vencido como terminal y conserva tanto fecha límite como instante de proceso.
                        """
                        UPDATE payments
                        SET status = 'failed', expired_at = ?, updated_at = ?
                        WHERE payment_id = ? AND status = 'pending' AND expired_at IS NULL
                        """,
                        (current_iso, current_iso, payment_id),  # Registra el momento del barrido sin reemplazar expires_at.
                    ).rowcount  # Verifica la transición pendiente→vencido aplicada.
                    if changed != 1:  # Evita liberar inventario si no se registró el vencimiento.
                        raise PurchasePersistenceError("No se pudo marcar el pago como vencido.")  # Rechaza una transición inconsistente.

                    if float(total_paid) > 0:  # Un abono confirmado conserva la reserva; solo vence este intento de pago.
                        continue  # La capacidad sigue retenida para que el cliente pueda continuar pagando el saldo.

                    cancelled = connection.execute(  # Cancela la reserva si venció antes de confirmar cualquier anticipo.
                        """
                        UPDATE reservas
                        SET status = 'cancelled', cancelled_at = ?
                        WHERE reservation_id = ? AND status = 'confirmed'
                        """,
                        (current_iso, reservation_id),  # Usa el mismo timestamp para auditar ambos cambios.
                    ).rowcount  # Comprueba que la reserva siga reteniendo capacidad.
                    if cancelled != 1:  # No permite contabilizar vencimiento sin cancelar la reserva.
                        raise PurchasePersistenceError("No se pudo cancelar la reserva del pago vencido.")  # Revierte también el estado de pago.

                    released = connection.execute(  # Devuelve al inventario los cupos que estaban retenidos.
                        """
                        UPDATE package_inventory
                        SET reserved_capacity = reserved_capacity - ?
                        WHERE package_code = ? AND travel_date = ? AND reserved_capacity >= ?
                        """,
                        (quantity, package_code, travel_date, quantity),  # Devuelve cupos solo para la fecha de viaje reservada.
                    ).rowcount  # Comprueba que existe capacidad suficiente para liberar.
                    if released != 1:  # Trata discrepancias como errores y revierte el barrido completo.
                        raise PurchasePersistenceError("El inventario no coincide con el pago vencido.")  # Evita desincronizar compra y disponibilidad.

                connection.commit()  # Persiste todas las expiraciones del ciclo o ninguna.
                return len(expired_rows)  # Informa cuántos pagos y reservas se vencieron en este barrido.
            except (PurchasePersistenceError, sqlite3.Error):  # Revertir explícitamente errores de dominio y SQLite del barrido.
                connection.rollback()  # Impide estados o liberaciones parciales incluso con varias reservas.
                raise  # Deja que expire_pending_payments aplique la traducción/retry correspondiente.

    def transition_payment(
        self,
        reservation_id: str,
        target_status: str,
        *,
        payment_id: str | None = None,
    ) -> PaymentReceipt:
        """Simula la transición de un pago pendiente identificado."""
        if target_status not in ("confirmed", "failed"):  # Solo se permiten resultados terminales desde la consola administrativa.
            raise ValueError("El estado de pago debe ser 'confirmed' o 'failed'.")  # No permite que una petición restaure pagos a pendiente.
        self.expire_pending_payments()  # Aplica vencimientos pendientes antes de aceptar un resultado administrativo.

        for attempt in range(1, self._max_write_attempts + 1):  # Reutiliza la política limitada de retry de escrituras del servicio.
            try:  # Repite únicamente bloqueos transitorios de SQLite.
                return self._transition_payment_once(reservation_id, target_status, payment_id)  # Cambia pago, reserva e inventario atómicamente.
            except sqlite3.OperationalError as error:  # Distingue un lock de otros errores SQL permanentes.
                if not self._is_database_locked(error):  # Evita reintentos de sintaxis, disco u otros fallos.
                    raise PurchasePersistenceError("SQLite rechazó la actualización del pago.") from error  # Traduce el error técnico a dominio.
                if attempt == self._max_write_attempts:  # Comprueba si ya se agotó el presupuesto de reintentos.
                    raise DatabaseBusyError("La base de datos sigue ocupada; reintente la actualización del pago.") from error  # Informa indisponibilidad temporal.
                time.sleep(self._retry_delay_seconds * attempt)  # Espera brevemente antes de repetir la transacción.
            except sqlite3.IntegrityError as error:  # No clasifica violaciones de esquema como bloqueos reintentables.
                raise PurchasePersistenceError("La actualización del pago incumplió una restricción.") from error  # Expone un fallo de persistencia controlado.

        raise DatabaseBusyError("No fue posible adquirir la base de datos para actualizar el pago.")  # Salvaguarda del bucle acotado.

    def _transition_payment_once(
        self,
        reservation_id: str,
        target_status: str,
        payment_id: str | None,
    ) -> PaymentReceipt:
        """Ejecuta el cambio del pago y cancela solo si aún no hubo abonos confirmados."""
        with closing(self._connect()) as connection:  # Abre una conexión nueva para esta tentativa transaccional.
            connection.execute("BEGIN IMMEDIATE")  # Serializa esta transición respecto a compras y cancelaciones.
            try:  # Mantiene pagos, reserva e inventario sincronizados frente a cualquier error.
                row = connection.execute(  # Lee el estado de pago y los datos requeridos para su transición.
                    """
                    SELECT p.payment_id, p.reservation_id, p.amount_minor, p.status,
                           p.created_at, p.updated_at, r.package_code,
                           r.quantity, r.status, p.expires_at, p.expired_at,
                           r.travel_date,
                           COALESCE((SELECT SUM(paid.amount_minor) FROM payments AS paid
                                     WHERE paid.reservation_id = r.reservation_id
                                       AND paid.status = 'confirmed'), 0),
                           r.total_price_minor
                    FROM payments AS p
                    JOIN reservas AS r ON r.reservation_id = p.reservation_id
                    WHERE p.reservation_id = ?
                      AND (? IS NULL OR p.payment_id = ?)
                    ORDER BY CASE WHEN p.status = 'pending' THEN 0 ELSE 1 END,
                             p.created_at DESC, p.payment_id DESC
                    LIMIT 1
                    """,
                    (reservation_id, payment_id, payment_id),  # Permite compatibilidad por reserva o seleccionar un pago exacto.
                ).fetchone()  # Retorna None si no existe una reserva con pago.
                if row is None:  # Distingue inexistencia antes de realizar escrituras.
                    raise ReservationNotFoundError("No se encontró la reserva con pago solicitado.")  # Permite a la API responder 404.
                if row[3] == target_status and row[10] is None:  # Repetir el mismo resultado no vencido es seguro ante reintentos de red.
                    connection.commit()  # Finaliza la lectura transaccional sin volver a modificar inventario.
                    return self._payment_from_row((*row[:6], row[9]))  # Reproduce el mismo pago terminal.
                if row[3] == "pending" and row[9] <= datetime.now(UTC).isoformat():  # Cierra la carrera en que vence tras el barrido previo.
                    raise PaymentTransitionConflictError("El plazo de pago venció y no se puede resolver manualmente.")  # El siguiente barrido libera sus cupos.
                if row[3] != "pending":  # Confirmado y fallido son estados terminales del flujo local.
                    raise PaymentTransitionConflictError("El pago ya fue resuelto y no admite otra transición.")  # Evita confirmar pagos fallidos o fallar pagos confirmados.
                if row[8] != "confirmed":  # La reserva debe seguir activa para poder cerrar el pago.
                    raise PaymentTransitionConflictError("La reserva no está activa para resolver su pago.")  # Evita resolver pagos de reservas canceladas.
                if target_status == "confirmed" and int(row[12]) + int(row[2]) > int(row[13]):
                    raise PaymentTransitionConflictError("El pago superaría el total de la reserva.")

                now = datetime.now(UTC).isoformat()  # Conserva la transición con fecha UTC auditable.
                updated_payment = connection.execute(  # Cambia el estado solo si sigue pendiente.
                    """
                    UPDATE payments
                    SET status = ?, updated_at = ?
                    WHERE payment_id = ? AND status = 'pending'
                    """,
                    (target_status, now, row[0]),  # Solo cambia el pago pendiente que se seleccionó y validó.
                ).rowcount  # Cuenta la transición aplicada.
                if updated_payment != 1:  # Detecta cualquier modificación inesperada del estado.
                    raise PaymentTransitionConflictError("El pago cambió antes de aplicar la transición.")  # No continúa con inventario si no cambió el pago.

                if target_status == "failed" and float(row[12]) <= 0:  # Si no hubo anticipo confirmado, el fallo inicial cancela y libera.
                    cancelled = connection.execute(  # Marca la reserva cancelada como parte de la misma transacción.
                        """
                        UPDATE reservas
                        SET status = 'cancelled', cancelled_at = ?
                        WHERE reservation_id = ? AND status = 'confirmed'
                        """,
                        (now, reservation_id),  # Registra el mismo instante UTC en pago y reserva.
                    ).rowcount  # Verifica que una reserva activa hizo la transición.
                    if cancelled != 1:  # No libera inventario si la reserva no quedó cancelada.
                        raise PaymentTransitionConflictError("No se pudo cancelar la reserva por pago fallido.")  # Evita estados de pago/reserva discordantes.
                    released = connection.execute(  # Devuelve los cupos retenidos de esta reserva al inventario.
                        """
                        UPDATE package_inventory
                        SET reserved_capacity = reserved_capacity - ?
                        WHERE package_code = ? AND travel_date = ? AND reserved_capacity >= ?
                        """,
                        (row[7], row[6], row[11], row[7]),  # Libera solo la fecha del viaje y evita inventario negativo.
                    ).rowcount  # Confirma que existe una fila de inventario consistente.
                    if released != 1:  # Trata discrepancias entre recibo e inventario como corrupción recuperable por rollback.
                        raise PurchasePersistenceError("El inventario no coincide con los cupos de la reserva.")  # Revierte pago fallido y cancelación.

                connection.commit()  # Persiste estado terminal y liberación eventual como una sola unidad.
                return PaymentReceipt(  # Construye la respuesta del resultado recién confirmado.
                    payment_id=str(row[0]),  # Conserva el identificador del pago existente.
                    reservation_id=str(row[1]),  # Vincula la respuesta a la reserva.
                    amount=float(from_minor_units(int(row[2]))),  # Devuelve el monto exacto, no una nueva cotización.
                    status=target_status,  # Refleja el nuevo resultado local.
                    created_at=str(row[4]),  # Conserva la creación original.
                    updated_at=now,  # Informa el timestamp UTC del cambio.
                    expires_at=None,  # Los pagos resueltos ya no tienen un vencimiento pendiente.
                )  # Entrega al endpoint el pago ya persistido.
            except (ReservationNotFoundError, PaymentTransitionConflictError, PurchasePersistenceError):  # Revierte fallos de dominio que ocurran antes del commit.
                connection.rollback()  # Evita dejar pagos o inventario parcialmente actualizados.
                raise  # Conserva el error para que la capa API elija la respuesta HTTP.
            except sqlite3.Error:  # Revierte fallos de persistencia antes de clasificarlos en transition_payment.
                connection.rollback()  # Mantiene atómicos pago, reserva e inventario.
                raise  # Permite que el wrapper reintente solo bloqueos BUSY/LOCKED.

    @staticmethod
    def _payment_from_row(row: tuple[Any, ...]) -> PaymentReceipt:
        """Convierte las columnas de SQLite al contrato inmutable de pago."""
        return PaymentReceipt(  # Normaliza tipos SQLite para el servicio y la API.
            payment_id=str(row[0]),  # Entrega el identificador local del pago.
            reservation_id=str(row[1]),  # Entrega el UUID de reserva relacionado.
            amount=float(from_minor_units(int(row[2]))),  # Expone el monto persistido como centésimos enteros.
            status=str(row[3]),  # Expone el estado local actual.
            created_at=str(row[4]),  # Expone el instante de creación.
            updated_at=str(row[5]),  # Expone el último cambio de estado.
            expires_at=None if row[6] is None or str(row[3]) != "pending" else str(row[6]),  # Solo informa deadline mientras el pago siga pendiente.
        )  # Retorna el recibo local de pago.

    def _build_package(self, row: tuple[Any, ...]) -> Paquete_Turistico:
        """Reconstruye el subtipo de paquete usando las columnas almacenadas."""
        return paquete_desde_columnas(row[0], row[1], row[2], row[3], row[4], row[5], row[6])  # Ignora las columnas de inventario unidas.

    def _connect(self) -> sqlite3.Connection:
        """Abre SQLite con espera breve para activar los reintentos del servicio."""
        connection = sqlite3.connect(self._database_path, timeout=0.1)  # Deja que la política acotada del servicio gestione bloqueos sin esperas largas implícitas.
        connection.execute("PRAGMA foreign_keys = ON")  # Activa las referencias entre inventario, reservas y catálogo en esta conexión.
        return connection  # Devuelve la conexión lista para consultas o transacciones.

    def _ensure_schema(self) -> None:
        """Crea inventario y reservas después de que la tabla paquetes ya exista."""
        with closing(self._connect()) as connection:  # Abre SQLite con claves foráneas y cierra el descriptor al terminar.
            connection.execute("BEGIN IMMEDIATE")  # Agrupa cambios de esquema y conversión de datos en una migración atómica.
            with connection:  # Confirma la creación de ambas tablas o revierte si falla el esquema.
                connection.execute(  # Define la capacidad vendible y el contador acumulado por paquete.
                    """
                    CREATE TABLE IF NOT EXISTS package_inventory (
                        package_code INTEGER NOT NULL,
                        travel_date TEXT NOT NULL,
                        total_capacity INTEGER NOT NULL CHECK (total_capacity >= 0),
                        reserved_capacity INTEGER NOT NULL DEFAULT 0
                            CHECK (reserved_capacity >= 0 AND reserved_capacity <= total_capacity),
                        PRIMARY KEY (package_code, travel_date),
                        FOREIGN KEY (package_code) REFERENCES paquetes (codigo)
                            ON DELETE CASCADE
                    )
                    """
                )  # La clave compuesta mantiene capacidad separada por paquete y fecha.
                inventory_columns = {
                    column[1]
                    for column in connection.execute("PRAGMA table_info(package_inventory)").fetchall()
                }
                if "travel_date" not in inventory_columns:
                    connection.execute("ALTER TABLE package_inventory RENAME TO package_inventory_legacy")
                    connection.execute(
                        """
                        CREATE TABLE package_inventory (
                            package_code INTEGER NOT NULL,
                            travel_date TEXT NOT NULL,
                            total_capacity INTEGER NOT NULL CHECK (total_capacity >= 0),
                            reserved_capacity INTEGER NOT NULL DEFAULT 0
                                CHECK (reserved_capacity >= 0 AND reserved_capacity <= total_capacity),
                            PRIMARY KEY (package_code, travel_date),
                            FOREIGN KEY (package_code) REFERENCES paquetes (codigo)
                                ON DELETE CASCADE
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO package_inventory (
                            package_code, travel_date, total_capacity, reserved_capacity
                        )
                        SELECT package_code, 'legacy', total_capacity, reserved_capacity
                        FROM package_inventory_legacy
                        """
                    )
                    connection.execute("DROP TABLE package_inventory_legacy")
                connection.execute(  # Define el recibo persistido para cada compra confirmada.
                    """
                    CREATE TABLE IF NOT EXISTS reservas (
                        reservation_id TEXT PRIMARY KEY,
                        rut TEXT NOT NULL,
                        package_code INTEGER NOT NULL,
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        unit_price REAL NOT NULL CHECK (unit_price > 0),
                        total_price REAL NOT NULL CHECK (total_price > 0),
                        unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor > 0),
                        total_price_minor INTEGER NOT NULL CHECK (total_price_minor > 0),
                        travel_date TEXT NOT NULL DEFAULT 'legacy',
                        exchange_rate_applied REAL,
                        created_at TEXT NOT NULL,
                        notification_email TEXT,
                        idempotency_key TEXT,
                        request_hash TEXT,
                        status TEXT NOT NULL DEFAULT 'confirmed'
                            CHECK (status IN ('confirmed', 'cancelled')),
                        cancelled_at TEXT,
                        FOREIGN KEY (package_code) REFERENCES paquetes (codigo)
                    )
                    """
                )  # Cada reserva conserva precio/cantidad acordados y admite clave opcional.
                existing_columns = {  # Obtiene las columnas actuales para migrar bases locales ya creadas.
                    column[1]
                    for column in connection.execute("PRAGMA table_info(reservas)").fetchall()
                }  # El nombre de columna está en la segunda posición del pragma SQLite.
                if "idempotency_key" not in existing_columns:  # Detecta el esquema previo que no tenía clave de repetición.
                    connection.execute("ALTER TABLE reservas ADD COLUMN idempotency_key TEXT")  # Agrega campo nullable sin modificar reservas históricas.
                if "request_hash" not in existing_columns:  # Detecta que falta la huella que enlaza clave con cuerpo.
                    connection.execute("ALTER TABLE reservas ADD COLUMN request_hash TEXT")  # Migra la base de forma aditiva y preserva los registros.
                if "status" not in existing_columns:  # Detecta una tabla de reservas anterior al ciclo de vida.
                    connection.execute("ALTER TABLE reservas ADD COLUMN status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled'))")  # Marca todas las reservas históricas como activas.
                if "cancelled_at" not in existing_columns:  # Detecta la ausencia de fecha de cancelación en el esquema legado.
                    connection.execute("ALTER TABLE reservas ADD COLUMN cancelled_at TEXT")  # Agrega campo nullable sin borrar filas existentes.
                if "travel_date" not in existing_columns:
                    connection.execute("ALTER TABLE reservas ADD COLUMN travel_date TEXT NOT NULL DEFAULT 'legacy'")
                if "exchange_rate_applied" not in existing_columns:
                    connection.execute("ALTER TABLE reservas ADD COLUMN exchange_rate_applied REAL")
                if "notification_email" not in existing_columns:
                    connection.execute("ALTER TABLE reservas ADD COLUMN notification_email TEXT")
                if "unit_price_minor" not in existing_columns:
                    connection.execute("ALTER TABLE reservas ADD COLUMN unit_price_minor INTEGER")
                if "total_price_minor" not in existing_columns:
                    connection.execute("ALTER TABLE reservas ADD COLUMN total_price_minor INTEGER")
                legacy_prices = connection.execute(
                    """
                    SELECT reservation_id, unit_price, total_price,
                           unit_price_minor, total_price_minor
                    FROM reservas
                    WHERE unit_price_minor IS NULL OR total_price_minor IS NULL
                    """
                ).fetchall()
                for reservation_id, unit_price, total_price, unit_minor, total_minor in legacy_prices:
                    connection.execute(
                        """
                        UPDATE reservas
                        SET unit_price_minor = ?, total_price_minor = ?
                        WHERE reservation_id = ?
                        """,
                        (
                            unit_minor if unit_minor is not None else to_minor_units(unit_price),
                            total_minor if total_minor is not None else to_minor_units(total_price),
                            reservation_id,
                        ),
                    )
                connection.execute(  # Crea el índice único que respalda la idempotencia dentro de reservas.
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_reservas_rut_idempotency
                    ON reservas (rut, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    """
                )  # El índice parcial permite múltiples reservas antiguas sin clave y una por clave/RUT.
                connection.execute(  # Crea la tabla de resultados locales sin integrar una pasarela ni datos bancarios.
                    """
                    CREATE TABLE IF NOT EXISTS payments (
                        payment_id TEXT PRIMARY KEY,
                        reservation_id TEXT NOT NULL,
                        amount REAL NOT NULL CHECK (amount > 0),
                        amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                        status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'failed')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        expired_at TEXT,
                        idempotency_key TEXT,
                        request_hash TEXT,
                        FOREIGN KEY (reservation_id) REFERENCES reservas (reservation_id)
                            ON DELETE CASCADE
                    )
                    """
                )  # Cada fila representa un pago independiente asociado a una reserva.
                payment_columns = {  # Inspecciona columnas para actualizar pagos creados por versiones anteriores.
                    column[1]
                    for column in connection.execute("PRAGMA table_info(payments)").fetchall()
                }  # SQLite informa cada nombre de columna en la segunda posición.
                if "expires_at" not in payment_columns:  # Detecta pagos existentes anteriores al vencimiento automático.
                    connection.execute("ALTER TABLE payments ADD COLUMN expires_at TEXT")  # Agrega el plazo sin eliminar estados de pago.
                if "expired_at" not in payment_columns:  # Detecta la ausencia del instante en que el worker procesa la expiración.
                    connection.execute("ALTER TABLE payments ADD COLUMN expired_at TEXT")  # Diferencia pagos vencidos de pagos fallidos manualmente.
                if "idempotency_key" not in payment_columns:
                    connection.execute("ALTER TABLE payments ADD COLUMN idempotency_key TEXT")
                if "request_hash" not in payment_columns:
                    connection.execute("ALTER TABLE payments ADD COLUMN request_hash TEXT")
                if "amount_minor" not in payment_columns:
                    connection.execute("ALTER TABLE payments ADD COLUMN amount_minor INTEGER")
                legacy_amounts = connection.execute(
                    "SELECT payment_id, amount FROM payments WHERE amount_minor IS NULL"
                ).fetchall()
                for payment_id, amount in legacy_amounts:
                    connection.execute(
                        "UPDATE payments SET amount_minor = ? WHERE payment_id = ?",
                        (to_minor_units(amount), payment_id),
                    )
                pending_without_deadline = connection.execute(
                    "SELECT payment_id, status, created_at FROM payments WHERE expires_at IS NULL"
                ).fetchall()
                for payment_id, payment_status, payment_created_at in pending_without_deadline:
                    created_datetime = datetime.fromisoformat(str(payment_created_at))
                    if created_datetime.tzinfo is None:
                        raise ValueError(f"El pago {payment_id} tiene una fecha de creación sin zona horaria.")
                    deadline = (
                        created_datetime + timedelta(seconds=PAYMENT_PENDING_TTL_SECONDS)
                    ).isoformat() if payment_status == "pending" else str(payment_created_at)
                    connection.execute(
                        "UPDATE payments SET expires_at = ? WHERE payment_id = ?",
                        (deadline, payment_id),
                    )
                unique_reservation_index = False
                for index in connection.execute("PRAGMA index_list(payments)").fetchall():
                    if index[2]:
                        index_name = str(index[1])
                        if not index_name or not index_name.replace("_", "").isalnum():
                            raise ValueError(f"Nombre de índice inesperado en payments: {index_name!r}")
                        indexed_columns = [
                            column[2]
                            for column in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
                        ]
                        if indexed_columns == ["reservation_id"]:
                            unique_reservation_index = True
                            break
                if unique_reservation_index:
                    connection.execute("ALTER TABLE payments RENAME TO payments_legacy_unique")
                    connection.execute(
                        """
                        CREATE TABLE payments (
                            payment_id TEXT PRIMARY KEY,
                            reservation_id TEXT NOT NULL,
                            amount REAL NOT NULL CHECK (amount > 0),
                            amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                            status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'failed')),
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL,
                            expired_at TEXT,
                            idempotency_key TEXT,
                            request_hash TEXT,
                            FOREIGN KEY (reservation_id) REFERENCES reservas (reservation_id)
                                ON DELETE CASCADE
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO payments (
                            payment_id, reservation_id, amount, amount_minor, status,
                            created_at, updated_at, expires_at, expired_at,
                            idempotency_key, request_hash
                        )
                        SELECT payment_id, reservation_id, amount, amount_minor, status,
                               created_at, updated_at, expires_at, expired_at,
                               idempotency_key, request_hash
                        FROM payments_legacy_unique
                        """
                    )
                    connection.execute("DROP TABLE payments_legacy_unique")
                connection.execute(  # Acelera la búsqueda periódica de pagos aún no resueltos.
                    """
                    CREATE INDEX IF NOT EXISTS idx_payments_pending_expiry
                    ON payments (status, expires_at)
                    """
                )  # Optimiza por estado y fecha de expiración.
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_one_pending_per_reservation
                    ON payments (reservation_id)
                    WHERE status = 'pending' AND expired_at IS NULL
                    """
                )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_reservation_idempotency
                    ON payments (reservation_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    """
                )
                connection.execute(  # Migra reservas anteriores como pagos históricos ya resueltos y no retiene inventario adicional.
                    """
                    INSERT INTO payments (
                        payment_id, reservation_id, amount, amount_minor, status, created_at,
                        updated_at, expires_at, expired_at
                    )
                    SELECT
                        'legacy-' || r.reservation_id,
                        r.reservation_id,
                        r.total_price,
                        r.total_price_minor,
                        CASE WHEN r.status = 'cancelled' THEN 'failed' ELSE 'confirmed' END,
                        r.created_at,
                        COALESCE(r.cancelled_at, r.created_at),
                        r.created_at,
                        NULL
                    FROM reservas AS r
                    WHERE NOT EXISTS (
                        SELECT 1 FROM payments AS p WHERE p.reservation_id = r.reservation_id
                    )
                    """
                )  # Conserva la semántica previa de reservas existentes como compras completadas o canceladas.
                OutboxRepository.ensure_schema(connection)
