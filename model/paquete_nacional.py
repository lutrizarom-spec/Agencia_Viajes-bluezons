# Importa el modelo padre para heredar sus datos y propiedades comunes a los paquetes turísticos.
from model.paquete_turistico import Paquete_Turistico  # Reutiliza el modelo base sin duplicar su definición.

# Define la variante nacional del paquete turístico con su regla de precio particular.
class Paquete_Nacional(Paquete_Turistico):  # Hereda de Paquete_Turistico para conservar sus atributos y operaciones compartidas.
    # Declara un constructor que recibe los datos básicos requeridos para un paquete nacional.
    def __init__(self, codigo: int, nombre: str, duracion: int, precio_base: float):  # Anota los tipos de los datos que se entregan al crear la instancia.
        super().__init__(codigo, nombre, duracion, precio_base)  # Inicializa en la clase padre los datos comunes de este paquete.

    # El paquete nacional no tiene recargo y retorna el precio base en pesos tal cual.
    def calcular_precio(self, tasa_cambio: float | None = None) -> float:  # Sobrescribe el cálculo heredado sin aplicar conversión de moneda.
        _ = tasa_cambio  # Conserva la firma compartida sin usar cotización para un precio en moneda local.
        return self.precio_base  # Devuelve el precio base consultando la propiedad pública heredada.