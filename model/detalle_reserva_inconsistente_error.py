"""Excepción para detalles de reserva que no pueden reconstruirse íntegramente."""

from __future__ import annotations


class DetalleReservaInconsistenteError(Exception):
    """Señala que un detalle persistido referencia datos inexistentes o falta."""
