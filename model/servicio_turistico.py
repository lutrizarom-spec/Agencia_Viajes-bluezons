"""Modelo base de los servicios turísticos que se agregan a una reserva."""

from __future__ import annotations

from model.validaciones import texto_requerido, validar_entero_positivo, validar_monto


class Servicio_Turistico:
    """Representa un servicio (vuelo, hotel, seguro o excursión) contratable."""

    def __init__(
        self,
        id_servicio: int,
        nombre: str,
        destino: str,
        zona: str,
        precio_base: float,
    ) -> None:
        self.id_servicio = id_servicio
        self.nombre = nombre
        self.destino = destino
        self.zona = zona
        self.precio_base = precio_base

    @property
    def id_servicio(self) -> int:
        return self.__id_servicio

    @id_servicio.setter
    def id_servicio(self, valor: int) -> None:
        self.__id_servicio = validar_entero_positivo(valor, "El ID del servicio")

    @property
    def nombre(self) -> str:
        return self.__nombre

    @nombre.setter
    def nombre(self, valor: str) -> None:
        self.__nombre = texto_requerido(valor, "El nombre del servicio")

    @property
    def destino(self) -> str:
        return self.__destino

    @destino.setter
    def destino(self, valor: str) -> None:
        self.__destino = texto_requerido(valor, "El destino del servicio")

    @property
    def zona(self) -> str:
        return self.__zona

    @zona.setter
    def zona(self, valor: str) -> None:
        self.__zona = texto_requerido(valor, "La zona del servicio")

    @property
    def precio_base(self) -> float:
        return self.__precio_base

    @precio_base.setter
    def precio_base(self, valor: float) -> None:
        validar_monto(valor, "El precio base del servicio", permitir_cero=False)
        self.__precio_base = valor

    def obtener_precio(self) -> float:
        """Devuelve el precio del servicio; los subtipos pueden especializarlo."""
        return self.precio_base

    def calcular_precio(self, tasa_cambio: float | None = None) -> float:
        """Mantiene la misma interfaz de precio que usan los paquetes."""
        _ = tasa_cambio
        return self.obtener_precio()

    def obtener_detalle(self) -> str:
        return (
            f"{type(self).__name__} {self.id_servicio}: {self.nombre} | "
            f"{self.destino} ({self.zona}) | Precio Base: ${self.precio_base}"
        )
