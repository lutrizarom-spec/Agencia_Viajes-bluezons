"""Datos de contacto comunes a personas de la agencia."""

from __future__ import annotations


class Persona:
    """Base que encapsula la identificación y los datos de contacto."""

    def __init__(self, rut: str, nombre: str, telefono_movil: str) -> None:
        self.rut = rut
        self.nombre = nombre
        self.telefono_movil = telefono_movil

    @property
    def rut(self) -> str:
        return self.__rut

    @rut.setter
    def rut(self, valor: str) -> None:
        self.__rut = self._texto_requerido(valor, "El RUT")

    @property
    def nombre(self) -> str:
        return self.__nombre

    @nombre.setter
    def nombre(self, valor: str) -> None:
        self.__nombre = self._texto_requerido(valor, "El nombre")

    @property
    def telefono_movil(self) -> str:
        return self.__telefono_movil

    @telefono_movil.setter
    def telefono_movil(self, valor: str) -> None:
        self.__telefono_movil = self._texto_requerido(valor, "El teléfono móvil")

    @staticmethod
    def _texto_requerido(valor: str, etiqueta: str) -> str:
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError(f"{etiqueta} no puede estar vacío.")
        return valor.strip()

    def validar_datos(self) -> bool:
        return bool(self.rut and self.nombre and self.telefono_movil)
