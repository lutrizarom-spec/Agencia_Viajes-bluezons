"""Servicio turístico de alojamiento: hotel o estancia."""

from __future__ import annotations

from model.servicio_turistico import Servicio_Turistico
from model.validaciones import validar_entero_positivo


class Hotel_Estancia(Servicio_Turistico):
    """Alojamiento con un número de noches consultable por la reserva."""

    def __init__(
        self,
        id_servicio: int,
        nombre: str,
        destino: str,
        zona: str,
        precio_base: float,
        noches: int,
    ) -> None:
        super().__init__(id_servicio, nombre, destino, zona, precio_base)
        self.noches = noches

    @property
    def noches(self) -> int:
        return self.__noches

    @noches.setter
    def noches(self, valor: int) -> None:
        self.__noches = validar_entero_positivo(valor, "El número de noches")
