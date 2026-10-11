"""Conversión exacta entre importes decimales y centésimos enteros."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
MAX_MINOR_UNITS = 2**63 - 1


def to_minor_units(value: int | float | Decimal) -> int:
    """Convierte un importe finito a centésimos con redondeo explícito."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("El importe debe ser numérico.")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError("El importe debe ser finito y no negativo.")
        minor_units = int(amount.quantize(CENT, rounding=ROUND_HALF_UP) * 100)
    except InvalidOperation as error:
        raise ValueError("El importe no admite la precisión monetaria requerida.") from error
    if amount > 0 and minor_units == 0:
        raise ValueError("El importe es menor que la unidad monetaria mínima.")
    if minor_units > MAX_MINOR_UNITS:
        raise ValueError("El importe excede la capacidad de persistencia.")
    return minor_units


def from_minor_units(value: int) -> Decimal:
    """Devuelve un importe decimal exacto desde centésimos enteros."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_MINOR_UNITS:
        raise ValueError("El importe persistido no es un entero monetario válido.")
    return Decimal(value) / 100


def half_up_minor_units(value: int) -> int:
    """Redondea a centésimos el anticipo equivalente a la mitad del total."""
    return int(
        (Decimal(value) / 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
