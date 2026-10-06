"""Cliente con datos heredados de Persona y pasaporte textual."""

from __future__ import annotations

from model.persona import Persona


class Cliente(Persona):
    """Representa al cliente, incluido el dato textual de su pasaporte."""

    def __init__(
        self,
        id_cliente: int,
        rut: str,
        nombre: str,
        telefono_movil: str,
        pasaporte: str,
        correo: str,
    ) -> None:
        super().__init__(rut, nombre, telefono_movil)
        self.id_cliente = id_cliente
        self.pasaporte = pasaporte
        self.correo = correo

    @property
    def id_cliente(self) -> int:
        return self.__id_cliente

    @id_cliente.setter
    def id_cliente(self, valor: int) -> None:
        if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
            raise ValueError("El ID del cliente debe ser un entero positivo.")
        self.__id_cliente = valor

    @property
    def pasaporte(self) -> str:
        return self.__pasaporte

    @pasaporte.setter
    def pasaporte(self, valor: str) -> None:
        if not isinstance(valor, str):
            raise ValueError("El pasaporte debe ser texto.")
        self.__pasaporte = valor.strip()

    @property
    def correo(self) -> str:
        return self.__correo

    @correo.setter
    def correo(self, valor: str) -> None:
        texto = self._texto_requerido(valor, "El correo")
        if "@" not in texto:
            raise ValueError("El correo debe incluir el símbolo @.")
        self.__correo = texto

    def validar_pasaporte(self) -> bool:
        """Indica si el cliente registró un número de pasaporte no vacío."""
        return bool(self.pasaporte)
