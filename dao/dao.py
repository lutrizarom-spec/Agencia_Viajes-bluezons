import sqlite3  # Importa el módulo SQLite para anotar y operar con conexiones a bases de datos locales.


# Declara Dao como objeto de acceso a datos, encargado de trabajar con una conexión SQLite existente.
class Dao:  # Agrupa la conexión y el cursor que las operaciones de acceso a datos reutilizarán.
    # Define el inicializador que recibe la conexión que usará esta instancia de Dao.
    def __init__(self, conexion: sqlite3.Connection) -> None:  # Anota el tipo recibido y que el inicializador no retorna un valor.
        """Conserva la conexión SQLite para las operaciones de los DAO concretos.

        La instancia recibe una conexión ya creada, de modo que el componente
        que coordina la aplicación mantiene el control de su ciclo de vida.
        """
        self.conexion = conexion  # Guarda la conexión recibida para que los métodos de la instancia puedan reutilizarla.
        self.cursor = self.conexion.cursor()  # Crea y conserva un cursor asociado a la conexión para ejecutar consultas SQLite.
