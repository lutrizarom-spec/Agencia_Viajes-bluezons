"""Pruebas de conversión monetaria y redondeo explícito."""

import unittest
from decimal import Decimal

from model.money import from_minor_units, half_up_minor_units, to_minor_units


class MoneyTests(unittest.TestCase):
    def test_decimal_amounts_use_half_up_rounding(self) -> None:
        self.assertEqual(to_minor_units(Decimal("1.005")), 101)
        self.assertEqual(to_minor_units(Decimal("1.004")), 100)
        self.assertEqual(from_minor_units(101), Decimal("1.01"))

    def test_half_advance_rounds_half_cent_up(self) -> None:
        self.assertEqual(half_up_minor_units(1), 1)
        self.assertEqual(half_up_minor_units(2), 1)

    def test_nonfinite_negative_and_overflow_amounts_are_rejected(self) -> None:
        for invalid in (Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01"), 2**63):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                to_minor_units(invalid)

    def test_positive_amount_below_smallest_unit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            to_minor_units(Decimal("0.001"))


if __name__ == "__main__":
    unittest.main()
