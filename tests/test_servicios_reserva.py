"""Pruebas de los servicios turísticos y de la reserva con varios servicios."""

import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from conectar import crear_conexion
from dao.reserva_dao import ReservaDao
from model.cliente import Cliente
from model.excursion import Excursion
from model.hotel_estancia import Hotel_Estancia
from model.linea_vuelo import Linea_Vuelo
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.pasaporte_requerido_error import PasaporteRequeridoError
from model.reserva import Reserva
from model.seguro import Seguro
from model.servicio_turistico import Servicio_Turistico


def _cliente_con_pasaporte() -> Cliente:
    return Cliente(
        1, "10.000.013-K", "Camila Rojas", "+56912345678", "AB123456", "camila@example.com"
    )


def _servicios() -> list[Servicio_Turistico]:
    return [
        Linea_Vuelo(1, "Vuelo SCL-LIM", "Lima", "Sudamérica", 120000.0, "LATAM"),
        Hotel_Estancia(2, "Hotel Miraflores", "Lima", "Sudamérica", 80000.0, 3),
        Excursion(3, "City tour", "Lima", "Sudamérica", 30000.0, 4),
        Seguro(4, "Seguro viaje", "Lima", "Sudamérica", 15000.0, "cobertura total"),
    ]


def _reserva_con_servicios() -> Reserva:
    paquete = Paquete_Nacional(701, "Ruta del Norte", 3, 50000.0)
    reserva = Reserva(
        "RES-701",
        date(2026, 10, 6),
        date(2026, 12, 15),
        _cliente_con_pasaporte(),
        paquete,
    )
    reserva.agregar_detalle(2)
    for servicio in _servicios():
        reserva.agregar_servicio(servicio)
    return reserva


class ServicioModeloTests(unittest.TestCase):
    """Verifica la composición de servicios dentro de una misma reserva."""

    def test_servicios_exponen_precio_y_campos_especificos(self) -> None:
        vuelo, hotel, excursion, seguro = _servicios()
        self.assertEqual(vuelo.obtener_precio(), 120000.0)
        self.assertEqual(hotel.noches, 3)
        self.assertEqual(vuelo.aerolinea, "LATAM")
        self.assertEqual(excursion.duracion_horas, 4)
        self.assertEqual(seguro.cobertura, "cobertura total")
        self.assertEqual(hotel.calcular_precio(), 80000.0)

    def test_servicio_rechaza_datos_invalidos(self) -> None:
        with self.assertRaises(ValueError):
            Hotel_Estancia(1, "Hotel", "Destino", "Zona", 1000.0, 0)
        with self.assertRaises(ValueError):
            Linea_Vuelo(2, "Vuelo", "  ", "Zona", 1000.0, "LATAM")
        with self.assertRaises(ValueError):
            Seguro(3, "Seguro", "Destino", "Zona", -5.0, "total")

    def test_detalle_de_servicio_calcula_subtotal(self) -> None:
        reserva = _reserva_con_servicios()
        detalle_vuelo = reserva.detalles[1]
        self.assertIsInstance(detalle_vuelo.servicio, Linea_Vuelo)
        self.assertIsNone(detalle_vuelo.paquete)
        self.assertEqual(detalle_vuelo.subtotal, 120000.0)

    def test_reserva_guarda_y_consulta_los_cuatro_servicios(self) -> None:
        reserva = _reserva_con_servicios()
        self.assertEqual(len(reserva.servicios), 4)
        tipos = {type(servicio) for servicio in reserva.servicios}
        self.assertEqual(
            tipos, {Linea_Vuelo, Hotel_Estancia, Excursion, Seguro}
        )
        self.assertEqual(len(reserva.detalles), 5)
        self.assertEqual(reserva.total, 345000.0)

    def test_pasaporte_se_exige_tambien_al_agregar_servicios(self) -> None:
        cliente = Cliente(
            2, "12.345.678-5", "Diego Soto", "+56987654321", "", "diego@example.com"
        )
        internacional = Paquete_Internacional(702, "Buenos Aires", 5, 250.0, True)
        reserva = Reserva(
            "RES-702", date(2026, 10, 6), date(2026, 12, 15), cliente, internacional
        )
        with self.assertRaises(PasaporteRequeridoError):
            reserva.agregar_servicio(Linea_Vuelo(9, "Vuelo", "BA", "Sur", 100.0, "AA"))

    def test_no_se_pueden_agregar_servicios_a_reserva_no_pendiente(self) -> None:
        reserva = _reserva_con_servicios()
        reserva.confirmar()
        with self.assertRaises(ValueError):
            reserva.agregar_servicio(
                Excursion(10, "Otra", "Lima", "Sur", 100.0, 2)
            )


class ReservaPersistenciaTests(unittest.TestCase):
    """Comprueba que los servicios sobreviven al cierre y reapertura de SQLite."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "reserva-test.db"
        self.conexion = crear_conexion(self.db_path)
        self.addCleanup(self.conexion.close)
        self.dao = ReservaDao(self.conexion)
        self.dao.crear_tabla()

    def test_los_cuatro_servicios_se_recuperan_de_una_sola_reserva(self) -> None:
        original = _reserva_con_servicios()
        self.dao.guardar_reserva(original)
        total_original = original.total

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            dao_nuevo = ReservaDao(otra_conexion)
            recuperada = dao_nuevo.obtener_reserva("RES-701")
            servicios = dao_nuevo.obtener_servicios_de_reserva("RES-701")

        self.assertIsNotNone(recuperada)
        self.assertEqual(len(recuperada.servicios), 4)
        self.assertEqual(len(servicios), 4)
        self.assertEqual(recuperada.total, total_original)
        self.assertEqual(
            {type(servicio) for servicio in recuperada.servicios},
            {Linea_Vuelo, Hotel_Estancia, Excursion, Seguro},
        )
        recuperados = {type(s): s for s in recuperada.servicios}
        self.assertEqual(recuperados[Linea_Vuelo].aerolinea, "LATAM")
        self.assertEqual(recuperados[Hotel_Estancia].noches, 3)
        self.assertEqual(recuperados[Excursion].duracion_horas, 4)
        self.assertEqual(recuperados[Seguro].cobertura, "cobertura total")
        self.assertEqual(recuperada.numero_reserva, "RES-701")
        self.assertEqual(recuperada.detalles[0].paquete.codigo, 701)

    def test_guardar_dos_veces_no_duplica_los_detalles(self) -> None:
        original = _reserva_con_servicios()
        self.dao.guardar_reserva(original)
        self.dao.guardar_reserva(original)

        filas = self.dao.cursor.execute(
            "SELECT COUNT(*) FROM detalle_reserva WHERE numero_reserva = ?",
            ("RES-701",),
        ).fetchone()[0]
        self.assertEqual(filas, 5)
        self.assertEqual(len(self.dao.obtener_servicios_de_reserva("RES-701")), 4)

    def test_reserva_inexistente_retorna_none(self) -> None:
        self.assertIsNone(self.dao.obtener_reserva("NO-EXISTE"))

    def test_servicio_individual_se_persiste_y_recupera(self) -> None:
        servicio = Hotel_Estancia(20, "Hotel Centro", "Santiago", "Centro", 45000.0, 2)
        self.dao.guardar_servicio(servicio)

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            recuperado = ReservaDao(otra_conexion).obtener_servicio(20)
        self.assertIsInstance(recuperado, Hotel_Estancia)
        self.assertEqual(recuperado.noches, 2)


if __name__ == "__main__":
    unittest.main()
