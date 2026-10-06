"""Línea de detalle que conserva la cantidad y el precio del paquete."""

from __future__ import annotations

import math

from model.paquete_turistico import Paquete_Turistico


class Detalle_Reserva:
    """Representa una línea creada y administrada por su reserva."""

    def __init__(
        self,
        id_detalle: int,
        paquete: Paquete_Turistico,
        cantidad: int,
        tasa_cambio: float | None = None,
    ) -> None:
        self.id_detalle = id_detalle
        self.paquete = paquete
        self.tasa_cambio = tasa_cambio
        self.cantidad = cantidad

    @property
    def id_detalle(self) -> int:
        return self.__id_detalle

    @id_detalle.setter
    def id_detalle(self, valor: int) -> None:
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            raise ValueError("El ID del detalle debe ser un entero positivo.")
        self.__id_detalle = valor

    @property
    def paquete(self) -> Paquete_Turistico:
        return self.__paquete

    @paquete.setter
    def paquete(self, valor: Paquete_Turistico) -> None:
        if not isinstance(valor, Paquete_Turistico):
            raise TypeError("El detalle debe referir a un Paquete_Turistico.")
        self.__paquete = valor

    @property
    def tasa_cambio(self) -> float | None:
        return self.__tasa_cambio

    @tasa_cambio.setter
    def tasa_cambio(self, valor: float | None) -> None:
        if valor is not None:
            Paquete_Turistico._validar_monto(
                valor, "La tasa de cambio", permitir_cero=False
            )
        self.__tasa_cambio = valor

    @property
    def cantidad(self) -> int:
        return self.__cantidad

    @cantidad.setter
    def cantidad(self, valor: int) -> None:
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            raise ValueError("La cantidad debe ser un entero positivo.")
        precio = self.paquete.calcular_precio(self.tasa_cambio)
        subtotal = precio * valor
        if not math.isfinite(subtotal) or subtotal <= 0:
            raise ValueError("El subtotal debe ser finito y positivo.")
        self.__cantidad = valor
        self.__subtotal = subtotal

    @property
    def subtotal(self) -> float:
        return self.__subtotal

    def calcular_subtotal(self) -> float:
        return self.subtotal
