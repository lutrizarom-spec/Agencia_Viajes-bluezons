"""Pruebas del CRUD de clientes sobre SQLite con baja lógica."""

import tempfile
import unittest
from pathlib import Path

from conectar import crear_conexion
from dao.cliente_dao import ClienteDao
from model.cliente import Cliente


def _cliente(
    id_cliente: int = 1,
    rut: str = "10.000.013-K",
    nombre: str = "Camila Rojas",
    telefono_movil: str = "+56912345678",
    pasaporte: str = "AB123456",
    correo: str = "camila@example.com",
) -> Cliente:
    return Cliente(id_cliente, rut, nombre, telefono_movil, pasaporte, correo)


class ClienteDaoTests(unittest.TestCase):
    """Verifica crear, leer, actualizar y la baja lógica de clientes."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "clientes.db"
        self.conexion = crear_conexion(self.db_path)
        self.addCleanup(self.conexion.close)
        self.dao = ClienteDao(self.conexion)
        self.dao.crear_tabla()

    def test_insertar_y_obtener_cliente(self) -> None:
        self.dao.insertar_cliente(_cliente())
        fila = self.dao.obtener_cliente(1)
        self.assertIsNotNone(fila)
        assert fila is not None
        self.assertEqual(fila[1], "10.000.013-K")
        self.assertEqual(fila[2], "Camila Rojas")
        self.assertEqual(fila[4], "AB123456")
        self.assertEqual(fila[6], 1)

    def test_listar_clientes_ordenados_por_id(self) -> None:
        self.dao.insertar_cliente(_cliente(id_cliente=2, rut="1-9", nombre="B", correo="b@example.com"))
        self.dao.insertar_cliente(_cliente(id_cliente=1, rut="2-7", nombre="A", correo="a@example.com"))
        filas = self.dao.listar_clientes()
        self.assertEqual([fila[0] for fila in filas], [1, 2])

    def test_actualizar_cliente_conserva_el_id(self) -> None:
        self.dao.insertar_cliente(_cliente())
        actualizado = _cliente(nombre="Camila Rojas Díaz", telefono_movil="+56900000000", pasaporte="AB999999")
        filas = self.dao.actualizar_cliente(actualizado, 1)
        self.assertEqual(filas, 1)
        fila = self.dao.obtener_cliente(1)
        self.assertIsNotNone(fila)
        assert fila is not None
        self.assertEqual(fila[2], "Camila Rojas Díaz")
        self.assertEqual(fila[4], "AB999999")

    def test_eliminar_es_baja_logica_e_idempotente(self) -> None:
        self.dao.insertar_cliente(_cliente())
        self.assertEqual(self.dao.eliminar_cliente(1), 1)
        self.assertIsNone(self.dao.obtener_cliente(1))
        self.assertEqual(self.dao.listar_clientes(), [])
        inactivo = self.dao.obtener_cliente(1, incluir_inactivo=True)
        self.assertIsNotNone(inactivo)
        assert inactivo is not None
        self.assertEqual(inactivo[6], 0)
        self.assertEqual(self.dao.eliminar_cliente(1), 0)

    def test_un_cliente_inactivo_no_se_actualiza(self) -> None:
        self.dao.insertar_cliente(_cliente())
        self.dao.eliminar_cliente(1)
        filas = self.dao.actualizar_cliente(_cliente(nombre="Otro"), 1)
        self.assertEqual(filas, 0)

    def test_texto_arbitrario_no_rompe_la_consulta(self) -> None:
        self.dao.insertar_cliente(_cliente(nombre="O'Hara; DROP TABLE clientes"))
        fila = self.dao.obtener_cliente(1)
        self.assertIsNotNone(fila)
        assert fila is not None
        self.assertEqual(fila[2], "O'Hara; DROP TABLE clientes")
        self.assertEqual(len(self.dao.listar_clientes()), 1)

    def test_crear_tabla_es_idempotente(self) -> None:
        self.dao.insertar_cliente(_cliente())
        self.dao.crear_tabla()
        self.assertIsNotNone(self.dao.obtener_cliente(1))


if __name__ == "__main__":
    unittest.main()
