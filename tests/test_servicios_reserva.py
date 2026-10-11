"""Pruebas de los servicios turísticos y de la reserva con varios servicios."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from conectar import crear_conexion
from dao.reserva_dao import ReservaDao
from model.cliente import Cliente
from model.detalle_reserva_inconsistente_error import DetalleReservaInconsistenteError
from model.excursion import Excursion
from model.hotel_estancia import Hotel_Estancia
from model.linea_vuelo import Linea_Vuelo
from model.paquete_crucero import Paquete_Crucero
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

    def _cursor_que_falla_en_insert(self, numero_insert: int) -> sqlite3.Cursor:
        original = self.dao.cursor

        class _CursorQueFalla:
            def __init__(self, cursor: sqlite3.Cursor) -> None:
                self._cursor = cursor
                self._inserts = 0

            def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
                if sql.lstrip().startswith("INSERT INTO detalle_reserva"):
                    self._inserts += 1
                    if self._inserts == numero_insert:
                        raise sqlite3.OperationalError("database is locked (simulado)")
                return self._cursor.execute(sql, *args)  # type: ignore[arg-type]

            def __getattr__(self, nombre: str) -> object:
                return getattr(self._cursor, nombre)

        self.dao.cursor = _CursorQueFalla(original)
        self.addCleanup(setattr, self.dao, "cursor", original)
        return original

    def test_fallo_al_guardar_deja_la_base_intacta(self) -> None:
        original = self._cursor_que_falla_en_insert(3)

        with self.assertRaises(sqlite3.OperationalError):
            self.dao.guardar_reserva(_reserva_con_servicios())

        self.dao.cursor = original
        self.dao.guardar_servicio(
            Hotel_Estancia(9, "Otro Hotel", "Lima", "Centro", 10000.0, 1)
        )

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            dao_nuevo = ReservaDao(otra_conexion)
            cabeceras = dao_nuevo.cursor.execute(
                "SELECT COUNT(*) FROM reservas_modelo WHERE numero_reserva = ?",
                ("RES-701",),
            ).fetchone()[0]
            detalles = dao_nuevo.cursor.execute(
                "SELECT COUNT(*) FROM detalle_reserva WHERE numero_reserva = ?",
                ("RES-701",),
            ).fetchone()[0]
            servicios = dao_nuevo.cursor.execute(
                "SELECT COUNT(*) FROM servicios"
            ).fetchone()[0]
            self.assertEqual(cabeceras, 0)
            self.assertEqual(detalles, 0)
            self.assertEqual(servicios, 1)
            self.assertIsNone(dao_nuevo.obtener_reserva("RES-701"))

    def test_reintento_tras_fallo_consolida_la_reserva_completa(self) -> None:
        original = self._cursor_que_falla_en_insert(3)

        with self.assertRaises(sqlite3.OperationalError):
            self.dao.guardar_reserva(_reserva_con_servicios())

        self.dao.cursor = original
        self.dao.guardar_reserva(_reserva_con_servicios())

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            recuperada = ReservaDao(otra_conexion).obtener_reserva("RES-701")

        self.assertIsNotNone(recuperada)
        self.assertEqual(recuperada.numero_reserva, "RES-701")
        self.assertEqual(len(recuperada.detalles), 5)
        self.assertEqual(len(recuperada.servicios), 4)

    def test_estado_confirmado_se_conserva_tras_recuperar(self) -> None:
        reserva = _reserva_con_servicios()
        reserva.confirmar()
        self.dao.guardar_reserva(reserva)

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            recuperada = ReservaDao(otra_conexion).obtener_reserva("RES-701")

        self.assertIsNotNone(recuperada)
        self.assertEqual(recuperada.estado, "confirmada")
        self.assertEqual(len(recuperada.detalles), 5)
        self.assertEqual(recuperada.total, reserva.total)

    def test_estado_cancelado_se_conserva_tras_recuperar(self) -> None:
        reserva = _reserva_con_servicios()
        reserva.confirmar()
        reserva.cancelar()
        self.dao.guardar_reserva(reserva)

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            recuperada = ReservaDao(otra_conexion).obtener_reserva("RES-701")

        self.assertIsNotNone(recuperada)
        self.assertEqual(recuperada.estado, "cancelada")
        self.assertEqual(len(recuperada.detalles), 5)

    def test_reserva_sin_detalles_se_conserva(self) -> None:
        paquete = Paquete_Nacional(702, "Solo cabecera", 2, 10000.0)
        reserva = Reserva(
            "RES-702",
            date(2026, 10, 6),
            date(2026, 12, 15),
            _cliente_con_pasaporte(),
            paquete,
        )
        self.dao.guardar_reserva(reserva)

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            recuperada = ReservaDao(otra_conexion).obtener_reserva("RES-702")

        self.assertIsNotNone(recuperada)
        self.assertEqual(recuperada.numero_reserva, "RES-702")
        self.assertEqual(recuperada.estado, "pendiente")
        self.assertEqual(len(recuperada.detalles), 0)

    def test_reserva_inexistente_retorna_none(self) -> None:
        self.assertIsNone(self.dao.obtener_reserva("NO-EXISTE"))

    def test_servicio_individual_se_persiste_y_recupera(self) -> None:
        servicio = Hotel_Estancia(20, "Hotel Centro", "Santiago", "Centro", 45000.0, 2)
        self.dao.guardar_servicio(servicio)

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            recuperado = ReservaDao(otra_conexion).obtener_servicio(20)
        self.assertIsInstance(recuperado, Hotel_Estancia)
        self.assertEqual(recuperado.noches, 2)

    def test_total_no_cambia_al_modificar_el_precio_del_servicio(self) -> None:
        original = _reserva_con_servicios()
        self.dao.guardar_reserva(original)
        total_original = original.total

        self.dao.guardar_servicio(
            Hotel_Estancia(2, "Hotel Miraflores", "Lima", "Sudamérica", 190000.0, 3)
        )

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            recuperada = ReservaDao(otra_conexion).obtener_reserva("RES-701")

        self.assertIsNotNone(recuperada)
        self.assertEqual(recuperada.total, total_original)

    def test_servicio_referenciado_inexistente_lanza_error(self) -> None:
        self.dao.guardar_reserva(_reserva_con_servicios())

        with closing(sqlite3.connect(self.db_path)) as cruda:
            cruda.execute(
                """
                INSERT INTO detalle_reserva (
                    id_detalle, numero_reserva, orden, tipo_item,
                    cantidad, subtotal, paquete_codigo, id_servicio
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (99, "RES-701", 99, "servicio", 1, 100.0, None, 999),
            )
            cruda.commit()

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            with self.assertRaises(DetalleReservaInconsistenteError) as contexto:
                ReservaDao(otra_conexion).obtener_reserva("RES-701")

        mensaje = str(contexto.exception)
        self.assertIn("RES-701", mensaje)
        self.assertIn("99", mensaje)
        self.assertIn("999", mensaje)

    def test_integridad_de_conteo_detecta_detalle_omitido(self) -> None:
        with self.assertRaises(DetalleReservaInconsistenteError):
            ReservaDao._validar_detalles_completos("RES-701", 2, [])
        ReservaDao._validar_detalles_completos("RES-701", 0, [])

    def _insertar_detalle_crudo(
        self,
        *,
        id_detalle: int,
        tipo_item: str,
        paquete_codigo: int | None,
        id_servicio: int | None,
    ) -> None:
        self.dao.guardar_reserva(_reserva_con_servicios())
        with closing(sqlite3.connect(self.db_path)) as cruda:
            cruda.execute(
                """
                INSERT INTO detalle_reserva (
                    id_detalle, numero_reserva, orden, tipo_item,
                    cantidad, subtotal, paquete_codigo, id_servicio
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (id_detalle, "RES-701", 99, tipo_item, 1, 100.0, paquete_codigo, id_servicio),
            )
            cruda.commit()

    def _obtener_con_detalle_crudo(self, **kwargs: object) -> None:
        self._insertar_detalle_crudo(**kwargs)
        with closing(crear_conexion(self.db_path)) as otra_conexion:
            with self.assertRaises(DetalleReservaInconsistenteError) as contexto:
                ReservaDao(otra_conexion).obtener_reserva("RES-701")
        mensaje = str(contexto.exception)
        self.assertIn("RES-701", mensaje)
        self.assertIn(str(kwargs["id_detalle"]), mensaje)

    def test_tipo_item_desconocido_es_rechazado(self) -> None:
        self._obtener_con_detalle_crudo(
            id_detalle=90, tipo_item="basura", paquete_codigo=None, id_servicio=None
        )

    def test_servicio_sin_id_servicio_es_rechazado(self) -> None:
        self._obtener_con_detalle_crudo(
            id_detalle=91, tipo_item="servicio", paquete_codigo=None, id_servicio=None
        )

    def test_servicio_con_paquete_simultaneo_es_rechazado(self) -> None:
        self._obtener_con_detalle_crudo(
            id_detalle=92, tipo_item="servicio", paquete_codigo=701, id_servicio=1
        )

    def test_paquete_sin_codigo_es_rechazado(self) -> None:
        self._obtener_con_detalle_crudo(
            id_detalle=93, tipo_item="paquete", paquete_codigo=None, id_servicio=None
        )

    def test_paquete_con_servicio_simultaneo_es_rechazado(self) -> None:
        self._obtener_con_detalle_crudo(
            id_detalle=94, tipo_item="paquete", paquete_codigo=701, id_servicio=1
        )

    def test_paquete_con_codigo_distinto_a_la_cabecera_es_rechazado(self) -> None:
        self._obtener_con_detalle_crudo(
            id_detalle=95, tipo_item="paquete", paquete_codigo=999, id_servicio=None
        )

    def test_servicios_de_reserva_rechaza_servicio_sin_id(self) -> None:
        self._insertar_detalle_crudo(
            id_detalle=96, tipo_item="servicio", paquete_codigo=None, id_servicio=None
        )
        with closing(crear_conexion(self.db_path)) as otra_conexion:
            with self.assertRaises(DetalleReservaInconsistenteError) as contexto:
                ReservaDao(otra_conexion).obtener_servicios_de_reserva("RES-701")
        mensaje = str(contexto.exception)
        self.assertIn("RES-701", mensaje)
        self.assertIn("96", mensaje)

    def _leer_tras_corromper(
        self,
        sql: str,
        *,
        metodo: str = "obtener_reserva",
        guardar: Reserva | None = None,
    ) -> str:
        self.dao.guardar_reserva(
            _reserva_con_servicios() if guardar is None else guardar
        )
        with closing(sqlite3.connect(self.db_path)) as cruda:
            cruda.execute(sql)
            cruda.commit()
        with closing(crear_conexion(self.db_path)) as otra_conexion:
            with self.assertRaises(DetalleReservaInconsistenteError) as contexto:
                getattr(ReservaDao(otra_conexion), metodo)("RES-701")
        return str(contexto.exception)

    def _reserva_crucero(self) -> Reserva:
        paquete = Paquete_Crucero(701, "Crucero", 3, 50000.0, 1000.0)
        reserva = Reserva(
            "RES-701",
            date(2026, 10, 6),
            date(2026, 12, 15),
            _cliente_con_pasaporte(),
            paquete,
        )
        reserva.agregar_detalle(2)
        return reserva

    def test_cabecera_fecha_invalida(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE reservas_modelo SET fecha_reserva='not-a-date'"
            ),
        )

    def test_cabecera_cliente_id_no_numerico(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE reservas_modelo SET cliente_id='abc'"
            ),
        )

    def test_cabecera_correo_invalido(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE reservas_modelo SET cliente_correo='sinarroba'"
            ),
        )

    def test_cabecera_duracion_no_numerica(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE reservas_modelo SET paquete_duracion='abc'"
            ),
        )

    def test_cabecera_impuesto_crucero_nulo(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE reservas_modelo SET paquete_impuesto=NULL",
                guardar=self._reserva_crucero(),
            ),
        )

    def test_detalle_id_invalido(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE detalle_reserva SET id_detalle=0 WHERE id_detalle=1"
            ),
        )

    def test_detalle_cantidad_invalida(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE detalle_reserva SET cantidad=0 WHERE id_detalle=1"
            ),
        )

    def test_detalle_subtotal_cero(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE detalle_reserva SET subtotal=0 WHERE id_detalle=1"
            ),
        )

    def test_detalle_subtotal_negativo(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE detalle_reserva SET subtotal=-5 WHERE id_detalle=1"
            ),
        )

    def test_detalle_paquete_codigo_no_numerico(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE detalle_reserva SET paquete_codigo='abc' WHERE id_detalle=1"
            ),
        )

    def test_detalle_servicio_id_no_numerico(self) -> None:
        self.assertIn(
            "RES-701",
            self._leer_tras_corromper(
                "UPDATE detalle_reserva SET id_servicio='abc' WHERE tipo_item='servicio'"
            ),
        )

    def test_servicio_noches_nula_por_reserva(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET noches=NULL WHERE tipo='hotel'"
        )
        self.assertIn("2", mensaje)
        self.assertIn("noches", mensaje)

    def test_servicio_duracion_nula_por_reserva(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET duracion_horas=NULL WHERE tipo='excursion'"
        )
        self.assertIn("3", mensaje)
        self.assertIn("duracion_horas", mensaje)

    def test_servicio_precio_no_numerico_por_reserva(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET precio_base='x' WHERE tipo='vuelo'"
        )
        self.assertIn("1", mensaje)

    def test_servicio_noches_cero_por_reserva(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET noches=0 WHERE tipo='hotel'"
        )
        self.assertIn("2", mensaje)
        self.assertIn("noches", mensaje)

    def test_servicio_duracion_cero_por_reserva(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET duracion_horas=0 WHERE tipo='excursion'"
        )
        self.assertIn("3", mensaje)

    def test_servicio_noches_nula_por_servicios(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET noches=NULL WHERE tipo='hotel'",
            metodo="obtener_servicios_de_reserva",
        )
        self.assertIn("2", mensaje)
        self.assertIn("noches", mensaje)

    def test_servicio_duracion_nula_por_servicios(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET duracion_horas=NULL WHERE tipo='excursion'",
            metodo="obtener_servicios_de_reserva",
        )
        self.assertIn("3", mensaje)
        self.assertIn("duracion_horas", mensaje)

    def test_servicio_precio_no_numerico_por_servicios(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET precio_base='x' WHERE tipo='vuelo'",
            metodo="obtener_servicios_de_reserva",
        )
        self.assertIn("1", mensaje)

    def test_servicio_noches_cero_por_servicios(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET noches=0 WHERE tipo='hotel'",
            metodo="obtener_servicios_de_reserva",
        )
        self.assertIn("2", mensaje)
        self.assertIn("noches", mensaje)

    def test_servicio_duracion_cero_por_servicios(self) -> None:
        mensaje = self._leer_tras_corromper(
            "UPDATE servicios SET duracion_horas=0 WHERE tipo='excursion'",
            metodo="obtener_servicios_de_reserva",
        )
        self.assertIn("3", mensaje)


if __name__ == "__main__":
    unittest.main()
