"""Paquete turístico internacional."""

from __future__ import annotations

from model.paquete_turistico import Paquete_Turistico


class Paquete_Internacional(Paquete_Turistico):
    """Convierte el precio base y requiere que el cliente tenga pasaporte."""

    def __init__(
        self,
        codigo: int,
        nombre: str,
        duracion: int,
        precio_base: float,
        pasaporte_valido: bool,
    ) -> None:
        super().__init__(codigo, nombre, duracion, precio_base)
        self.pasaporte_valido = pasaporte_valido

    @property
    def pasaporte_valido(self) -> bool:
        return self.__pasaporte_valido

    @pasaporte_valido.setter
    def pasaporte_valido(self, estado: bool) -> None:
        if not isinstance(estado, bool):
            raise ValueError("El estado del pasaporte debe ser un booleano (True/False).")
        self.__pasaporte_valido = estado

    @property
    def requiere_pasaporte(self) -> bool:
        return True

    def calcular_precio(self, tasa_cambio: float | None = None) -> float:
        tasa_aplicada = 900 if tasa_cambio is None else tasa_cambio
        self._validar_monto(tasa_aplicada, "La tasa de cambio", permitir_cero=False)
        return self.precio_base * tasa_aplicada
