"""Pago asociado a una reserva y compuesto por su boleta.

Estados del dominio y su equivalencia persistida por la API:
``pendiente`` → ``pending``, ``confirmado`` → ``confirmed``,
``fallido`` → ``failed``. La API no usa esta clase; su estado se guarda en
``payments`` a través de ``CompraService``.
"""

from __future__ import annotations

from datetime import date, datetime

from model.boleta import Boleta
from model.paquete_turistico import Paquete_Turistico
from model.reserva import Reserva


class Pago:
    """Valida el anticipo y crea internamente la boleta del pago."""

    def __init__(
        self,
        id_pago: str,
        fecha: date,
        monto: float,
        fecha_vencimiento: date,
        reserva: Reserva,
        num_boleta: int,
    ) -> None:
        self.id_pago = id_pago
        self.fecha = fecha
        self.monto = monto
        self.estado = "pendiente"
        self.fecha_vencimiento = fecha_vencimiento
        self.reserva = reserva
        self.__boleta = Boleta(num_boleta, fecha, monto)

    @property
    def id_pago(self) -> str:
        return self.__id_pago

    @id_pago.setter
    def id_pago(self, valor: str) -> None:
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError("El ID del pago no puede estar vacío.")
        self.__id_pago = valor.strip()

    @property
    def fecha(self) -> date:
        return self.__fecha

    @fecha.setter
    def fecha(self, valor: date) -> None:
        self.__fecha = self._validar_fecha(valor, "La fecha del pago")

    @property
    def monto(self) -> float:
        return self.__monto

    @monto.setter
    def monto(self, valor: float) -> None:
        Paquete_Turistico._validar_monto(valor, "El monto del pago", permitir_cero=False)
        self.__monto = valor

    @property
    def estado(self) -> str:
        return self.__estado

    @estado.setter
    def estado(self, valor: str) -> None:
        if not isinstance(valor, str) or valor not in {
            "pendiente",
            "confirmado",
            "fallido",
        }:
            raise ValueError("El estado del pago no es válido.")
        self.__estado = valor

    @property
    def fecha_vencimiento(self) -> date:
        return self.__fecha_vencimiento

    @fecha_vencimiento.setter
    def fecha_vencimiento(self, valor: date) -> None:
        self.__fecha_vencimiento = self._validar_fecha(
            valor, "La fecha de vencimiento"
        )

    @property
    def reserva(self) -> Reserva:
        return self.__reserva

    @reserva.setter
    def reserva(self, valor: Reserva) -> None:
        if not isinstance(valor, Reserva):
            raise TypeError("El pago debe asociarse a una Reserva.")
        self.__reserva = valor

    @property
    def boleta(self) -> Boleta:
        return self.__boleta

    @staticmethod
    def _validar_fecha(valor: date, etiqueta: str) -> date:
        if isinstance(valor, datetime) or not isinstance(valor, date):
            raise ValueError(f"{etiqueta} debe ser válida.")
        return valor

    def procesar_pago(self) -> None:
        self.reserva.validar_anticipo(self.monto)
        self.estado = "confirmado"
        self.boleta.emitir()
