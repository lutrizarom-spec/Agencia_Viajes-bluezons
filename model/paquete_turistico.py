# Define el modelo base que reúne los datos comunes a todos los paquetes turísticos.
import math  # Permite rechazar precios infinitos o NaN antes de que entren al dominio.


class Paquete_Turistico:  # Sirve como clase padre para compartir atributos y comportamientos con los tipos concretos de paquete.
    # Declara el inicializador que recibe los datos básicos con los que se construye un paquete turístico.
    def __init__(self, codigo: int, nombre: str, duracion: int, precio_base: float):  # Anota los tipos esperados para documentar y facilitar el análisis de los parámetros.
        # Rechaza bool además de otros tipos porque en Python bool hereda de int.
        if isinstance(codigo, bool) or not isinstance(codigo, int) or codigo <= 0:  # Un paquete necesita un identificador entero positivo.
            raise ValueError("El código del paquete debe ser un entero positivo.")  # Informa el primer dato de dominio inválido.
        if not isinstance(nombre, str) or not nombre.strip():  # Exige un nombre textual que no esté vacío ni contenga solo espacios.
            raise ValueError("El nombre del paquete no puede estar vacío.")  # Evita guardar paquetes imposibles de identificar en la interfaz.
        if isinstance(duracion, bool) or not isinstance(duracion, int) or duracion <= 0:  # La duración debe ser una cantidad entera positiva de días.
            raise ValueError("La duración debe ser un entero positivo de días.")  # Evita viajes sin duración o con unidades inválidas.
        self._validar_monto(precio_base, "El precio base", permitir_cero=False)  # Valida el precio sin alterar su valor ni su fórmula.
        # Atributos privados que mantienen el estado interno del paquete protegido del acceso directo.
        self.__codigo = codigo  # Conserva el identificador único del paquete para reconocerlo.
        self.__nombre = nombre.strip()  # Conserva el nombre sin espacios exteriores que causan duplicados visuales.
        self.__duracion = duracion  # Conserva la duración del viaje, expresada en días.
        self.__precio_base = precio_base  # Conserva el precio inicial usado en los cálculos de las clases derivadas.

    @staticmethod  # Permite validar montos desde la clase base y sus subtipos sin requerir una instancia inicializada.
    def _validar_monto(monto: float, etiqueta: str, *, permitir_cero: bool) -> None:  # Centraliza reglas monetarias compartidas por precio e impuestos.
        """Exige un monto numérico, finito y positivo o no negativo según corresponda."""
        if isinstance(monto, bool) or not isinstance(monto, (int, float)):  # Acepta los tipos numéricos del dominio y excluye booleanos.
            raise ValueError(f"{etiqueta} debe ser un número válido.")  # Rechaza texto y otros tipos antes de calcular precios.
        try:  # La conversión permite detectar enteros demasiado grandes para representarse como número finito.
            es_finito = math.isfinite(float(monto))  # Comprueba finitud sin modificar el monto que se guardará.
        except OverflowError:  # Python puede elevar este error al convertir un entero enorme a float.
            es_finito = False  # Clasifica el valor no representable como monto inválido.
        if not es_finito:  # Ningún precio o impuesto puede ser NaN ni infinito.
            raise ValueError(f"{etiqueta} debe ser un número finito.")  # Evita contaminar cálculos, respuestas y persistencia.
        if monto < 0 or (monto == 0 and not permitir_cero):  # El precio exige valor positivo; un recargo puede ser cero pero no negativo.
            condicion = "no puede ser negativo" if permitir_cero else "debe ser mayor que cero"  # Explica el límite aplicable.
            raise ValueError(f"{etiqueta} {condicion}.")  # Rechaza montos fuera de la regla del dominio.

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
    def calcular_precio(self, tasa_cambio: float | None = None) -> float:  # Acepta una tasa para mantener una firma común entre subtipos.
        _ = tasa_cambio  # Mantiene la firma polimórfica aunque el paquete genérico no convierte moneda.
        return self.__precio_base  # Usa el precio base sin recargos como precio para el paquete genérico.

    # Método para obtener una descripción legible que reúna los datos principales del paquete.
    def obtener_detalle(self) -> str:  # Define una operación que devuelve el detalle completo en formato de texto.
        return f"Paquete {self.__codigo}: {self.__nombre} | {self.__duracion} días | Precio Base: ${self.__precio_base}"  # Interpola los atributos para presentar código, nombre, duración y precio base.