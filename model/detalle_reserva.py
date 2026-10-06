"""Detalle de una reserva asociado a un paquete turístico existente."""

import math

from model.paquete_turistico import Paquete_Turistico


class DetalleReserva:
    """Conserva el paquete asociado, la cantidad y el precio unitario reservado."""

    def __init__(self, paquete: Paquete_Turistico, cantidad: int) -> None:
        if not isinstance(paquete, Paquete_Turistico):
            raise TypeError("El paquete debe ser una instancia de Paquete_Turistico.")
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0:
            raise ValueError("La cantidad debe ser un entero positivo.")

        precio_unitario = paquete.calcular_precio()
        if not math.isfinite(precio_unitario) or precio_unitario <= 0:
            raise ValueError("El precio calculado del paquete debe ser finito y positivo.")

        total = precio_unitario * cantidad
        if not math.isfinite(total):
            raise ValueError("El subtotal del detalle debe ser finito.")

        self.__paquete = paquete
        self.__cantidad = cantidad
        self.__precio_unitario = precio_unitario
        self.__subtotal = total

    @property
    def paquete(self) -> Paquete_Turistico:
        return self.__paquete

    @property
    def cantidad(self) -> int:
        return self.__cantidad

    @property
    def precio_unitario(self) -> float:
        return self.__precio_unitario

    @property
    def subtotal(self) -> float:
        return self.__subtotal
