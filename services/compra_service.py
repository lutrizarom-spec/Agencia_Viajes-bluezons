"""Compra atómica de cupos de paquetes turísticos con SQLite local."""

from __future__ import annotations

import hashlib  # Crea una huella compacta del cuerpo asociado a una clave idempotente.
import math  # Valida que el precio calculado sea un número finito y positivo.
import sqlite3  # Ejecuta el bloqueo y las escrituras transaccionales locales.
import time  # Espera brevemente antes de reintentar una transacción bloqueada.
import uuid  # Genera identificadores no predecibles para cada reserva.
from contextlib import closing  # Garantiza el cierre de conexiones SQLite.
from dataclasses import dataclass  # Define el resultado de compra como un valor inmutable.
from datetime import datetime, timedelta, timezone  # Registra creación y vencimiento de pagos en UTC.
from pathlib import Path  # Acepta rutas locales de base de datos.

from model.paquete_crucero import Paquete_Crucero  # Reconstruye la regla existente de precio de crucero.
from model.paquete_internacional import Paquete_Internacional  # Reconstruye la regla existente de precio internacional.
from model.paquete_nacional import Paquete_Nacional  # Reconstruye el modelo nacional sin cambiar su fórmula.
from model.paquete_turistico import Paquete_Turistico  # Proporciona la clase base para tipos genéricos.
from services.auth_service import normalize_rut  # Almacena el mismo RUT canónico que usa el sistema de login.

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


@dataclass(frozen=True)
class ReservationReceipt:
    """Contiene el recibo de cupos retenidos y su pago local asociado."""

    reservation_id: str  # UUID asignado a la reserva confirmada.
    rut: str  # RUT normalizado del usuario autenticado que compró.
    package_code: int  # Código del paquete comprado.
    quantity: int  # Cantidad de cupos consumidos por la compra.
    unit_price: float  # Precio por persona según la fórmula actual del subtipo.
    total_price: float  # Precio unitario multiplicado por la cantidad reservada.
    created_at: str  # Fecha UTC serializada de creación de la reserva.
    status: str = "confirmed"  # Estado persistido del ciclo de vida de la reserva.
    cancelled_at: str | None = None  # Fecha UTC de cancelación o None mientras siga confirmada.
    payment_id: str = ""  # Identificador local del registro de pago asociado.
    payment_status: str = "confirmed"  # Estado local pendiente, confirmado o fallido.
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
        max_write_attempts: int = 3,
        retry_delay_seconds: float = 0.05,
    ) -> None:
        """Prepara el servicio y configura una política acotada de reintentos."""
        if isinstance(max_write_attempts, bool) or not isinstance(max_write_attempts, int) or max_write_attempts < 1:  # Evita una política vacía o no entera.
            raise ValueError("max_write_attempts debe ser un entero positivo.")  # Informa el parámetro de reintentos esperado.
        if isinstance(retry_delay_seconds, bool) or not isinstance(retry_delay_seconds, (int, float)) or not math.isfinite(retry_delay_seconds) or retry_delay_seconds < 0:  # Acepta espera cero para pruebas, pero no tiempos negativos/no finitos.
            raise ValueError("retry_delay_seconds debe ser finito y no negativo.")  # Evita pausas no válidas.
        self._database_path = str(database_path)  # Conserva la ruta usada para cada operación independiente.
        self._max_write_attempts = max_write_attempts  # Limita la cantidad máxima de transacciones intentadas.
        self._retry_delay_seconds = float(retry_delay_seconds)  # Normaliza la pausa entre intentos.
        self._ensure_schema()  # Crea las tablas auxiliares después de que exista paquetes.

    def configure_capacity(self, package_code: int, total_capacity: int) -> int:
        """Define cupos de un paquete sin reducirlos por debajo de reservas confirmadas."""
        if isinstance(package_code, bool) or not isinstance(package_code, int) or not 1 <= package_code <= SQLITE_INTEGER_MAX:  # Rechaza códigos fuera del rango numérico de SQLite.
            raise ValueError("El código de paquete debe ser un entero positivo válido para SQLite.")  # Explica qué identificador se espera.
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
                    INSERT INTO package_inventory (package_code, total_capacity, reserved_capacity)
                    VALUES (?, ?, 0)
                    ON CONFLICT (package_code) DO UPDATE SET
                        total_capacity = excluded.total_capacity
                    WHERE excluded.total_capacity >= package_inventory.reserved_capacity
                    """,
                    (package_code, total_capacity),  # Parametriza el código y la capacidad configurada por el administrador.
                )  # El WHERE impide rebajar la capacidad por debajo de las ventas existentes.
                updated = connection.execute("SELECT changes()").fetchone()[0]  # Lee el conteo que SQLite devuelve para la última escritura.
                if updated == 0:  # Distingue el conflicto de capacidad del caso de inserción/actualización exitosa.
                    raise CapacityBelowReservedError("La capacidad no puede ser menor a los cupos ya reservados.")  # Protege consistencia de inventario.

                available = connection.execute(  # Calcula los cupos disponibles posteriores a la configuración.
                    "SELECT total_capacity - reserved_capacity FROM package_inventory WHERE package_code = ?",  # Lee la fila recién insertada o actualizada.
                    (package_code,),  # Limita la consulta al paquete configurado.
                ).fetchone()[0]  # Obtiene el entero de cupos que puede venderse.
                connection.commit()  # Confirma la nueva capacidad tras completar sus validaciones.
                return int(available)  # Devuelve al administrador el inventario vendible actual.
            except Exception:  # Captura errores solo para revertir y volver a propagar el fallo original.
                connection.rollback()  # Deshace cambios parciales para no dejar capacidad en estado incierto.
                raise  # No oculta la causa ni la convierte en un resultado de éxito.

    def purchase(self, rut: str, package_code: int, quantity: int) -> ReservationReceipt:
        """Confirma una compra no idempotente y conserva la firma pública existente."""
        result = self.purchase_idempotently(rut, package_code, quantity)  # Delega a la operación transaccional común sin clave idempotente.
        return result.receipt  # Conserva el tipo de retorno usado por los consumidores existentes.

    def purchase_idempotently(
        self,
        rut: str,
        package_code: int,
        quantity: int,
        idempotency_key: str | None = None,
    ) -> IdempotentPurchaseResult:
        """Compra una sola vez por RUT/clave y reproduce el recibo en reintentos."""
        normalized_rut = normalize_rut(rut)  # Asegura que el comprador quede vinculado al RUT autenticado normalizado.
        if isinstance(package_code, bool) or not isinstance(package_code, int) or not 1 <= package_code <= SQLITE_INTEGER_MAX:  # Valida el código dentro del rango SQLite.
            raise ValueError("El código de paquete debe ser un entero positivo válido para SQLite.")  # Rechaza valores que no son códigos persistibles.
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= SQLITE_INTEGER_MAX:  # Exige cupos positivos que quepan en SQLite.
            raise ValueError("La cantidad debe ser un entero positivo válido para SQLite.")  # Impide reservas vacías, negativas o fuera de rango.
        normalized_key = self._normalize_idempotency_key(idempotency_key)  # Valida y canoniza la clave opcional antes de acceder a SQLite.
        request_hash = hashlib.sha256(f"{package_code}:{quantity}".encode("ascii")).hexdigest() if normalized_key else None  # Vincula la clave al contenido semántico de la compra.

        for attempt in range(1, self._max_write_attempts + 1):  # Ejecuta el número de intentos acotado por configuración.
            try:  # Repite la transacción completa solo para bloqueos SQLite transitorios.
                receipt, replayed = self._purchase_once(normalized_rut, package_code, quantity, normalized_key, request_hash)  # Ejecuta una transacción indivisible o recupera el resultado anterior.
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
        idempotency_key: str | None,
        request_hash: str | None,
    ) -> tuple[ReservationReceipt, bool]:
        """Realiza una transacción de compra o lee la reserva ya confirmada."""
        reservation_id = str(uuid.uuid4())  # Genera un UUID distinto para una nueva compra; no se usa al reproducir.
        payment_id = str(uuid.uuid4())  # Crea el identificador del registro de pago simulado asociado a la reserva.
        created_datetime = datetime.now(timezone.utc)  # Toma una sola hora base para evitar pequeñas diferencias entre campos.
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
                               r.cancelled_at, r.request_hash, p.payment_id,
                               CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END,
                               p.expires_at
                        FROM reservas AS r
                        JOIN payments AS p ON p.reservation_id = r.reservation_id
                        WHERE r.rut = ? AND r.idempotency_key = ?
                        """,
                        (normalized_rut, idempotency_key),  # Acota la unicidad a la identidad autenticada y su clave.
                    ).fetchone()  # Lee recibo y huella previamente confirmados, si existen.
                    if previous is not None:  # Una coincidencia indica que la petición pudo ser un reintento tras perder respuesta.
                        if previous[9] != request_hash:  # Impide reutilizar la clave para comprar otro paquete o cantidad.
                            raise IdempotencyConflictError("La clave de idempotencia ya se usó con otra solicitud.")  # Informa conflicto sin modificar inventario.
                        connection.commit()  # Finaliza la transacción de lectura antes de devolver el resultado existente.
                        return self._receipt_from_row(previous), True  # Reproduce recibo y estado de pago sin descontar cupos.

                package_row = connection.execute(  # Recupera paquete y capacidad disponible bajo el bloqueo transaccional.
                    """
                    SELECT p.codigo, p.nombre, p.duracion, p.precio_base, p.tipo,
                           p.pasaporte_valido, p.impuesto_puerto,
                           i.total_capacity, i.reserved_capacity
                    FROM paquetes AS p
                    LEFT JOIN package_inventory AS i ON i.package_code = p.codigo
                    WHERE p.codigo = ? AND p.activo = 1
                    """,
                    (package_code,),  # Busca únicamente el paquete solicitado.
                ).fetchone()  # Lee producto e inventario en una sola consulta.
                if package_row is None:  # Distingue un código inexistente de una falta de cupos.
                    raise PackageNotFoundError("No existe el paquete solicitado.")  # La API traducirá esta condición a HTTP 404.
                if package_row[7] is None:  # Una fila NULL indica que un administrador aún no configuró capacidad.
                    raise InventoryNotConfiguredError("El paquete todavía no tiene cupos configurados.")  # Impide vender disponibilidad desconocida.

                available = int(package_row[7]) - int(package_row[8])  # Calcula cupos restantes mientras otras compras están bloqueadas.
                if quantity > available:  # Rechaza una cantidad superior al inventario.
                    raise InsufficientCapacityError("No hay cupos suficientes para la cantidad solicitada.")  # No genera recibo ni descuento.

                package = self._build_package(package_row)  # Reconstruye subtipo y conserva la regla de precio actual.
                unit_price = package.calcular_precio()  # Calcula el precio unitario con el método polimórfico original.
                if not math.isfinite(unit_price) or unit_price <= 0:  # Evita almacenar tasas/valores no finitos o no positivos.
                    raise InvalidPackagePriceError("El paquete tiene un precio inválido para la compra.")  # No cambia fórmulas de dominio.
                total_price = unit_price * quantity  # Calcula el monto que queda persistido en el recibo.
                if not math.isfinite(total_price) or total_price <= 0:  # Comprueba overflow y validez antes de enlazarlo a SQLite.
                    raise InvalidPackagePriceError("El precio total de la compra no es válido.")  # Evita persistir un importe corrupto.

                updated = connection.execute(  # Descuenta cupos mediante una condición atómica de capacidad disponible.
                    """
                    UPDATE package_inventory
                    SET reserved_capacity = reserved_capacity + ?
                    WHERE package_code = ?
                      AND total_capacity - reserved_capacity >= ?
                    """,
                    (quantity, package_code, quantity),  # Mantiene datos fuera del SQL y vuelve a comprobar capacidad al escribir.
                ).rowcount  # Cuenta cuántas filas afectó la actualización condicional.
                if updated != 1:  # Protege contra una condición que cambiase entre lectura y actualización.
                    raise InsufficientCapacityError("No hay cupos suficientes para la cantidad solicitada.")  # Previene sobreventa incluso ante escrituras concurrentes.

                connection.execute(  # Guarda el recibo asociado al descuento de cupos.
                    """
                    INSERT INTO reservas (
                        reservation_id, rut, package_code, quantity,
                        unit_price, total_price, created_at,
                        idempotency_key, request_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (reservation_id, normalized_rut, package_code, quantity, unit_price, total_price, created_at, idempotency_key, request_hash),  # Conserva compra y clave en una sola fila.
                )  # La referencia al catálogo impide reservas de paquetes inexistentes.
                connection.execute(  # Abre un pago local pendiente en la misma transacción que retiene los cupos.
                    """
                    INSERT INTO payments (
                        payment_id, reservation_id, amount, status, created_at, updated_at, expires_at
                    )
                    VALUES (?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (payment_id, reservation_id, total_price, created_at, created_at, expires_at),  # Congela monto, fechas y vencimiento del pago local.
                )  # Un fallo al insertar el pago revierte también la reserva y la retención de inventario.
                connection.commit()  # Confirma cupos, recibo y clave idempotente como una sola unidad.
            except Exception:  # Revierte dominio, persistencia u otros errores ocurridos antes del commit.
                connection.rollback()  # Evita reservar cupos sin un recibo/clave confirmados.
                raise  # Mantiene intacta la excepción original para su traducción posterior.

        return self._receipt_from_values(reservation_id, normalized_rut, package_code, quantity, unit_price, total_price, created_at, payment_id), False  # Devuelve reserva y pago pendiente recién creados.

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
    def _is_database_locked(error: sqlite3.OperationalError) -> bool:
        """Reconoce solo errores SQLite BUSY/LOCKED, nunca sintaxis u otros fallos."""
        error_code = getattr(error, "sqlite_errorcode", None)  # Obtiene el código extendido disponible en las versiones actuales de sqlite3.
        if error_code is not None:  # Prefiere el código estable cuando Python lo proporciona.
            primary_code = error_code & 0xFF  # SQLite guarda el código primario en los ocho bits inferiores.
            return primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)  # Solo bloqueos se consideran transitorios.
        message = str(error).casefold()  # Compatibilidad con runtimes antiguos que no exponen sqlite_errorcode.
        return "database is locked" in message or "database table is locked" in message  # Usa mensajes exactos conocidos como fallback limitado.

    @staticmethod
    def _receipt_from_row(row: tuple[object, ...]) -> ReservationReceipt:
        """Reconstruye un recibo persistido desde las columnas de reserva."""
        return ReservationReceipt(  # Normaliza los valores SQLite al contrato inmutable del servicio.
            reservation_id=str(row[0]),  # Recupera la clave única de la reserva.
            rut=str(row[1]),  # Recupera el RUT canónico persistido.
            package_code=int(row[2]),  # Recupera el código de catálogo.
            quantity=int(row[3]),  # Recupera la cantidad original comprada.
            unit_price=float(row[4]),  # Recupera el precio unitario acordado.
            total_price=float(row[5]),  # Recupera el total original, sin recalcularlo con precios actuales.
            created_at=str(row[6]),  # Recupera la fecha de confirmación original.
            status=str(row[7]),  # Recupera estado actual: confirmado o cancelado.
            cancelled_at=None if row[8] is None else str(row[8]),  # Conserva fecha de cancelación cuando existe.
            payment_id=str(row[10]),  # Recupera el identificador del pago local asociado.
            payment_status=str(row[11]),  # Recupera el estado vigente del pago.
            payment_expires_at=None if row[12] is None else str(row[12]),  # Recupera el vencimiento asociado al pago.
        )  # Entrega exactamente el recibo de la primera ejecución.

    @staticmethod
    def _receipt_from_values(
        reservation_id: str,
        rut: str,
        package_code: int,
        quantity: int,
        unit_price: float,
        total_price: float,
        created_at: str,
        payment_id: str,
    ) -> ReservationReceipt:
        """Construye el recibo inmutable de una reserva con pago pendiente."""
        return ReservationReceipt(  # Usa los mismos campos que se insertaron en SQLite.
            reservation_id=reservation_id,  # Incluye el identificador generado para esta compra.
            rut=rut,  # Vincula el recibo al RUT normalizado autenticado.
            package_code=package_code,  # Incluye el paquete comprado.
            quantity=quantity,  # Incluye los cupos descontados.
            unit_price=unit_price,  # Incluye precio unitario aplicado.
            total_price=total_price,  # Incluye el importe total confirmado.
            created_at=created_at,  # Incluye timestamp UTC guardado.
            payment_id=payment_id,  # Asocia al recibo el pago local creado con la reserva.
            payment_status="pending",  # La simulación comienza sin afirmar que hubo un cobro real.
            payment_expires_at=(datetime.fromisoformat(created_at) + timedelta(seconds=PAYMENT_PENDING_TTL_SECONDS)).isoformat(),  # Expone la fecha límite del pago creado.
        )  # Retorna el resultado de negocio recién confirmado.

    def get_available_capacity(self, package_code: int) -> int | None:
        """Devuelve cupos restantes o None si el paquete no tiene inventario configurado."""
        with closing(self._connect()) as connection:  # Usa una conexión acotada solo para leer el inventario.
            row = connection.execute(  # Calcula disponibilidad a partir de capacidad menos cupos ya vendidos.
                """
                SELECT total_capacity - reserved_capacity
                FROM package_inventory
                WHERE package_code = ?
                """,
                (package_code,),  # Limita la lectura al código consultado.
            ).fetchone()  # Obtiene cero o un resultado por la clave primaria.
        return None if row is None else int(row[0])  # Distingue inventario desconocido de una disponibilidad explícita de cero.

    def list_reservations(self, rut: str | None = None) -> list[ReservationReceipt]:
        """Lista todas las reservas para administración o solo las de un RUT."""
        if rut is None:  # None se reserva para el endpoint administrativo que ya validó rol.
            query = """
                SELECT r.reservation_id, r.rut, r.package_code, r.quantity,
                       r.unit_price, r.total_price, r.created_at, r.status,
                       r.cancelled_at, r.request_hash, p.payment_id,
                       CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END,
                       p.expires_at
                FROM reservas AS r
                JOIN payments AS p ON p.reservation_id = r.reservation_id
                ORDER BY r.created_at DESC, r.reservation_id DESC
            """  # Lista de forma estable todas las reservas sin aceptar filtro externo.
            parameters: tuple[object, ...] = ()  # La consulta administrativa no agrega parámetros.
        else:  # Una consulta de cliente siempre queda acotada a la identidad del token.
            normalized_rut = normalize_rut(rut)  # Canonicaliza para que el filtro coincida con el dato persistido.
            query = """
                SELECT r.reservation_id, r.rut, r.package_code, r.quantity,
                       r.unit_price, r.total_price, r.created_at, r.status,
                       r.cancelled_at, r.request_hash, p.payment_id,
                       CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END,
                       p.expires_at
                FROM reservas AS r
                JOIN payments AS p ON p.reservation_id = r.reservation_id
                WHERE r.rut = ?
                ORDER BY r.created_at DESC, r.reservation_id DESC
            """  # La condición por RUT se ejecuta en SQLite antes de devolver datos.
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
                           r.cancelled_at, r.request_hash, p.payment_id,
                           CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END,
                           p.expires_at
                    FROM reservas AS r
                    JOIN payments AS p ON p.reservation_id = r.reservation_id
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

                cancelled_at = datetime.now(timezone.utc).isoformat()  # Registra el instante UTC en que el estado cambia.
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

                connection.execute(  # Cierra el intento de pago abierto si se cancela antes de resolverlo.
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
                    WHERE package_code = ? AND reserved_capacity >= ?
                    """,
                    (row[3], row[2], row[3]),  # Impide que un error de datos deje capacidad reservada negativa.
                ).rowcount  # Comprueba que el inventario de ese paquete exista y tenía cupos suficientes reservados.
                if released != 1:  # Una discrepancia sería una corrupción entre recibo e inventario.
                    raise PurchasePersistenceError("El inventario no coincide con los cupos de la reserva.")  # Lanza antes del commit para revertir el estado.

                connection.commit()  # Confirma estado y recuperación de capacidad conjuntamente.
                return self._receipt_from_row((*row[:7], "cancelled", cancelled_at, row[9], row[10], "failed" if row[11] == "pending" else row[11], row[12]))  # Devuelve estados de reserva y pago actualizados.
            except (ReservationNotFoundError, PurchasePersistenceError):  # Revierte antes de propagar errores de dominio explícitos.
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
        is_admin: bool = False,
    ) -> PaymentReceipt:
        """Devuelve el estado del pago solo al dueño de la reserva o a un administrador."""
        normalized_rut = normalize_rut(rut)  # Normaliza la identidad autenticada antes de comprobar propiedad.
        with closing(self._connect()) as connection:  # Abre una conexión de solo lectura para el estado persistido.
            row = connection.execute(  # Obtiene pago y propietario en una consulta para no filtrar reservas ajenas.
                """
                SELECT p.payment_id, p.reservation_id, p.amount,
                       CASE WHEN p.expired_at IS NOT NULL THEN 'expired' ELSE p.status END,
                       p.created_at, p.updated_at, r.rut, p.expires_at
                FROM payments AS p
                JOIN reservas AS r ON r.reservation_id = p.reservation_id
                WHERE p.reservation_id = ?
                """,
                (reservation_id,),  # Parametriza el UUID de reserva.
            ).fetchone()  # Retorna None cuando reserva/pago no existe.
        if row is None or (not is_admin and row[6] != normalized_rut):  # Oculta existencia y titularidad ante usuarios no autorizados.
            raise ReservationNotFoundError("No se encontró un pago accesible.")  # Usa el mismo error para inexistencia y propiedad ajena.
        return self._payment_from_row((*row[:6], row[7]))  # Omite el RUT interno y entrega los campos públicos del pago local.

    def expire_pending_payments(self, *, now: datetime | None = None) -> int:
        """Marca vencidos pagos pendientes y libera sus reservas en una transacción."""
        current_time = now or datetime.now(timezone.utc)  # Permite controlar el reloj en pruebas y usa UTC en la ejecución normal.
        if current_time.tzinfo is None or current_time.utcoffset() is None:  # Exige una hora consciente de zona para comparar fechas confiablemente.
            raise ValueError("now debe incluir zona horaria.")  # Evita interpretaciones ambiguas de vencimiento.
        current_time = current_time.astimezone(timezone.utc)  # Normaliza fechas equivalentes al formato UTC persistido.
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
                    SELECT p.payment_id, p.reservation_id, r.package_code, r.quantity
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
                for payment_id, reservation_id, package_code, quantity in expired_rows:  # Resuelve cada pago y su retención asociada.
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

                    cancelled = connection.execute(  # Cancela la reserva asociada al pago ya vencido.
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
                        WHERE package_code = ? AND reserved_capacity >= ?
                        """,
                        (quantity, package_code, quantity),  # Impide que la liberación deje capacidad reservada negativa.
                    ).rowcount  # Comprueba que existe capacidad suficiente para liberar.
                    if released != 1:  # Trata discrepancias como errores y revierte el barrido completo.
                        raise PurchasePersistenceError("El inventario no coincide con el pago vencido.")  # Evita desincronizar compra y disponibilidad.

                connection.commit()  # Persiste todas las expiraciones del ciclo o ninguna.
                return len(expired_rows)  # Informa cuántos pagos y reservas se vencieron en este barrido.
            except (PurchasePersistenceError, sqlite3.Error):  # Revertir explícitamente errores de dominio y SQLite del barrido.
                connection.rollback()  # Impide estados o liberaciones parciales incluso con varias reservas.
                raise  # Deja que expire_pending_payments aplique la traducción/retry correspondiente.

    def transition_payment(self, reservation_id: str, target_status: str) -> PaymentReceipt:
        """Simula una transición administrativa pendiente→confirmado o pendiente→fallido."""
        if target_status not in ("confirmed", "failed"):  # Solo se permiten resultados terminales desde la consola administrativa.
            raise ValueError("El estado de pago debe ser 'confirmed' o 'failed'.")  # No permite que una petición restaure pagos a pendiente.
        self.expire_pending_payments()  # Aplica vencimientos pendientes antes de aceptar un resultado administrativo.

        for attempt in range(1, self._max_write_attempts + 1):  # Reutiliza la política limitada de retry de escrituras del servicio.
            try:  # Repite únicamente bloqueos transitorios de SQLite.
                return self._transition_payment_once(reservation_id, target_status)  # Cambia pago, reserva e inventario atómicamente.
            except sqlite3.OperationalError as error:  # Distingue un lock de otros errores SQL permanentes.
                if not self._is_database_locked(error):  # Evita reintentos de sintaxis, disco u otros fallos.
                    raise PurchasePersistenceError("SQLite rechazó la actualización del pago.") from error  # Traduce el error técnico a dominio.
                if attempt == self._max_write_attempts:  # Comprueba si ya se agotó el presupuesto de reintentos.
                    raise DatabaseBusyError("La base de datos sigue ocupada; reintente la actualización del pago.") from error  # Informa indisponibilidad temporal.
                time.sleep(self._retry_delay_seconds * attempt)  # Espera brevemente antes de repetir la transacción.
            except sqlite3.IntegrityError as error:  # No clasifica violaciones de esquema como bloqueos reintentables.
                raise PurchasePersistenceError("La actualización del pago incumplió una restricción.") from error  # Expone un fallo de persistencia controlado.

        raise DatabaseBusyError("No fue posible adquirir la base de datos para actualizar el pago.")  # Salvaguarda del bucle acotado.

    def _transition_payment_once(self, reservation_id: str, target_status: str) -> PaymentReceipt:
        """Ejecuta el cambio de pago y, si falla, cancela/libera cupos en la misma transacción."""
        with closing(self._connect()) as connection:  # Abre una conexión nueva para esta tentativa transaccional.
            connection.execute("BEGIN IMMEDIATE")  # Serializa esta transición respecto a compras y cancelaciones.
            try:  # Mantiene pagos, reserva e inventario sincronizados frente a cualquier error.
                row = connection.execute(  # Lee el estado de pago y los datos requeridos para su transición.
                    """
                    SELECT p.payment_id, p.reservation_id, p.amount, p.status,
                           p.created_at, p.updated_at, r.package_code,
                           r.quantity, r.status, p.expires_at, p.expired_at
                    FROM payments AS p
                    JOIN reservas AS r ON r.reservation_id = p.reservation_id
                    WHERE p.reservation_id = ?
                    """,
                    (reservation_id,),  # Solo usa el identificador proporcionado por la ruta administrativa.
                ).fetchone()  # Retorna None si no existe una reserva con pago.
                if row is None:  # Distingue inexistencia antes de realizar escrituras.
                    raise ReservationNotFoundError("No se encontró la reserva con pago solicitado.")  # Permite a la API responder 404.
                if row[3] == target_status and row[10] is None:  # Repetir el mismo resultado no vencido es seguro ante reintentos de red.
                    connection.commit()  # Finaliza la lectura transaccional sin volver a modificar inventario.
                    return self._payment_from_row((*row[:6], row[9]))  # Reproduce el mismo pago terminal.
                if row[3] == "pending" and row[9] <= datetime.now(timezone.utc).isoformat():  # Cierra la carrera en que vence tras el barrido previo.
                    raise PaymentTransitionConflictError("El plazo de pago venció y no se puede resolver manualmente.")  # El siguiente barrido libera sus cupos.
                if row[3] != "pending":  # Confirmado y fallido son estados terminales del flujo local.
                    raise PaymentTransitionConflictError("El pago ya fue resuelto y no admite otra transición.")  # Evita confirmar pagos fallidos o fallar pagos confirmados.
                if row[8] != "confirmed":  # La reserva debe seguir activa para poder cerrar el pago.
                    raise PaymentTransitionConflictError("La reserva no está activa para resolver su pago.")  # Evita resolver pagos de reservas canceladas.

                now = datetime.now(timezone.utc).isoformat()  # Conserva la transición con fecha UTC auditable.
                updated_payment = connection.execute(  # Cambia el estado solo si sigue pendiente.
                    """
                    UPDATE payments
                    SET status = ?, updated_at = ?
                    WHERE reservation_id = ? AND status = 'pending'
                    """,
                    (target_status, now, reservation_id),  # Escribe el resultado validado y su fecha de cambio.
                ).rowcount  # Cuenta la transición aplicada.
                if updated_payment != 1:  # Detecta cualquier modificación inesperada del estado.
                    raise PaymentTransitionConflictError("El pago cambió antes de aplicar la transición.")  # No continúa con inventario si no cambió el pago.

                if target_status == "failed":  # Un pago fallido termina la reserva y libera los cupos retenidos.
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
                        WHERE package_code = ? AND reserved_capacity >= ?
                        """,
                        (row[7], row[6], row[7]),  # Asegura que no se reste más capacidad de la que figura reservada.
                    ).rowcount  # Confirma que existe una fila de inventario consistente.
                    if released != 1:  # Trata discrepancias entre recibo e inventario como corrupción recuperable por rollback.
                        raise PurchasePersistenceError("El inventario no coincide con los cupos de la reserva.")  # Revierte pago fallido y cancelación.

                connection.commit()  # Persiste estado terminal y liberación eventual como una sola unidad.
                return PaymentReceipt(  # Construye la respuesta del resultado recién confirmado.
                    payment_id=str(row[0]),  # Conserva el identificador del pago existente.
                    reservation_id=str(row[1]),  # Vincula la respuesta a la reserva.
                    amount=float(row[2]),  # Devuelve el monto congelado, no una nueva cotización.
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
    def _payment_from_row(row: tuple[object, ...]) -> PaymentReceipt:
        """Convierte las columnas de SQLite al contrato inmutable de pago."""
        return PaymentReceipt(  # Normaliza tipos SQLite para el servicio y la API.
            payment_id=str(row[0]),  # Entrega el identificador local del pago.
            reservation_id=str(row[1]),  # Entrega el UUID de reserva relacionado.
            amount=float(row[2]),  # Expone el monto total original de la reserva.
            status=str(row[3]),  # Expone el estado local actual.
            created_at=str(row[4]),  # Expone el instante de creación.
            updated_at=str(row[5]),  # Expone el último cambio de estado.
            expires_at=None if row[6] is None or str(row[3]) != "pending" else str(row[6]),  # Solo informa deadline mientras el pago siga pendiente.
        )  # Retorna el recibo local de pago.

    def _build_package(self, row: tuple[object, ...]) -> Paquete_Turistico:
        """Reconstruye el subtipo de paquete usando las columnas almacenadas."""
        codigo, nombre, duracion, precio_base, tipo, pasaporte, impuesto, *_ = row  # Separa las columnas del paquete y descarta el inventario unido.
        if tipo == "internacional":  # Reconoce el modelo que conserva el dato de pasaporte.
            return Paquete_Internacional(codigo, nombre, duracion, precio_base, bool(pasaporte))  # Aplica la fórmula internacional original sin modificaciones.
        if tipo == "crucero":  # Reconoce el subtipo con impuesto portuario.
            return Paquete_Crucero(codigo, nombre, duracion, precio_base, impuesto)  # Preserva la fórmula de crucero existente.
        if tipo == "nacional":  # Reconoce el paquete nacional que no aplica multiplicador.
            return Paquete_Nacional(codigo, nombre, duracion, precio_base)  # Retorna el modelo nacional con su cálculo actual.
        return Paquete_Turistico(codigo, nombre, duracion, precio_base)  # Usa comportamiento base para paquetes genéricos antiguos.

    def _connect(self) -> sqlite3.Connection:
        """Abre SQLite con espera breve para activar los reintentos del servicio."""
        connection = sqlite3.connect(self._database_path, timeout=0.1)  # Deja que la política acotada del servicio gestione bloqueos sin esperas largas implícitas.
        connection.execute("PRAGMA foreign_keys = ON")  # Activa las referencias entre inventario, reservas y catálogo en esta conexión.
        return connection  # Devuelve la conexión lista para consultas o transacciones.

    def _ensure_schema(self) -> None:
        """Crea inventario y reservas después de que la tabla paquetes ya exista."""
        with closing(self._connect()) as connection:  # Abre SQLite con claves foráneas y cierra el descriptor al terminar.
            with connection:  # Confirma la creación de ambas tablas o revierte si falla el esquema.
                connection.execute(  # Define la capacidad vendible y el contador acumulado por paquete.
                    """
                    CREATE TABLE IF NOT EXISTS package_inventory (
                        package_code INTEGER PRIMARY KEY,
                        total_capacity INTEGER NOT NULL CHECK (total_capacity >= 0),
                        reserved_capacity INTEGER NOT NULL DEFAULT 0
                            CHECK (reserved_capacity >= 0 AND reserved_capacity <= total_capacity),
                        FOREIGN KEY (package_code) REFERENCES paquetes (codigo)
                            ON DELETE CASCADE
                    )
                    """
                )  # La restricción de fila impide capacidad negativa o más reservados que el total.
                connection.execute(  # Define el recibo persistido para cada compra confirmada.
                    """
                    CREATE TABLE IF NOT EXISTS reservas (
                        reservation_id TEXT PRIMARY KEY,
                        rut TEXT NOT NULL,
                        package_code INTEGER NOT NULL,
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        unit_price REAL NOT NULL CHECK (unit_price > 0),
                        total_price REAL NOT NULL CHECK (total_price > 0),
                        created_at TEXT NOT NULL,
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
                        reservation_id TEXT NOT NULL UNIQUE,
                        amount REAL NOT NULL CHECK (amount > 0),
                        status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'failed')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        expired_at TEXT,
                        FOREIGN KEY (reservation_id) REFERENCES reservas (reservation_id)
                            ON DELETE CASCADE
                    )
                    """
                )  # Cada reserva posee como máximo un pago local con estados controlados.
                payment_columns = {  # Inspecciona columnas para actualizar pagos creados por versiones anteriores.
                    column[1]
                    for column in connection.execute("PRAGMA table_info(payments)").fetchall()
                }  # SQLite informa cada nombre de columna en la segunda posición.
                if "expires_at" not in payment_columns:  # Detecta pagos existentes anteriores al vencimiento automático.
                    connection.execute("ALTER TABLE payments ADD COLUMN expires_at TEXT")  # Agrega el plazo sin eliminar estados de pago.
                if "expired_at" not in payment_columns:  # Detecta la ausencia del instante en que el worker procesa la expiración.
                    connection.execute("ALTER TABLE payments ADD COLUMN expired_at TEXT")  # Diferencia pagos vencidos de pagos fallidos manualmente.
                pending_without_deadline = connection.execute(  # Selecciona filas viejas que necesitan calcular fecha límite.
                    "SELECT payment_id, status, created_at FROM payments WHERE expires_at IS NULL"
                ).fetchall()  # Resuelve pendientes con su hora de creación original.
                for payment_id, payment_status, payment_created_at in pending_without_deadline:  # Migra cada pago sin deadline de forma explícita.
                    created_datetime = datetime.fromisoformat(str(payment_created_at))  # Interpreta el timestamp UTC persistido por versiones previas.
                    if created_datetime.tzinfo is None:  # Rechaza datos históricos ambiguos en vez de asumir su zona horaria.
                        raise ValueError(f"El pago {payment_id} tiene una fecha de creación sin zona horaria.")  # Detiene la migración si no se puede calcular con seguridad.
                    deadline = (created_datetime + timedelta(seconds=PAYMENT_PENDING_TTL_SECONDS)).isoformat() if payment_status == "pending" else str(payment_created_at)  # Da a pendientes el TTL y evita expirar pagos ya resueltos.
                    connection.execute(  # Guarda la fecha límite calculada sin alterar estado ni monto.
                        "UPDATE payments SET expires_at = ? WHERE payment_id = ?",
                        (deadline, payment_id),  # Usa parámetros para el identificador y la fecha.
                    )  # Los pagos pendientes antiguos se podrán expirar en el barrido inicial.
                connection.execute(  # Acelera la búsqueda periódica de pagos aún no resueltos.
                    """
                    CREATE INDEX IF NOT EXISTS idx_payments_pending_expiry
                    ON payments (status, expires_at)
                    """
                )  # Optimiza por estado y fecha de expiración.
                connection.execute(  # Migra reservas anteriores como pagos históricos ya resueltos y no retiene inventario adicional.
                    """
                    INSERT INTO payments (
                        payment_id, reservation_id, amount, status, created_at,
                        updated_at, expires_at, expired_at
                    )
                    SELECT
                        'legacy-' || r.reservation_id,
                        r.reservation_id,
                        r.total_price,
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
