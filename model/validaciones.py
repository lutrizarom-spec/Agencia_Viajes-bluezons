"""Validaciones numéricas y textuales compartidas por los modelos."""

from __future__ import annotations

import math


def validar_monto(monto: float, etiqueta: str, *, permitir_cero: bool) -> None:
    """Exige un número finito y no negativo, opcionalmente mayor que cero."""
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


def validar_entero_positivo(valor: int, etiqueta: str) -> int:
    """Exige un entero mayor que cero, excluyendo los booleanos."""
    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise ValueError(f"{etiqueta} debe ser un entero positivo.")
    return valor


def texto_requerido(valor: str, etiqueta: str) -> str:
    """Exige un texto no vacío y retorna su versión sin espacios exteriores."""
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"{etiqueta} no puede estar vacío.")
    return valor.strip()
