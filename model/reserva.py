"""Modelo de reserva con detalles propios y referencias a paquetes existentes."""

import math

from model.detalle_reserva import DetalleReserva
from model.paquete_turistico import Paquete_Turistico


class Reserva:
    """Agrupa detalles creados internamente y conserva el identificador del cliente."""

    def __init__(
        self,
        rut_cliente: str,
        paquete: Paquete_Turistico,
        cantidad: int,
    ) -> None:
        if not isinstance(rut_cliente, str) or not rut_cliente.strip():
            raise ValueError("El RUT del cliente no puede estar vacío.")

        self.__rut_cliente = rut_cliente.strip()
        self.__paquete = paquete
        self.__detalles = [DetalleReserva(paquete, cantidad)]

    @property
    def rut_cliente(self) -> str:
        return self.__rut_cliente

    @property
    def paquete(self) -> Paquete_Turistico:
        return self.__paquete

    @property
    def detalles(self) -> tuple[DetalleReserva, ...]:
        return tuple(self.__detalles)

    @property
    def total(self) -> float:
        total = sum(detalle.subtotal for detalle in self.__detalles)
        if not math.isfinite(total):
            raise ValueError("El total de la reserva debe ser finito.")
        return total

    def agregar_detalle(self, paquete: Paquete_Turistico, cantidad: int) -> None:
        """Crea y agrega un detalle nuevo a la reserva."""
        detalle = DetalleReserva(paquete, cantidad)
        nuevo_total = self.total + detalle.subtotal
        if not math.isfinite(nuevo_total):
            raise ValueError("El total de la reserva debe ser finito.")
        self.__detalles.append(detalle)
