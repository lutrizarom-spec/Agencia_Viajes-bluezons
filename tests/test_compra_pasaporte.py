"""Pruebas de la regla de pasaporte en el flujo real de compra (P07-P08)."""

import sqlite3
import tempfile
import unittest
import warnings
from contextlib import closing
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from dao.paquete_dao import PaqueteDao
from main_api import create_app
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from services.auth_service import UserRole
from services.compra_service import CompraService, PassportRequiredError

TRAVEL_DATE = date(2026, 12, 15)
TRAVEL_DATE_JSON = TRAVEL_DATE.isoformat()


class PasaporteApiTests(unittest.TestCase):
    """Verifica que la API exija pasaporte solo en paquetes internacionales."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "pasaporte.db"
        self.app = create_app(
            self.db_path,
            jwt_secret="pasaporte-test-secret-at-least-32-bytes",
            fx_provider=lambda: 900.0,
        )
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.app.state.auth_service.create_user(
            "10.000.013-K", "cliente-pasaporte-2026", UserRole.CLIENTE
        )
        self._insert_packages()
        service = self.app.state.compra_service
        service.configure_capacity(801, TRAVEL_DATE, 5)
        service.configure_capacity(802, TRAVEL_DATE, 5)

    def _insert_packages(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                dao = PaqueteDao(connection)
                dao.crear_tabla()
                dao.insertar_paquete(
                    Paquete_Internacional(801, "Ruta internacional", 5, 100.0, True)
                )
                dao.insertar_paquete(
                    Paquete_Nacional(802, "Ruta nacional", 3, 50000.0)
                )

    def _login(self) -> str:
        response = self.client.post(
            "/auth/login",
            json={"rut": "10.000.013-K", "password": "cliente-pasaporte-2026"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["access_token"]

    def test_internacional_sin_pasaporte_es_rechazado(self) -> None:
        headers = {"Authorization": f"Bearer {self._login()}"}
        response = self.client.post(
            "/reservas",
            json={"package_code": 801, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
            headers=headers,
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            self.client.get(f"/paquetes?travel_date={TRAVEL_DATE_JSON}").json()[0][
                "cupos_disponibles"
            ],
            5,
        )
        self.assertEqual(self.client.get("/reservas", headers=headers).json(), [])

    def test_internacional_pasaporte_vacio_o_espacios_es_rechazado(self) -> None:
        headers = {"Authorization": f"Bearer {self._login()}"}
        for pasaporte in ("", "   "):
            with self.subTest(passport=pasaporte):
                response = self.client.post(
                    "/reservas",
                    json={
                        "package_code": 801,
                        "quantity": 1,
                        "travel_date": TRAVEL_DATE_JSON,
                        "passport": pasaporte,
                    },
                    headers=headers,
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_compra_sin_pasaporte_no_emite_aviso_de_constante_deprecada(self) -> None:
        headers = {"Authorization": f"Bearer {self._login()}"}
        with warnings.catch_warnings(record=True) as capturados:
            warnings.simplefilter("always")
            response = self.client.post(
                "/reservas",
                json={"package_code": 801, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
                headers=headers,
            )
        self.assertEqual(response.status_code, 422, response.text)
        avisos = [
            str(aviso.message)
            for aviso in capturados
            if "HTTP_422_UNPROCESSABLE_ENTITY" in str(aviso.message)
        ]
        self.assertEqual(avisos, [])

    def test_internacional_con_pasaporte_valido_confirma(self) -> None:
        headers = {"Authorization": f"Bearer {self._login()}"}
        response = self.client.post(
            "/reservas",
            json={
                "package_code": 801,
                "quantity": 1,
                "travel_date": TRAVEL_DATE_JSON,
                "passport": "AB123456",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["unit_price"], 90000.0)

    def test_nacional_no_exige_pasaporte(self) -> None:
        headers = {"Authorization": f"Bearer {self._login()}"}
        response = self.client.post(
            "/reservas",
            json={"package_code": 802, "quantity": 1, "travel_date": TRAVEL_DATE_JSON},
            headers=headers,
        )
        self.assertEqual(response.status_code, 201, response.text)


class PasaporteServicioTests(unittest.TestCase):
    """Verifica la regla directamente en CompraService sobre una base temporal."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "servicio.db"
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                dao = PaqueteDao(connection)
                dao.crear_tabla()
                dao.insertar_paquete(
                    Paquete_Internacional(901, "Internacional", 5, 100.0, True)
                )
                dao.insertar_paquete(Paquete_Nacional(902, "Nacional", 3, 200.0))
        self.service = CompraService(self.db_path, exchange_rate_provider=lambda: 900.0)
        self.service.configure_capacity(901, TRAVEL_DATE, 5)
        self.service.configure_capacity(902, TRAVEL_DATE, 5)

    def test_internacional_sin_pasaporte_lanza_error_de_dominio(self) -> None:
        with self.assertRaises(PassportRequiredError):
            self.service.purchase(
                "10.000.013-K", 901, 1, travel_date=TRAVEL_DATE
            )
        self.assertEqual(self.service.get_available_capacity(901, TRAVEL_DATE), 5)

    def test_internacional_con_pasaporte_confirma(self) -> None:
        receipt = self.service.purchase(
            "10.000.013-K", 901, 1, travel_date=TRAVEL_DATE, passport="AB123456"
        )
        self.assertEqual(receipt.status, "confirmed")
        self.assertEqual(self.service.get_available_capacity(901, TRAVEL_DATE), 4)

    def test_nacional_no_exige_pasaporte(self) -> None:
        receipt = self.service.purchase(
            "10.000.013-K", 902, 1, travel_date=TRAVEL_DATE
        )
        self.assertEqual(receipt.status, "confirmed")


if __name__ == "__main__":
    unittest.main()
