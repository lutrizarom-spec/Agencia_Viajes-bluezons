"""Servicio turístico de transporte aéreo: línea de vuelo."""

from __future__ import annotations

from model.servicio_turistico import Servicio_Turistico
from model.validaciones import texto_requerido


class Linea_Vuelo(Servicio_Turistico):
    """Vuelo con su aerolínea consultable por la reserva."""

    def __init__(
        self,
        id_servicio: int,
        nombre: str,
        destino: str,
        zona: str,
        precio_base: float,
        aerolinea: str,
    ) -> None:
        super().__init__(id_servicio, nombre, destino, zona, precio_base)
        self.aerolinea = aerolinea

    @property
    def aerolinea(self) -> str:
        return self.__aerolinea

    @aerolinea.setter
    def aerolinea(self, valor: str) -> None:
        self.__aerolinea = texto_requerido(valor, "La aerolínea")
