# Importa el modelo padre para heredar la información común a todos los paquetes turísticos.
from model.paquete_turistico import Paquete_Turistico  # Evita redefinir los atributos y comportamientos compartidos.

# Define el tipo de paquete para cruceros, que agrega un impuesto portuario al precio calculado.
class Paquete_Crucero(Paquete_Turistico):  # Hereda los datos comunes y personaliza el cálculo del precio para un crucero.
    # Declara el constructor que recibe los datos generales y el impuesto específico del crucero.
    def __init__(self, codigo: int, nombre: str, duracion: int, precio_base: float, impuesto_puerto: float):  # Anota los tipos de los datos requeridos al construir esta instancia.
        super().__init__(codigo, nombre, duracion, precio_base)  # Inicializa mediante la clase padre los datos generales del paquete.
        self._validar_monto(impuesto_puerto, "El impuesto portuario", permitir_cero=True)  # Exige un recargo finito no negativo; cero sigue siendo válido.
        self.__impuesto_puerto = impuesto_puerto  # Guarda de forma privada el impuesto portuario fijo asociado al crucero.

    @property  # Expone el impuesto como una propiedad de solo lectura sin dar acceso directo al atributo privado.
    def impuesto_puerto(self) -> float:  # Declara la propiedad e indica que el impuesto consultado es un valor decimal.
        return self.__impuesto_puerto  # Retorna el impuesto guardado para permitir que otros componentes lo consulten.

    # Polimorfismo: multiplica el precio base por el valor del dólar y suma el impuesto fijo adicional.
    def calcular_precio(self) -> float:  # Sobrescribe el método base para aplicar la regla específica de precio del crucero.
        return (self.precio_base * 900) + self.__impuesto_puerto  # Convierte el precio base y agrega el impuesto portuario al total.