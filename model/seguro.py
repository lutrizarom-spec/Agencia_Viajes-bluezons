"""Servicio turístico de protección: seguro de viaje."""

from __future__ import annotations

from model.servicio_turistico import Servicio_Turistico
from model.validaciones import texto_requerido


class Seguro(Servicio_Turistico):
    """Seguro de viaje con su cobertura consultable por la reserva."""

    def __init__(
        self,
        id_servicio: int,
        nombre: str,
        destino: str,
        zona: str,
        precio_base: float,
        cobertura: str,
    ) -> None:
        super().__init__(id_servicio, nombre, destino, zona, precio_base)
        self.cobertura = cobertura

    @property
    def cobertura(self) -> str:
        return self.__cobertura

    @cobertura.setter
    def cobertura(self, valor: str) -> None:
        self.__cobertura = texto_requerido(valor, "La cobertura del seguro")
