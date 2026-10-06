"""Pruebas de invariantes de los modelos turísticos y sus subtipos."""

import math  # Proporciona valores de prueba NaN e infinito para importes.
import unittest  # Ejecuta los casos de modelo sin base de datos ni dependencias externas.

from model.paquete_crucero import Paquete_Crucero  # Comprueba atributos e importe específico del crucero.
from model.paquete_internacional import Paquete_Internacional  # Comprueba el estado booleano del pasaporte.
from model.paquete_turistico import Paquete_Turistico  # Comprueba las reglas compartidas por todos los paquetes.


class PackageModelInvariantTests(unittest.TestCase):
    """Verifica que los objetos no puedan nacer con datos inválidos."""

    def test_base_model_rejects_invalid_common_fields(self) -> None:
        """Código, nombre, duración y precio deben cumplir sus invariantes."""
        invalid_packages = (  # Agrupa combinaciones con un solo campo inválido para aislar cada validación.
            (0, "Viaje", 1, 100.0),  # El código no puede ser cero.
            (True, "Viaje", 1, 100.0),  # bool no debe aceptarse como entero aunque Python herede de int.
            (1, "   ", 1, 100.0),  # Un nombre vacío después de quitar espacios no identifica un paquete.
            (1, "Viaje", 0, 100.0),  # La duración debe ser positiva.
            (1, "Viaje", True, 100.0),  # bool no representa una duración entera válida.
            (1, "Viaje", 1, 0.0),  # El precio base debe ser mayor que cero.
            (1, "Viaje", 1, -1.0),  # Un precio negativo no tiene sentido comercial.
            (1, "Viaje", 1, math.nan),  # NaN contaminaría cálculos y persistencia.
            (1, "Viaje", 1, math.inf),  # Infinito no es un precio persistible.
            (1, "Viaje", 1, True),  # bool no debe ser aceptado como un importe numérico.
        )  # Cierra los datos inválidos que se ejercerán en subtests independientes.
        for arguments in invalid_packages:  # Comprueba que cada entrada no válida falle durante construcción.
            with self.subTest(arguments=arguments):  # Identifica exactamente qué combinación produjo el resultado.
                with self.assertRaises(ValueError):  # El dominio informa explícitamente un argumento inválido.
                    Paquete_Turistico(*arguments)  # Intenta construir el modelo base con la combinación.

    def test_valid_common_fields_are_preserved_and_name_is_trimmed(self) -> None:
        """Los datos válidos siguen accesibles y conservan el cálculo base."""
        package = Paquete_Turistico(8, "  Ruta Austral  ", 5, 125000)  # Acepta entero para precio sin cambiar su cálculo.
        self.assertEqual(package.codigo, 8)  # Conserva el identificador positivo.
        self.assertEqual(package.nombre, "Ruta Austral")  # Normaliza espacios exteriores para almacenar un nombre canónico.
        self.assertEqual(package.duracion, 5)  # Conserva la duración positiva.
        self.assertEqual(package.precio_base, 125000)  # Preserva el valor numérico original sin forzar una conversión.
        self.assertEqual(package.calcular_precio(), 125000)  # Mantiene sin cambios la fórmula de la clase base.
        package.nombre = "  Otro viaje  "  # El setter conserva la encapsulación y normaliza el nuevo valor.
        self.assertEqual(package.nombre, "Otro viaje")  # Comprueba que la asignación válida pasa por la propiedad.
        with self.assertRaises(ValueError):  # El setter rechaza un nombre que viola la regla del dominio.
            package.nombre = "   "  # Un nombre vacío no puede reemplazar el valor válido.

    def test_cruise_tax_must_be_finite_and_non_negative(self) -> None:
        """El impuesto del crucero admite cero, pero rechaza negativos y no finitos."""
        for invalid_tax in (-0.01, math.nan, math.inf, True, "100"):  # Incluye importes negativos, no finitos y tipos impropios.
            with self.subTest(tax=invalid_tax):  # Mantiene cada valor inválido identificable.
                with self.assertRaises(ValueError):  # El constructor comunica el error de dominio.
                    Paquete_Crucero(1, "Crucero", 3, 100.0, invalid_tax)  # No crea el crucero con un impuesto incorrecto.

        cruise = Paquete_Crucero(1, "Crucero", 3, 100.0, 0)  # Un impuesto nulo es válido para paquetes sin recargo portuario.
        self.assertEqual(cruise.impuesto_puerto, 0)  # Conserva el valor válido sin alterar la regla del subtipo.
        self.assertEqual(cruise.calcular_precio(), 90000.0)  # Mantiene la fórmula existente: precio base por 900 más impuesto.

    def test_international_model_requires_a_boolean_passport_flag(self) -> None:
        """El constructor y el setter aplican el mismo tipo estricto al pasaporte."""
        with self.assertRaises(ValueError):  # Un entero no debe confundirse con un booleano.
            Paquete_Internacional(1, "Internacional", 4, 100.0, 1)  # Intenta crear el subtipo con tipo incorrecto.

        package = Paquete_Internacional(1, "Internacional", 4, 100.0, True)  # Crea un paquete con pasaporte válido.
        self.assertTrue(package.pasaporte_valido)  # Comprueba que el booleano válido se conserva.
        with self.assertRaises(ValueError):  # El setter conserva su validación para actualizaciones posteriores.
            package.pasaporte_valido = 0  # Rechaza el entero cero aunque tenga significado de falso en una condición.
        self.assertEqual(package.calcular_precio(), 90000.0)  # Confirma que validar el pasaporte no cambia la fórmula internacional.
        self.assertEqual(package.calcular_precio(987.65), 98765.0)  # Permite calcular con la cotización congelada por la compra.
        for invalid_rate in (0, -1, math.nan, math.inf, True, "900"):  # No acepta tasas nulas, negativas, no finitas ni tipos incorrectos.
            with self.subTest(rate=invalid_rate):
                with self.assertRaises(ValueError):
                    getattr(package, "calcular_precio")(invalid_rate)

    def test_cruise_uses_supplied_exchange_rate_and_keeps_port_tax(self) -> None:
        """La cotización recibida reemplaza el multiplicador fijo durante una reserva."""
        cruise = Paquete_Crucero(1, "Crucero", 3, 100.0, 12500.0)
        self.assertEqual(cruise.calcular_precio(987.65), 111265.0)


if __name__ == "__main__":  # Permite ejecutar esta suite directamente durante desarrollo local.
    unittest.main()  # Ejecuta todos los casos declarados en la clase.
