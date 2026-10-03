# Define el modelo base que reúne los datos comunes a todos los paquetes turísticos.
class Paquete_Turistico:  # Sirve como clase padre para compartir atributos y comportamientos con los tipos concretos de paquete.
    # Declara el inicializador que recibe los datos básicos con los que se construye un paquete turístico.
    def __init__(self, codigo: int, nombre: str, duracion: int, precio_base: float):  # Anota los tipos esperados para documentar y facilitar el análisis de los parámetros.
        # Atributos privados que mantienen el estado interno del paquete protegido del acceso directo.
        self.__codigo = codigo  # Conserva el identificador único del paquete para reconocerlo.
        self.__nombre = nombre  # Conserva el nombre descriptivo que identifica el paquete al usuario.
        self.__duracion = duracion  # Conserva la duración del viaje, expresada en días.
        self.__precio_base = precio_base  # Conserva el precio inicial usado en los cálculos de las clases derivadas.

    # Métodos getter que exponen los atributos privados mediante propiedades de solo lectura.
    @property  # Permite consultar el código como atributo sin exponer directamente el campo privado.
    def codigo(self) -> int:  # Declara la propiedad que devuelve el identificador del paquete como entero.
        return self.__codigo  # Entrega el código guardado para que otros componentes puedan consultarlo.

    @property  # Convierte el método siguiente en una propiedad pública de lectura para el nombre.
    def nombre(self) -> str:  # Declara la propiedad que devuelve el nombre del paquete como texto.
        return self.__nombre  # Entrega el nombre guardado para mostrar o procesar el paquete.

    @property  # Convierte el método siguiente en una propiedad pública de lectura para la duración.
    def duracion(self) -> int:  # Declara la propiedad que devuelve la duración del paquete como entero.
        return self.__duracion  # Entrega la cantidad de días guardada para describir el viaje.

    @property  # Convierte el método siguiente en una propiedad pública de lectura para el precio base.
    def precio_base(self) -> float:  # Declara la propiedad que devuelve el precio base como número decimal.
        return self.__precio_base  # Entrega el valor base para que el paquete o sus subclases calculen el precio final.

    # Método que retorna el precio base y puede ser sobrescrito para aplicar reglas específicas en clases hijas.
    def calcular_precio(self) -> float:  # Define el cálculo predeterminado y señala que su resultado es un número decimal.
        return self.__precio_base  # Usa el precio base sin recargos como precio para el paquete genérico.

    # Método para obtener una descripción legible que reúna los datos principales del paquete.
    def obtener_detalle(self) -> str:  # Define una operación que devuelve el detalle completo en formato de texto.
        return f"Paquete {self.__codigo}: {self.__nombre} | {self.__duracion} días | Precio Base: ${self.__precio_base}"  # Interpola los atributos para presentar código, nombre, duración y precio base.