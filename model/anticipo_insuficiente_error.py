"""Excepción para anticipos inferiores al mínimo exigido por la reserva."""

from __future__ import annotations


class AnticipoInsuficienteError(Exception):
    """Señala que el anticipo no alcanza el 50 % del total de la reserva."""
