"""Paquete turístico nacional."""

from __future__ import annotations

from model.paquete_turistico import Paquete_Turistico


class Paquete_Nacional(Paquete_Turistico):
    """Calcula su precio local sin conversión de moneda."""

    def __init__(self, codigo: int, nombre: str, duracion: int, precio_base: float) -> None:
        super().__init__(codigo, nombre, duracion, precio_base)

    def calcular_precio(self, tasa_cambio: float | None = None) -> float:
        _ = tasa_cambio
        return self.precio_base
