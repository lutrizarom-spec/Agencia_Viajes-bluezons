"""Guarda de arquitectura: la API no usa la persistencia POO de consola.

Documenta y protege la frontera descrita en el README: las capas
`services/` y `main_api.py` no deben acoplarse a `ReservaDao` ni a las tablas
`reservas_modelo`/`detalle_reserva`, que pertenecen al mundo POO/consola.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_TOKENS = ("ReservaDao", "reservas_modelo", "detalle_reserva")


class ArchitectureBoundaryTests(unittest.TestCase):
    """Comprueba que la frontera dominio/consola vs API se mantiene."""

    def _api_layer_files(self) -> list[Path]:
        service_files = sorted((ROOT / "services").glob("*.py"))
        return service_files + [ROOT / "main_api.py"]

    def test_api_layer_does_not_use_console_reservation_persistence(self) -> None:
        for path in self._api_layer_files():
            source = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_TOKENS:
                self.assertNotIn(
                    token,
                    source,
                    f"{path.relative_to(ROOT)} no debe referenciar {token!r} (frontera POO/API).",
                )


if __name__ == "__main__":
    unittest.main()
