# Importa el modelo padre para compartir sus datos y propiedades comunes con los paquetes internacionales.
from model.paquete_turistico import Paquete_Turistico  # Permite extender el comportamiento existente sin duplicar la clase base.

# Define la variante internacional que agrega el estado del pasaporte y una regla de cálculo propia.
class Paquete_Internacional(Paquete_Turistico):  # Hereda los datos comunes del paquete y personaliza operaciones cuando corresponde.
    # Declara el constructor que recibe los datos comunes y el estado de validez del pasaporte.
    def __init__(self, codigo: int, nombre: str, duracion: int, precio_base: float, pasaporte_valido: bool):  # Anota cada argumento para describir los valores esperados durante la creación.
        super().__init__(codigo, nombre, duracion, precio_base)  # Delega en la clase padre la inicialización de los datos compartidos.
        self.pasaporte_valido = pasaporte_valido  # Reutiliza la validación del setter también durante la creación del objeto.

    @property  # Expone el estado del pasaporte como propiedad pública de solo lectura directa.
    def pasaporte_valido(self) -> bool:  # Declara la propiedad y documenta que devuelve un valor booleano.
        return self.__pasaporte_valido  # Retorna el estado privado guardado para permitir consultarlo.

    @pasaporte_valido.setter  # Asocia este método como mecanismo de escritura para la propiedad pasaporte_valido.
    def pasaporte_valido(self, estado: bool):  # Declara el asignador que recibe el nuevo estado solicitado para el pasaporte.
        # Validación simple requerida por la rúbrica para impedir guardar valores que no sean booleanos.
        if not isinstance(estado, bool):  # Comprueba que el valor recibido sea realmente True o False y no otro tipo.
            raise ValueError("El estado del pasaporte debe ser un booleano (True/False).")  # Informa explícitamente que el dato ingresado no cumple el tipo requerido.
        self.__pasaporte_valido = estado  # Actualiza el atributo privado solo después de validar el valor.

    def calcular_precio(self, tasa_cambio: float | None = None) -> float:  # Permite fijar la tasa de la reserva y mantiene compatibilidad con el factor histórico.
        tasa_aplicada = 900 if tasa_cambio is None else tasa_cambio  # Usa la tasa suministrada o el valor histórico para llamadas fuera de una reserva.
        self._validar_monto(tasa_aplicada, "La tasa de cambio", permitir_cero=False)  # Impide tasas no numéricas, no finitas o no positivas.
        return self.precio_base * tasa_aplicada  # Calcula CLP usando la cotización congelada por la compra cuando se entrega.