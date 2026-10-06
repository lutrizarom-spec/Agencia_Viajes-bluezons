"""Excepción para reservas internacionales sin pasaporte registrado."""

from __future__ import annotations


class PasaporteRequeridoError(Exception):
    """Señala que el cliente no puede reservar un paquete internacional."""
