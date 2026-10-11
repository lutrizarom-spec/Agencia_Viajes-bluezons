"""Pruebas de integridad bajo concurrencia real entre operaciones de CompraService.

Cada prueba usa una base SQLite temporal, varias conexiones independientes
(una por operacion del servicio) y sincronizacion explicita con threading.Barrier.
No se usan mocks: la contencion la produce SQLite con escritores reales.
"""

import sqlite3  # Abre conexiones de inspeccion independientes para verificar el estado final.
import tempfile  # Aisla cada escenario en una base descartable.
import threading  # Ejecuta operaciones simultaneas reales sobre la misma base.
import unittest  # Ejecuta las aserciones de integridad del dominio.
from contextlib import closing  # Cierra las conexiones de inspeccion y permite borrar la base temporal.
from datetime import date  # Fija la fecha de viaje usada por el inventario.
from pathlib import Path  # Construye rutas portables dentro de la carpeta temporal.

from dao.paquete_dao import PaqueteDao  # Crea el catalogo con el DAO real de la aplicacion.
from model.paquete_nacional import Paquete_Nacional  # Usa un paquete con precio determinista.
from services.compra_service import CompraService  # Servicio real bajo prueba con SQLite genuino.

TRAVEL_DATE = date(2026, 12, 15)  # Fecha de inventario compartida por las pruebas.
RUT = "10.000.013-K"  # RUT normalizado del cliente titular de las reservas.
CONCURRENCY_TIMEOUT = 20  # Tiempo maximo de espera para que ningun hilo quede colgado.
OLD_DEADLINE = "2000-01-01T00:00:00+00:00"  # Vencimiento determinista muy anterior a la fecha de prueba.


class ConcurrencyIntegrityTests(unittest.TestCase):
    """Verifica invariantes de inventario, reservas y pagos bajo concurrencia real."""

    def setUp(self) -> None:
        """Crea una base temporal con catalogo y paquete listos para operar."""
        self.temp_directory = tempfile.TemporaryDirectory()  # Evita tocar cualquier base real del proyecto.
        self.addCleanup(self.temp_directory.cleanup)  # Elimina la carpeta tras cerrar las conexiones.
        self.database_path = Path(self.temp_directory.name) / "concurrency.db"  # Base exclusiva de cada prueba.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Prepara el esquema minimo necesario.
            with connection:  # Confirma catalogo y paquete como una sola unidad.
                PaqueteDao(connection).crear_tabla()  # Crea la tabla de paquetes que referencia el inventario.
                PaqueteDao(connection).insertar_paquete(  # Inserta el paquete cuyos cupos se gestionaran.
                    Paquete_Nacional(101, "Paquete concurrencia", 2, 40000.0)
                )
        self.service = CompraService(self.database_path)  # Servicio real con la politica de retry por defecto.

    def _run_concurrently(self, operations: list) -> list:
        """Ejecuta operaciones simultaneas con una barrera de inicio y devuelve sus resultados."""
        barrier = threading.Barrier(len(operations))  # Sincroniza el inicio de todos los hilos.
        outcomes: list = [None] * len(operations)  # Conserva exito o excepcion por operacion.

        def runner(index: int, operation) -> None:
            """Espera la barrera y registra el resultado de una operacion."""
            try:  # Garantiza que ningun hilo invalide la barrera de forma silenciosa.
                barrier.wait(timeout=5)  # Inicia todas las operaciones lo mas cerca posible.
            except threading.BrokenBarrierError as error:  # Detecta si otro hilo no llego a tiempo.
                outcomes[index] = ("BrokenBarrierError", error)  # Expone el fallo de sincronizacion.
                return  # No ejecuta la operacion sin sincronizacion valida.
            try:  # Captura cualquier resultado para no abortar el resto de hilos.
                outcomes[index] = ("ok", operation())  # Ejecuta la operacion real sobre SQLite.
            except BaseException as error:  # Registra el tipo y la instancia del error.
                outcomes[index] = (type(error).__name__, error)  # Permite distinguir dominio de SQL crudo.

        threads = [  # Un hilo por operacion concurrente.
            threading.Thread(target=runner, args=(index, operation), daemon=True)
            for index, operation in enumerate(operations)
        ]
        for thread in threads:  # Arranca todas las operaciones.
            thread.start()
        for thread in threads:  # Limita la espera para no colgar la prueba.
            thread.join(timeout=CONCURRENCY_TIMEOUT)
        self.assertFalse(any(thread.is_alive() for thread in threads), "alguna operacion concurrente quedo colgada")  # Ningun hilo abandonado.
        return outcomes  # Entrega los resultados obtenidos por cada hilo.

    def _inventory(self) -> tuple[int, int]:
        """Lee la capacidad total y reservada del paquete y fecha de la prueba."""
        with closing(sqlite3.connect(self.database_path)) as connection:  # Conexion de solo lectura independiente.
            return connection.execute(  # Recupera la fila exacta de inventario.
                "SELECT total_capacity, reserved_capacity FROM package_inventory WHERE package_code = 101 AND travel_date = ?",
                (TRAVEL_DATE.isoformat(),),
            ).fetchone()

    def _reservation(self, reservation_id: str) -> tuple[str, str | None]:
        """Lee el estado y la fecha de cancelacion de una reserva."""
        with closing(sqlite3.connect(self.database_path)) as connection:  # Conexion de solo lectura independiente.
            return connection.execute(  # Recupera el estado persistido de la reserva.
                "SELECT status, cancelled_at FROM reservas WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()

    def _active_reserved_quantity(self) -> int:
        """Suma los cupos retenidos por reservas confirmadas del paquete de la prueba."""
        with closing(sqlite3.connect(self.database_path)) as connection:  # Conexion de solo lectura independiente.
            return int(  # Convierte el agregado nulo en cero.
                connection.execute(  # Suma solo reservas confirmadas, que son las que retienen cupos.
                    "SELECT COALESCE(SUM(quantity), 0) FROM reservas WHERE package_code = 101 AND status = 'confirmed'"
                ).fetchone()[0]
            )

    def _payment(self, payment_id: str) -> tuple[str, str | None]:
        """Lee el estado y la marca de vencimiento de un pago."""
        with closing(sqlite3.connect(self.database_path)) as connection:  # Conexion de solo lectura independiente.
            return connection.execute(  # Recupera el estado persistido del pago.
                "SELECT status, expired_at FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()

    def test_concurrent_independent_purchases_never_oversell_inventory(self) -> None:
        """Ocho compras independientes con claves distintas compiten por tres cupos sin sobrevender."""
        capacity = 3  # Define un inventario finito y conocido.
        self.service.configure_capacity(101, TRAVEL_DATE, capacity)  # Publica los cupos a competir.
        attempts = 8  # Lanza mas solicitudes que cupos disponibles.

        def purchase(index: int):
            """Realiza una compra independiente identificada por su propia clave."""
            return self.service.purchase_idempotently(  # Cada clave representa una operacion logicamente distinta.
                RUT, 101, 1, f"independent-key-{index}", travel_date=TRAVEL_DATE
            )

        outcomes = self._run_concurrently([lambda index=index: purchase(index) for index in range(attempts)])  # Compras simultaneas.
        successes = [outcome for outcome in outcomes if outcome[0] == "ok"]  # Operaciones confirmadas.
        under_capacity = [outcome for outcome in outcomes if outcome[0] == "InsufficientCapacityError"]  # Rechazos por falta de cupo.
        unexpected = [outcome for outcome in outcomes if outcome[0] not in ("ok", "InsufficientCapacityError")]  # Cualquier otro error.

        self.assertEqual([], unexpected, f"aparecieron errores no previstos: {[outcome[0] for outcome in unexpected]}")  # Sin locks agotados ni fallos.
        self.assertEqual(capacity, len(successes))  # Se confirman exactamente los cupos disponibles.
        self.assertEqual(attempts - capacity, len(under_capacity))  # El resto se rechaza por inventario.
        self.assertTrue(all(not outcome[1].replayed for outcome in successes))  # Con claves distintas no hay repeticiones.
        self.assertEqual(  # Cada compra confirmada crea una reserva distinta.
            capacity, len({outcome[1].receipt.reservation_id for outcome in successes})
        )

        total_capacity, reserved_capacity = self._inventory()  # Lee el inventario final.
        self.assertEqual(capacity, total_capacity)  # La capacidad total no cambio.
        self.assertEqual(capacity, reserved_capacity)  # Se retuvieron todos los cupos vendidos.
        self.assertGreaterEqual(reserved_capacity, 0)  # La capacidad nunca queda negativa.
        self.assertEqual(self._active_reserved_quantity(), reserved_capacity)  # El inventario concuerda con las reservas activas.

    def test_concurrent_double_cancel_releases_inventory_once(self) -> None:
        """Dos cancelaciones simultaneas de la misma reserva liberan los cupos una sola vez."""
        self.service.configure_capacity(101, TRAVEL_DATE, 2)  # Configura mas capacidad que la reservada.
        receipt = self.service.purchase_idempotently(RUT, 101, 1, "cancel-once-key", travel_date=TRAVEL_DATE).receipt  # Reserva sujeta a doble cancelacion.

        outcomes = self._run_concurrently([  # Dos intentos de cancelar exactamente la misma reserva.
            lambda: self.service.cancel_reservation(receipt.reservation_id, RUT),
            lambda: self.service.cancel_reservation(receipt.reservation_id, RUT),
        ])
        self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), [outcome[0] for outcome in outcomes])  # La cancelacion es idempotente.

        status, cancelled_at = self._reservation(receipt.reservation_id)  # Estado final de la reserva.
        self.assertEqual("cancelled", status)  # La reserva queda cancelada.
        self.assertIsNotNone(cancelled_at)  # Se registro la transicion una sola vez.
        total_capacity, reserved_capacity = self._inventory()  # Inventario tras liberar.
        self.assertEqual(2, total_capacity)  # La capacidad total permanece intacta.
        self.assertEqual(0, reserved_capacity)  # Solo se libero la cantidad reservada una vez.
        self.assertGreaterEqual(reserved_capacity, 0)  # No hay liberacion doble (cupos negativos).

    def test_concurrent_cancel_and_purchase_keep_inventory_consistent(self) -> None:
        """Una cancelacion y una compra simultaneas sobre el mismo paquete dejan inventario consistente."""
        self.service.configure_capacity(101, TRAVEL_DATE, 1)  # Un unico cupo compartido por ambas operaciones.
        first = self.service.purchase_idempotently(RUT, 101, 1, "cancel-race-key-a", travel_date=TRAVEL_DATE).receipt  # Reserva confirmada que se cancelara.

        outcomes = self._run_concurrently([  # Compiten por el mismo paquete y fecha.
            lambda: self.service.cancel_reservation(first.reservation_id, RUT),
            lambda: self.service.purchase_idempotently(RUT, 101, 1, "cancel-race-key-b", travel_date=TRAVEL_DATE),
        ])
        cancel_outcome, purchase_outcome = outcomes  # Separa los resultados para aserciones dirigidas.
        self.assertEqual("ok", cancel_outcome[0], cancel_outcome)  # La cancelacion no depende del resultado de la compra.
        self.assertIn(purchase_outcome[0], ("ok", "InsufficientCapacityError"), purchase_outcome)  # La compra se confirma o se rechaza por cupo.

        total_capacity, reserved_capacity = self._inventory()  # Inventario final tras la carrera.
        active_quantity = self._active_reserved_quantity()  # Cupos que las reservas activas aun retienen.
        self.assertEqual(1, total_capacity)  # La capacidad total no cambia.
        self.assertGreaterEqual(reserved_capacity, 0)  # La capacidad nunca es negativa.
        self.assertLessEqual(reserved_capacity, total_capacity)  # No puede exceder la capacidad total.
        self.assertEqual(active_quantity, reserved_capacity)  # El inventario coincide con las reservas confirmadas.

    def test_concurrent_confirm_and_fail_on_same_payment_end_in_one_terminal_state(self) -> None:
        """Confirmar y fallar el mismo pago en simultaneo produce un unico estado terminal coherente."""
        self.service.configure_capacity(101, TRAVEL_DATE, 1)  # Un cupo para la reserva bajo prueba.
        receipt = self.service.purchase_idempotently(RUT, 101, 1, "payment-race-key", travel_date=TRAVEL_DATE).receipt  # Reserva con pago pendiente.

        outcomes = self._run_concurrently([  # Compiten por resolver el mismo pago.
            lambda: self.service.transition_payment(receipt.reservation_id, "confirmed", payment_id=receipt.payment_id),
            lambda: self.service.transition_payment(receipt.reservation_id, "failed", payment_id=receipt.payment_id),
        ])
        succeeded = [outcome for outcome in outcomes if outcome[0] == "ok"]  # Solo uno debe aplicar la transicion.
        conflicted = [outcome for outcome in outcomes if outcome[0] == "PaymentTransitionConflictError"]  # El otro debe rechazarse.
        unexpected = [outcome for outcome in outcomes if outcome[0] not in ("ok", "PaymentTransitionConflictError")]  # Sin errores SQL crudos.
        self.assertEqual([], unexpected, f"errores no previstos: {[outcome[0] for outcome in unexpected]}")  # Contrato respetado.
        self.assertEqual(1, len(succeeded))  # Exactamente un resultado terminal aplicado.
        self.assertEqual(1, len(conflicted))  # El oponente no revierte el estado terminal.

        payment_status, expired_at = self._payment(receipt.payment_id)  # Estado final del pago.
        reservation_status, _ = self._reservation(receipt.reservation_id)  # Estado final de la reserva.
        total_capacity, reserved_capacity = self._inventory()  # Inventario final.
        self.assertIn(payment_status, ("confirmed", "failed"))  # El pago queda en un estado terminal.
        self.assertIsNone(expired_at)  # Ningun flujo de vencimiento participo en esta carrera.
        if payment_status == "confirmed":  # Un pago confirmado conserva la reserva y sus cupos.
            self.assertEqual("confirmed", reservation_status)  # La reserva sigue activa.
            self.assertEqual(1, reserved_capacity)  # El cupo permanece retenido.
        else:  # Un pago fallido sin abonos cancela la reserva y libera sus cupos.
            self.assertEqual("cancelled", reservation_status)  # La reserva se cancela.
            self.assertEqual(0, reserved_capacity)  # El cupo se devuelve una sola vez.
        self.assertEqual(self._active_reserved_quantity(), reserved_capacity)  # Inventario y reservas activas concuerdan.
        self.assertEqual(1, total_capacity)  # La capacidad total no cambia.

    def test_concurrent_double_expire_releases_inventory_once(self) -> None:
        """Dos barridos de expiracion simultaneos liberan una unica vez los cupos vencidos."""
        self.service.configure_capacity(101, TRAVEL_DATE, 1)  # Un cupo retenido por la reserva a vencer.
        receipt = self.service.purchase_idempotently(RUT, 101, 1, "expire-race-key", travel_date=TRAVEL_DATE).receipt  # Reserva con anticipo pendiente.
        with closing(sqlite3.connect(self.database_path)) as connection:  # Fuerza el vencimiento de forma determinista.
            with connection:  # Confirma el cambio de reloj de prueba.
                connection.execute(  # Antecede el limite para que el barrido lo considere vencido.
                    "UPDATE payments SET expires_at = ? WHERE payment_id = ?",
                    (OLD_DEADLINE, receipt.payment_id),
                )

        outcomes = self._run_concurrently([  # Dos barridos simultaneos sobre el mismo pago vencido.
            lambda: self.service.expire_pending_payments(),
            lambda: self.service.expire_pending_payments(),
        ])
        self.assertTrue(all(outcome[0] == "ok" for outcome in outcomes), [outcome[0] for outcome in outcomes])  # El barrido no falla.
        expired_counts = [outcome[1] for outcome in outcomes]  # Cantidades vencidas reportadas por cada barrido.
        self.assertEqual(1, sum(expired_counts))  # Entre ambos se vence exactamente un pago.

        payment_status, expired_at = self._payment(receipt.payment_id)  # Estado final del pago vencido.
        self.assertEqual("failed", payment_status)  # El pago queda como terminal fallido.
        self.assertIsNotNone(expired_at)  # Se registra el instante de vencimiento una sola vez.
        reservation_status, _ = self._reservation(receipt.reservation_id)  # Reserva asociada al pago vencido.
        self.assertEqual("cancelled", reservation_status)  # Sin abonos, el vencimiento cancela la reserva.
        total_capacity, reserved_capacity = self._inventory()  # Inventario tras el vencimiento.
        self.assertEqual(1, total_capacity)  # La capacidad total permanece igual.
        self.assertEqual(0, reserved_capacity)  # Los cupos se liberan una sola vez.


if __name__ == "__main__":  # Permite ejecutar el archivo directamente con Python.
    unittest.main()  # Descubre y ejecuta las clases de prueba definidas.
