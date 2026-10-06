"""Modelo base compartido por los paquetes turísticos."""

from __future__ import annotations

import math


class Paquete_Turistico:
    """Define los datos comunes y el cálculo de precio por defecto."""

    def __init__(self, codigo: int, nombre: str, duracion: int, precio_base: float) -> None:
        self.codigo = codigo
        self.nombre = nombre
        self.duracion = duracion
        self.precio_base = precio_base

    @property
    def codigo(self) -> int:
        return self.__codigo

    @codigo.setter
    def codigo(self, valor: int) -> None:
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            raise ValueError("El código del paquete debe ser un entero positivo.")
        self.__codigo = valor

    @property
    def nombre(self) -> str:
        return self.__nombre

    @nombre.setter
    def nombre(self, valor: str) -> None:
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError("El nombre del paquete no puede estar vacío.")
        self.__nombre = valor.strip()

    @property
    def duracion(self) -> int:
        return self.__duracion

    @duracion.setter
    def duracion(self, valor: int) -> None:
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            raise ValueError("La duración debe ser un entero positivo de días.")
        self.__duracion = valor

    @property
    def precio_base(self) -> float:
        return self.__precio_base

    @precio_base.setter
    def precio_base(self, valor: float) -> None:
        self._validar_monto(valor, "El precio base", permitir_cero=False)
        self.__precio_base = valor

    @staticmethod
    def _validar_monto(monto: float, etiqueta: str, *, permitir_cero: bool) -> None:
        if isinstance(monto, bool) or not isinstance(monto, (int, float)):
            raise ValueError(f"{etiqueta} debe ser un número válido.")
        try:
            es_finito = math.isfinite(float(monto))
        except OverflowError:
            es_finito = False
        if not es_finito:
            raise ValueError(f"{etiqueta} debe ser un número finito.")
        if monto < 0 or (monto == 0 and not permitir_cero):
            condicion = "no puede ser negativo" if permitir_cero else "debe ser mayor que cero"
            raise ValueError(f"{etiqueta} {condicion}.")

    @property
    def requiere_pasaporte(self) -> bool:
        return False

    def calcular_precio(self, tasa_cambio: float | None = None) -> float:
        _ = tasa_cambio
        return self.precio_base

    def obtener_detalle(self) -> str:
        return (
            f"Paquete {self.codigo}: {self.nombre} | {self.duracion} días "
            f"| Precio Base: ${self.precio_base}"
        )
