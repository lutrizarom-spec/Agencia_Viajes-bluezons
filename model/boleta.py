"""Boleta emitida como parte de la composición de un pago."""

from __future__ import annotations

from datetime import date, datetime

from model.paquete_turistico import Paquete_Turistico


class Boleta:
    """Representa el comprobante asociado a un pago procesado."""

    def __init__(self, num_boleta: int, fecha: date, monto: float) -> None:
        self.num_boleta = num_boleta
        self.fecha = fecha
        self.monto = monto
        self.emitida = False

    @property
    def num_boleta(self) -> int:
        return self.__num_boleta

    @num_boleta.setter
    def num_boleta(self, valor: int) -> None:
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            raise ValueError("El número de boleta debe ser un entero positivo.")
        self.__num_boleta = valor

    @property
    def fecha(self) -> date:
        return self.__fecha

    @fecha.setter
    def fecha(self, valor: date) -> None:
        if isinstance(valor, datetime) or not isinstance(valor, date):
            raise ValueError("La fecha de boleta debe ser válida.")
        self.__fecha = valor

    @property
    def monto(self) -> float:
        return self.__monto

    @monto.setter
    def monto(self, valor: float) -> None:
        Paquete_Turistico._validar_monto(
            valor, "El monto de boleta", permitir_cero=False
        )
        self.__monto = valor

    @property
    def emitida(self) -> bool:
        return self.__emitida

    @emitida.setter
    def emitida(self, valor: bool) -> None:
        if not isinstance(valor, bool):
            raise ValueError("El estado de emisión debe ser booleano.")
        self.__emitida = valor

    def emitir(self) -> None:
        self.emitida = True
