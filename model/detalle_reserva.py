"""Línea de detalle que referencia un paquete o un servicio de la reserva."""

from __future__ import annotations

import math

from model.paquete_turistico import Paquete_Turistico
from model.servicio_turistico import Servicio_Turistico


class Detalle_Reserva:
    """Representa una línea creada y administrada por su reserva."""

    def __init__(
        self,
        id_detalle: int,
        referencia: Paquete_Turistico | Servicio_Turistico,
        cantidad: int,
        tasa_cambio: float | None = None,
    ) -> None:
        self.id_detalle = id_detalle
        self.referencia = referencia
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
    def referencia(self) -> Paquete_Turistico | Servicio_Turistico:
        return self.__referencia

    @referencia.setter
    def referencia(self, valor: Paquete_Turistico | Servicio_Turistico) -> None:
        if not isinstance(valor, (Paquete_Turistico, Servicio_Turistico)):
            raise TypeError(
                "El detalle debe referir a un Paquete_Turistico o un Servicio_Turistico."
            )
        self.__referencia = valor

    @property
    def paquete(self) -> Paquete_Turistico | None:
        """Conserva la lectura histórica del paquete cuando la línea es de paquete."""
        return self.__referencia if isinstance(self.__referencia, Paquete_Turistico) else None

    @property
    def servicio(self) -> Servicio_Turistico | None:
        """Expone el servicio asociado cuando la línea representa un servicio."""
        return self.__referencia if isinstance(self.__referencia, Servicio_Turistico) else None

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
        precio = self.referencia.calcular_precio(self.tasa_cambio)
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
