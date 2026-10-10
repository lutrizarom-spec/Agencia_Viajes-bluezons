"""Pruebas del menú de consola de main.py sobre una base SQLite temporal."""

import io
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import main as main_module
from conectar import crear_conexion
from dao.paquete_dao import PaqueteDao


class MenuConsolaTests(unittest.TestCase):
    """Verifica CRUD, persistencia y tolerancia a entradas inválidas del menú."""

    def setUp(self) -> None:
        """Prepara una base temporal aislada de la base real del proyecto."""
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "menu-test.db"
        self.conexion = crear_conexion(self.db_path)
        self.addCleanup(self.conexion.close)
        self.dao = PaqueteDao(self.conexion)
        self.dao.crear_tabla()

    def _ejecutar(self, entradas: list[str]) -> str:
        """Ejecuta el menú alimentando entradas y capturando la salida mostrada."""
        salida: list[str] = []
        iterador = iter(entradas)

        def leer() -> str:
            return next(iterador)

        main_module.ejecutar_menu(self.dao, leer=leer, mostrar=salida.append)
        return "\n".join(salida)

    def test_crear_y_listar_deja_el_paquete_persistido(self) -> None:
        """Opción 1 crea y opción 2 lista el registro recién guardado."""
        salida = self._ejecutar(
            ["1", "1", "501", "Tour Norte", "3", "50000", "2", "6"]
        )

        fila = self.dao.obtener_paquete(501)
        self.assertIsNotNone(fila)
        self.assertEqual(fila[1], "Tour Norte")
        self.assertEqual(fila[4], "nacional")
        self.assertIn("Tour Norte", salida)

    def test_modificar_actualiza_los_campos_sin_cambiar_el_codigo(self) -> None:
        """Opción 3 edita nombre, duración y precio conservando el código."""
        self._ejecutar(
            [
                "1", "1", "502", "Original", "3", "10000",
                "3", "502", "Modificado", "4", "20000",
                "6",
            ]
        )

        fila = self.dao.obtener_paquete(502)
        self.assertEqual(fila[0], 502)
        self.assertEqual(fila[1], "Modificado")
        self.assertEqual(fila[2], 4)
        self.assertEqual(fila[3], 20000)

    def test_eliminar_hace_baja_logica_y_oculta_del_listado(self) -> None:
        """Opción 4 desactiva el paquete sin borrarlo físicamente."""
        self._ejecutar(["1", "1", "503", "A borrar", "2", "1000", "4", "503", "6"])

        self.assertIsNone(self.dao.obtener_paquete(503))
        self.assertEqual(self.dao.obtener_paquetes(), [])
        inactivo = self.dao.obtener_paquete(503, incluir_inactivo=True)
        self.assertIsNotNone(inactivo)
        self.assertEqual(inactivo[7], 0)

    def test_persistencia_entre_conexiones_distintas(self) -> None:
        """Lo creado por el menú sobrevive al cierre y reapertura de la base."""
        self._ejecutar(["1", "1", "504", "Persistente", "5", "30000", "6"])

        with closing(crear_conexion(self.db_path)) as otra_conexion:
            dao_nuevo = PaqueteDao(otra_conexion)
            fila = dao_nuevo.obtener_paquete(504)
        self.assertIsNotNone(fila)
        self.assertEqual(fila[1], "Persistente")

    def test_entradas_invalidas_no_cierran_el_menu(self) -> None:
        """Valores no numéricos, negativos o vacíos se rechazan sin excepción."""
        salida = self._ejecutar(
            [
                "1", "9", "x", "1",
                "abc", "-5", "505",
                "   ", "Valido",
                "x", "0", "2",
                "1500",
                "2", "6",
            ]
        )

        fila = self.dao.obtener_paquete(505)
        self.assertIsNotNone(fila)
        self.assertEqual(fila[1], "Valido")
        self.assertEqual(fila[2], 2)
        self.assertEqual(fila[3], 1500)
        self.assertIn("Entrada inválida", salida)

    def test_opcion_invalida_repite_sin_romper_el_bucle(self) -> None:
        """Una opción fuera de rango informa y vuelve a mostrar el menú."""
        salida = self._ejecutar(["7", "abc", "6"])

        self.assertIn("Opción inválida", salida)
        self.assertIn("Saliendo", salida)

    def test_codigo_duplicado_informa_sin_sobrescribir(self) -> None:
        """Un alta con código repetido no cae ni reemplaza el registro original."""
        salida = self._ejecutar(
            [
                "1", "1", "506", "Primero", "1", "1000",
                "1", "1", "506", "Segundo", "1", "2000",
                "6",
            ]
        )

        self.assertIn("ya existe", salida)
        fila = self.dao.obtener_paquete(506)
        self.assertEqual(fila[1], "Primero")

    def test_entrada_cerrada_sale_de_forma_limpia(self) -> None:
        """Sin más entradas (EOF) el menú termina sin propagar excepción."""
        salida: list[str] = []

        def leer():
            raise EOFError

        main_module.ejecutar_menu(self.dao, leer=leer, mostrar=salida.append)
        self.assertIn("Saliendo", "\n".join(salida))

    def test_crear_internacional_y_crucero_guarda_campos_especificos(self) -> None:
        """Cada subtipo pide y persiste su atributo propio."""
        self._ejecutar(["1", "2", "507", "Buenos Aires", "5", "200", "s", "6"])
        self._ejecutar(["1", "3", "508", "Fiordos", "7", "400", "25000", "6"])

        internacional = self.dao.obtener_paquete(507)
        crucero = self.dao.obtener_paquete(508)
        self.assertEqual(internacional[4], "internacional")
        self.assertEqual(internacional[5], 1)
        self.assertEqual(crucero[4], "crucero")
        self.assertEqual(crucero[6], 25000)

    def test_main_completa_el_flujo_de_consola_con_base_temporal(self) -> None:
        """`main(db_path=...)` arranca el menú desde el punto de entrada real."""
        with patch(
            "builtins.input",
            side_effect=["1", "1", "509", "Via main", "3", "5000", "6"],
        ):
            with redirect_stdout(io.StringIO()):
                main_module.main(db_path=self.db_path)

        fila = self.dao.obtener_paquete(509)
        self.assertIsNotNone(fila)
        self.assertEqual(fila[1], "Via main")


if __name__ == "__main__":
    unittest.main()
