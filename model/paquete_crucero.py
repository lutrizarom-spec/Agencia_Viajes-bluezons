"""Paquete turístico de crucero."""

from __future__ import annotations

from model.paquete_turistico import Paquete_Turistico


class Paquete_Crucero(Paquete_Turistico):
    """Convierte el precio base y suma el impuesto portuario."""

    def __init__(
        self,
        codigo: int,
        nombre: str,
        duracion: int,
        precio_base: float,
        impuesto_puerto: float,
    ) -> None:
        super().__init__(codigo, nombre, duracion, precio_base)
        self.impuesto_puerto = impuesto_puerto

    @property
    def impuesto_puerto(self) -> float:
        return self.__impuesto_puerto

    @impuesto_puerto.setter
    def impuesto_puerto(self, monto: float) -> None:
        self._validar_monto(monto, "El impuesto portuario", permitir_cero=True)
        self.__impuesto_puerto = monto

    def calcular_precio(self, tasa_cambio: float | None = None) -> float:
        tasa_aplicada = 900 if tasa_cambio is None else tasa_cambio
        self._validar_monto(tasa_aplicada, "La tasa de cambio", permitir_cero=False)
        return (self.precio_base * tasa_aplicada) + self.impuesto_puerto
