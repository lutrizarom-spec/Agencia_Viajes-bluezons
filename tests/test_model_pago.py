"""Pruebas de las transiciones de estado del pago y de la boleta."""

import unittest
from datetime import date
from typing import Any, cast
from unittest import mock

from model.boleta import Boleta
from model.cliente import Cliente
from model.pago import Pago
from model.paquete_nacional import Paquete_Nacional
from model.reserva import Reserva


class PaymentStateTests(unittest.TestCase):
    """Verifica que las transiciones de estado de ``Pago`` respeten el dominio."""

    def setUp(self) -> None:
        client = Cliente(
            1,
            "10.000.013-K",
            "Camila Rojas",
            "+56912345678",
            "AB123456",
            "camila@example.com",
        )
        package = Paquete_Nacional(301, "Ruta del Norte", 3, 85000.0)
        self.reservation = Reserva(
            "RES-301",
            date(2026, 10, 6),
            date(2026, 12, 15),
            client,
            package,
        )
        self.reservation.agregar_detalle(2)
        self.payment = Pago(
            "PAGO-301",
            date(2026, 10, 6),
            self.reservation.total * 0.5,
            date(2026, 12, 15),
            self.reservation,
            9301,
        )

    def test_pending_to_confirmed(self) -> None:
        self.payment.estado = "confirmado"
        self.assertEqual(self.payment.estado, "confirmado")

    def test_pending_to_failed(self) -> None:
        self.payment.estado = "fallido"
        self.assertEqual(self.payment.estado, "fallido")

    def test_same_pending_state_is_allowed(self) -> None:
        self.payment.estado = "pendiente"
        self.assertEqual(self.payment.estado, "pendiente")

    def test_confirmed_to_pending_is_rejected(self) -> None:
        self.payment.estado = "confirmado"
        with self.assertRaises(ValueError):
            self.payment.estado = "pendiente"

    def test_confirmed_to_failed_is_rejected(self) -> None:
        self.payment.estado = "confirmado"
        with self.assertRaises(ValueError):
            self.payment.estado = "fallido"

    def test_failed_to_confirmed_is_rejected(self) -> None:
        self.payment.estado = "fallido"
        with self.assertRaises(ValueError):
            self.payment.estado = "confirmado"

    def test_invalid_state_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.payment.estado = "desconocido"


class ReceiptEmissionTests(unittest.TestCase):
    """Verifica que la emisión de la boleta no pueda revertirse."""

    def setUp(self) -> None:
        self.boleta = Boleta(9301, date(2026, 10, 6), 85000.0)

    def test_unemitted_receipt_can_be_emitted(self) -> None:
        self.assertFalse(self.boleta.emitida)
        self.boleta.emitir()
        self.assertTrue(self.boleta.emitida)

    def test_emitir_is_idempotent(self) -> None:
        self.boleta.emitir()
        self.boleta.emitir()
        self.assertTrue(self.boleta.emitida)

    def test_emitted_receipt_cannot_be_reverted(self) -> None:
        self.boleta.emitir()
        with self.assertRaises(ValueError):
            self.boleta.emitida = False

    def test_non_boolean_emission_state_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cast(Any, self.boleta).emitida = "si"


class PaymentProcessingTests(unittest.TestCase):
    """Verifica el procesamiento del pago y la ausencia de efectos duplicados."""

    def setUp(self) -> None:
        client = Cliente(
            1,
            "10.000.013-K",
            "Camila Rojas",
            "+56912345678",
            "AB123456",
            "camila@example.com",
        )
        self.reservation = Reserva(
            "RES-302",
            date(2026, 10, 6),
            date(2026, 12, 15),
            client,
            Paquete_Nacional(302, "Ruta del Sur", 4, 99000.0),
        )
        self.reservation.agregar_detalle(2)
        self.payment = Pago(
            "PAGO-302",
            date(2026, 10, 6),
            self.reservation.total * 0.5,
            date(2026, 12, 15),
            self.reservation,
            9302,
        )

    def test_processing_confirms_payment_and_emits_receipt(self) -> None:
        self.payment.procesar_pago()

        self.assertEqual(self.payment.estado, "confirmado")
        self.assertTrue(self.payment.boleta.emitida)

    def test_reprocessing_confirmed_payment_has_no_duplicate_effects(self) -> None:
        self.payment.procesar_pago()

        with mock.patch.object(self.reservation, "validar_anticipo") as validar:
            with mock.patch.object(self.payment.boleta, "emitir") as emitir:
                self.payment.procesar_pago()

        validar.assert_not_called()
        emitir.assert_not_called()
        self.assertEqual(self.payment.estado, "confirmado")

    def test_processing_failed_payment_is_rejected_without_effects(self) -> None:
        self.payment.estado = "fallido"

        with mock.patch.object(self.reservation, "validar_anticipo") as validar:
            with mock.patch.object(self.payment.boleta, "emitir") as emitir:
                with self.assertRaises(ValueError):
                    self.payment.procesar_pago()

        validar.assert_not_called()
        emitir.assert_not_called()
        self.assertEqual(self.payment.estado, "fallido")
        self.assertFalse(self.payment.boleta.emitida)


if __name__ == "__main__":
    unittest.main()
