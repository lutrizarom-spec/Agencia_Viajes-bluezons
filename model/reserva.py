"""Reserva con composición de detalles y agregación de cliente y paquete."""

from __future__ import annotations

import math
from datetime import date, datetime

from model.anticipo_insuficiente_error import AnticipoInsuficienteError
from model.cliente import Cliente
from model.detalle_reserva import Detalle_Reserva
from model.paquete_turistico import Paquete_Turistico
from model.pasaporte_requerido_error import PasaporteRequeridoError


class Reserva:
    """Agrupa líneas propias y conserva referencias a cliente y paquete."""

    def __init__(
        self,
        numero_reserva: str,
        fecha_reserva: date,
        fecha_viaje: date,
        cliente: Cliente,
        paquete: Paquete_Turistico,
        tasa_cambio_aplicada: float = 900,
    ) -> None:
        self.numero_reserva = numero_reserva
        self.fecha_reserva = fecha_reserva
        self.fecha_viaje = fecha_viaje
        self.cliente = cliente
        self.paquete = paquete
        self.tasa_cambio_aplicada = tasa_cambio_aplicada
        self.estado = "pendiente"
        self.__detalles: list[Detalle_Reserva] = []

    @property
    def numero_reserva(self) -> str:
        return self.__numero_reserva

    @numero_reserva.setter
    def numero_reserva(self, valor: str) -> None:
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError("El número de reserva no puede estar vacío.")
        self.__numero_reserva = valor.strip()

    @property
    def fecha_reserva(self) -> date:
        return self.__fecha_reserva

    @fecha_reserva.setter
    def fecha_reserva(self, valor: date) -> None:
        self.__fecha_reserva = self._validar_fecha(valor, "La fecha de reserva")

    @property
    def fecha_viaje(self) -> date:
        return self.__fecha_viaje

    @fecha_viaje.setter
    def fecha_viaje(self, valor: date) -> None:
        self.__fecha_viaje = self._validar_fecha(valor, "La fecha de viaje")

    @property
    def cliente(self) -> Cliente:
        return self.__cliente

    @cliente.setter
    def cliente(self, valor: Cliente) -> None:
        if not isinstance(valor, Cliente):
            raise TypeError("La reserva debe recibir un objeto Cliente.")
        self.__cliente = valor

    @property
    def paquete(self) -> Paquete_Turistico:
        return self.__paquete

    @paquete.setter
    def paquete(self, valor: Paquete_Turistico) -> None:
        if not isinstance(valor, Paquete_Turistico):
            raise TypeError("La reserva debe recibir un Paquete_Turistico.")
        self.__paquete = valor

    @property
    def tasa_cambio_aplicada(self) -> float:
        return self.__tasa_cambio_aplicada

    @tasa_cambio_aplicada.setter
    def tasa_cambio_aplicada(self, valor: float) -> None:
        Paquete_Turistico._validar_monto(
            valor, "La tasa de cambio", permitir_cero=False
        )
        self.__tasa_cambio_aplicada = valor

    @property
    def estado(self) -> str:
        return self.__estado

    @estado.setter
    def estado(self, valor: str) -> None:
        estados_permitidos = {"pendiente", "confirmada", "cancelada"}
        if not isinstance(valor, str) or valor not in estados_permitidos:
            raise ValueError("El estado de reserva no es válido.")
        self.__estado = valor

    @property
    def detalles(self) -> tuple[Detalle_Reserva, ...]:
        return tuple(self.__detalles)

    @property
    def total(self) -> float:
        total = sum(detalle.subtotal for detalle in self.__detalles)
        if not math.isfinite(total):
            raise ValueError("El total de la reserva debe ser finito.")
        return total

    @staticmethod
    def _validar_fecha(valor: date, etiqueta: str) -> date:
        if isinstance(valor, datetime) or not isinstance(valor, date):
            raise ValueError(f"{etiqueta} debe ser una fecha válida.")
        return valor

    def agregar_detalle(self, cantidad: int) -> Detalle_Reserva:
        """Valida el pasaporte y compone internamente una línea de detalle."""
        if self.paquete.requiere_pasaporte and not self.cliente.validar_pasaporte():
            raise PasaporteRequeridoError(
                "Se requiere un pasaporte registrado para reservar un paquete internacional."
            )
        detalle = Detalle_Reserva(
            len(self.__detalles) + 1,
            self.paquete,
            cantidad,
            self.tasa_cambio_aplicada,
        )
        self.__detalles.append(detalle)
        return detalle

    def calcular_total(self) -> float:
        return self.total

    def validar_anticipo(self, monto_anticipo: float) -> bool:
        """Exige y valida el anticipo mínimo del 50 % de la reserva."""
        if not self.__detalles:
            raise ValueError("No se puede validar un anticipo sin detalles.")
        Paquete_Turistico._validar_monto(
            monto_anticipo, "El anticipo", permitir_cero=True
        )
        minimo = self.total * 0.5
        if monto_anticipo < minimo:
            raise AnticipoInsuficienteError(
                f"El anticipo debe ser al menos el 50 % del total (${minimo:.2f})."
            )
        return True

    def confirmar(self) -> bool:
        if not self.__detalles:
            raise ValueError("No se puede confirmar una reserva sin detalles.")
        self.estado = "confirmada"
        return True

    def cancelar(self) -> bool:
        self.estado = "cancelada"
        return True
