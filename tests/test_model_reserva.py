"""Pruebas de las relaciones y reglas de negocio del modelo de reserva."""

import unittest
from datetime import date

from model.anticipo_insuficiente_error import AnticipoInsuficienteError
from model.cliente import Cliente
from model.detalle_reserva import Detalle_Reserva
from model.pago import Pago
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.pasaporte_requerido_error import PasaporteRequeridoError
from model.reserva import Reserva


class ReservationModelTests(unittest.TestCase):
    """Verifica agregación, composición, validaciones y reglas propias."""

    def setUp(self) -> None:
        self.client = Cliente(
            1,
            "10.000.013-K",
            "Camila Rojas",
            "+56912345678",
            "AB123456",
            "camila@example.com",
        )
        self.package = Paquete_Nacional(301, "Ruta del Norte", 3, 85000.0)
        self.reservation = Reserva(
            "RES-301",
            date(2026, 10, 6),
            date(2026, 12, 15),
            self.client,
            self.package,
        )

    def test_reservation_composes_details_and_aggregates_existing_objects(self) -> None:
        self.reservation.agregar_detalle(2)

        self.assertIs(self.reservation.cliente, self.client)
        self.assertIs(self.reservation.paquete, self.package)
        self.assertEqual(len(self.reservation.detalles), 1)
        self.assertIsInstance(self.reservation.detalles[0], Detalle_Reserva)
        self.assertIs(self.reservation.detalles[0].paquete, self.package)
        self.assertEqual(self.reservation.detalles[0].cantidad, 2)
        self.assertEqual(self.reservation.detalles[0].subtotal, 170000.0)
        self.assertEqual(self.reservation.total, 170000.0)
        with self.assertRaises(AttributeError):
            self.reservation.detalles.append(self.reservation.detalles[0])

    def test_reservation_composes_each_detail_internally(self) -> None:
        self.reservation.agregar_detalle(1)
        self.reservation.agregar_detalle(2)

        self.assertEqual(len(self.reservation.detalles), 2)
        self.assertEqual(self.reservation.total, 255000.0)
        self.assertEqual(
            [detail.id_detalle for detail in self.reservation.detalles],
            [1, 2],
        )

    def test_reservation_rejects_invalid_state_transitions_and_mutation(self) -> None:
        self.reservation.agregar_detalle(1)
        self.assertTrue(self.reservation.confirmar())
        with self.assertRaises(AttributeError):
            self.reservation.estado = "cancelada"
        with self.assertRaises(ValueError):
            self.reservation.agregar_detalle(1)
        self.assertTrue(self.reservation.cancelar())
        with self.assertRaises(ValueError):
            self.reservation.cancelar()

    def test_reservation_rejects_travel_before_booking_date(self) -> None:
        reservation = Reserva(
            "RES-305",
            date(2026, 12, 15),
            date(2026, 10, 6),
            self.client,
            self.package,
        )
        with self.assertRaisesRegex(ValueError, "no puede preceder"):
            reservation.agregar_detalle(1)

    def test_reservation_rejects_non_client_or_non_package_aggregation(self) -> None:
        with self.assertRaises(TypeError):
            Reserva(
                "RES-302",
                date(2026, 10, 6),
                date(2026, 12, 15),
                "cliente",
                self.package,
            )
        with self.assertRaises(TypeError):
            Reserva(
                "RES-303",
                date(2026, 10, 6),
                date(2026, 12, 15),
                self.client,
                "paquete",
            )

    def test_international_reservation_requires_registered_passport(self) -> None:
        client_without_passport = Cliente(
            2,
            "12.345.678-5",
            "Diego Soto",
            "+56987654321",
            "",
            "diego@example.com",
        )
        international = Paquete_Internacional(
            302, "Buenos Aires", 5, 250.0, True
        )
        reservation = Reserva(
            "RES-304",
            date(2026, 10, 6),
            date(2026, 12, 15),
            client_without_passport,
            international,
        )

        with self.assertRaises(PasaporteRequeridoError):
            reservation.agregar_detalle(1)
        self.assertFalse(client_without_passport.validar_pasaporte())

    def test_advance_must_be_at_least_half_of_reservation_total(self) -> None:
        self.reservation.agregar_detalle(2)

        with self.assertRaises(AnticipoInsuficienteError):
            self.reservation.validar_anticipo(self.reservation.total * 0.4)
        self.assertTrue(
            self.reservation.validar_anticipo(self.reservation.total * 0.5)
        )

    def test_payment_composes_and_emits_its_receipt(self) -> None:
        self.reservation.agregar_detalle(2)
        payment = Pago(
            "PAGO-301",
            date(2026, 10, 6),
            self.reservation.total * 0.5,
            date(2026, 12, 15),
            self.reservation,
            9301,
        )
        payment.procesar_pago()

        self.assertEqual(payment.estado, "confirmado")
        self.assertEqual(payment.boleta.num_boleta, 9301)
        self.assertTrue(payment.boleta.emitida)

    def test_setters_reject_invalid_client_values(self) -> None:
        with self.assertRaises(ValueError):
            self.client.telefono_movil = 123
        with self.assertRaises(ValueError):
            self.client.pasaporte = None
        with self.assertRaises(ValueError):
            self.client.nombre = " "


if __name__ == "__main__":
    unittest.main()
