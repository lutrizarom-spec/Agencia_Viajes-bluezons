"""Reconstruye el subtipo de Paquete_Turistico a partir de columnas persistidas.

Fuente única del mapeo tipo→modelo que antes estaba duplicado en `main_api.py`,
`main.py`, `dao/reserva_dao.py` y `services/compra_service.py`.
"""

from __future__ import annotations

from model.paquete_crucero import Paquete_Crucero
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.paquete_turistico import Paquete_Turistico


def paquete_desde_columnas(
    codigo: int,
    nombre: str,
    duracion: int,
    precio_base: float,
    tipo: str,
    pasaporte: object = None,
    impuesto: object = None,
) -> Paquete_Turistico:
    """Devuelve el subtipo correspondiente al discriminador `tipo` almacenado."""
    if tipo == "internacional":
        return Paquete_Internacional(
            codigo, nombre, duracion, precio_base, bool(pasaporte)
        )
    if tipo == "crucero":
        return Paquete_Crucero(codigo, nombre, duracion, precio_base, impuesto)
    if tipo == "nacional":
        return Paquete_Nacional(codigo, nombre, duracion, precio_base)
    return Paquete_Turistico(codigo, nombre, duracion, precio_base)
