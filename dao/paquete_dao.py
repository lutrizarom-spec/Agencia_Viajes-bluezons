import sqlite3  # Importa SQLite para definir el tipo de los resultados consultados en la base de datos.
from dao.dao import Dao  # Importa la clase base que proporciona la conexión y el cursor reutilizable.
from model.paquete_crucero import Paquete_Crucero  # Importa el modelo de crucero para reconocer su impuesto específico.
from model.paquete_internacional import Paquete_Internacional  # Importa el modelo internacional para reconocer su dato de pasaporte.
from model.paquete_nacional import Paquete_Nacional  # Importa el modelo nacional para clasificar sus instancias al guardarlas.
from model.paquete_turistico import Paquete_Turistico  # Importa el modelo padre usado como tipo de entrada para los paquetes.


class PaqueteDao(Dao):  # Define el acceso a datos de paquetes y hereda la conexión y el cursor comunes.
    def crear_tabla(self) -> None:  # Define la operación que prepara la tabla de paquetes si todavía no existe.
        self.cursor.execute("CREATE TABLE IF NOT EXISTS paquetes (codigo INTEGER PRIMARY KEY, nombre TEXT NOT NULL, duracion INTEGER NOT NULL, precio_base REAL NOT NULL, tipo TEXT NOT NULL, pasaporte_valido INTEGER, impuesto_puerto REAL)")  # Crea el esquema para los datos comunes y los atributos específicos de los subtipos.
        self.conexion.commit()  # Confirma la creación de la tabla para hacerla persistente en la base de datos.

    def insertar_paquete(self, paquete: Paquete_Turistico) -> None:  # Define la operación que almacena un objeto de cualquiera de los modelos de paquete.
        tipo = "turistico"  # Establece el tipo genérico como valor inicial para paquetes de la clase base.
        pasaporte_valido = None  # Inicializa sin dato de pasaporte porque solo aplica a paquetes internacionales.
        impuesto_puerto = None  # Inicializa sin impuesto portuario porque solo aplica a paquetes de crucero.
        if isinstance(paquete, Paquete_Internacional):  # Comprueba primero si el paquete requiere almacenar validez de pasaporte.
            tipo = "internacional"  # Registra la categoría del subtipo internacional en la columna tipo.
            pasaporte_valido = int(paquete.pasaporte_valido)  # Convierte el booleano a 0 o 1 para guardarlo en una columna SQLite INTEGER.
        elif isinstance(paquete, Paquete_Crucero):  # Comprueba si el paquete es un crucero con un impuesto portuario propio.
            tipo = "crucero"  # Registra la categoría de crucero para identificar posteriormente el subtipo.
            impuesto_puerto = paquete.impuesto_puerto  # Obtiene y conserva el impuesto portuario mediante su propiedad pública.
        elif isinstance(paquete, Paquete_Nacional):  # Comprueba si el objeto corresponde a un paquete nacional.
            tipo = "nacional"  # Registra la categoría nacional para identificar el subtipo al leer los datos.
        self.cursor.execute("INSERT INTO paquetes (codigo, nombre, duracion, precio_base, tipo, pasaporte_valido, impuesto_puerto) VALUES (?, ?, ?, ?, ?, ?, ?)", (paquete.codigo, paquete.nombre, paquete.duracion, paquete.precio_base, tipo, pasaporte_valido, impuesto_puerto))  # Inserta los valores de forma parametrizada para respetar tipos y evitar concatenar SQL con datos.
        self.conexion.commit()  # Confirma la inserción para que el paquete quede guardado de forma persistente.

    def obtener_paquetes(self) -> list[tuple]:  # Define la consulta que devuelve todos los registros como una lista de filas SQLite.
        self.cursor.execute("SELECT codigo, nombre, duracion, precio_base, tipo, pasaporte_valido, impuesto_puerto FROM paquetes")  # Solicita todas las columnas de los paquetes guardados.
        return self.cursor.fetchall()  # Entrega las filas consultadas, cada una como una tupla en una lista.

    def eliminar_paquete(self, codigo: int) -> int:  # Define la eliminación por código y devuelve cuántos registros fueron afectados.
        self.cursor.execute("DELETE FROM paquetes WHERE codigo = ?", (codigo,))  # Elimina el registro cuyo código coincide usando un parámetro SQL seguro.
        self.conexion.commit()  # Confirma la eliminación para que se aplique en la base de datos.
        return self.cursor.rowcount  # Devuelve la cantidad de filas eliminadas para que quien llama sepa si encontró el código.
