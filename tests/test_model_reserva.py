"""Pruebas de las relaciones entre Reserva, DetalleReserva y paquetes."""

import unittest

from model.detalle_reserva import DetalleReserva
from model.paquete_nacional import Paquete_Nacional
from model.reserva import Reserva


class ReservationModelTests(unittest.TestCase):
    """Comprueba composición, agregación y validación del modelo de reserva."""

    def test_reservation_composes_details_and_aggregates_existing_package(self) -> None:
        package = Paquete_Nacional(301, "Ruta del Norte", 3, 85000.0)
        reservation = Reserva("10.000.013-K", package, 2)

        self.assertIs(reservation.paquete, package)
        self.assertEqual(reservation.rut_cliente, "10.000.013-K")
        self.assertEqual(len(reservation.detalles), 1)
        self.assertIsInstance(reservation.detalles[0], DetalleReserva)
        self.assertIs(reservation.detalles[0].paquete, package)
        self.assertEqual(reservation.detalles[0].cantidad, 2)
        self.assertEqual(reservation.detalles[0].subtotal, 170000.0)
        self.assertEqual(reservation.total, 170000.0)
        with self.assertRaises(AttributeError):
            reservation.detalles.append(DetalleReserva(package, 1))

    def test_reservation_can_compose_another_detail(self) -> None:
        first_package = Paquete_Nacional(302, "Ruta del Sur", 3, 70000.0)
        second_package = Paquete_Nacional(303, "Ruta del Norte", 2, 50000.0)
        reservation = Reserva("10.000.013-K", first_package, 1)

        reservation.agregar_detalle(second_package, 2)

        self.assertEqual(len(reservation.detalles), 2)
        self.assertEqual(reservation.total, 170000.0)
        self.assertIs(reservation.detalles[1].paquete, second_package)

    def test_reservation_rejects_invalid_client_and_quantity(self) -> None:
        package = Paquete_Nacional(304, "Ruta Central", 2, 45000.0)

        with self.assertRaises(ValueError):
            Reserva("  ", package, 1)
        with self.assertRaises(ValueError):
            Reserva("10.000.013-K", package, 0)
        with self.assertRaises(ValueError):
            Reserva("10.000.013-K", package, True)

    def test_detail_rejects_non_package_argument(self) -> None:
        with self.assertRaises(TypeError):
            DetalleReserva("paquete", 1)


if __name__ == "__main__":
    unittest.main()
