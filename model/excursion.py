"""Servicio turístico de actividad guiada: excursión."""

from __future__ import annotations

from model.servicio_turistico import Servicio_Turistico
from model.validaciones import validar_entero_positivo


class Excursion(Servicio_Turistico):
    """Excursión con su duración en horas consultable por la reserva."""

    def __init__(
        self,
        id_servicio: int,
        nombre: str,
        destino: str,
        zona: str,
        precio_base: float,
        duracion_horas: int,
    ) -> None:
        super().__init__(id_servicio, nombre, destino, zona, precio_base)
        self.duracion_horas = duracion_horas

    @property
    def duracion_horas(self) -> int:
        return self.__duracion_horas

    @duracion_horas.setter
    def duracion_horas(self, valor: int) -> None:
        self.__duracion_horas = validar_entero_positivo(
            valor, "La duración de la excursión"
        )
