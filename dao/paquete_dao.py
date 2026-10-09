from dao.dao import Dao  # Importa la clase base que proporciona la conexión y el cursor reutilizable.
from model.paquete_crucero import Paquete_Crucero  # Importa el modelo de crucero para reconocer su impuesto específico.
from model.paquete_internacional import Paquete_Internacional  # Importa el modelo internacional para reconocer su dato de pasaporte.
from model.paquete_nacional import Paquete_Nacional  # Importa el modelo nacional para clasificar sus instancias al guardarlas.
from model.paquete_turistico import Paquete_Turistico  # Importa el modelo padre usado como tipo de entrada para los paquetes.


class PaqueteDao(Dao):  # Define el acceso a datos de paquetes y hereda la conexión y el cursor comunes.
    def crear_tabla(self) -> None:  # Define la operación que prepara la tabla de paquetes si todavía no existe.
        """Crea el esquema de paquetes sin reemplazar una tabla ya existente."""
        self.conexion.execute("SAVEPOINT package_schema_migration")
        try:
            self.cursor.execute("CREATE TABLE IF NOT EXISTS paquetes (codigo INTEGER PRIMARY KEY, nombre TEXT NOT NULL, duracion INTEGER NOT NULL, precio_base REAL NOT NULL, tipo TEXT NOT NULL, pasaporte_valido INTEGER, impuesto_puerto REAL, activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)))")  # Crea datos compartidos, campos específicos y la bandera de publicación.
            columns = {column[1] for column in self.cursor.execute("PRAGMA table_info(paquetes)").fetchall()}  # Lee las columnas actuales para reconocer esquemas anteriores.
            if "activo" not in columns:  # Comprueba si la base todavía no tiene soporte para baja lógica.
                self.cursor.execute("ALTER TABLE paquetes ADD COLUMN activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))")  # Migra sin borrar datos y mantiene publicados los registros existentes.
            self.conexion.execute("RELEASE SAVEPOINT package_schema_migration")
        except Exception:
            self.conexion.execute("ROLLBACK TO SAVEPOINT package_schema_migration")
            self.conexion.execute("RELEASE SAVEPOINT package_schema_migration")
            raise

    def insertar_paquete(self, paquete: Paquete_Turistico) -> None:  # Define la operación que almacena un objeto de cualquiera de los modelos de paquete.
        """Guarda los datos comunes y las columnas del subtipo correspondiente."""
        tipo, pasaporte_valido, impuesto_puerto = self._specific_fields(paquete)  # Prepara los campos que dependen del subtipo.
        self.cursor.execute("INSERT INTO paquetes (codigo, nombre, duracion, precio_base, tipo, pasaporte_valido, impuesto_puerto) VALUES (?, ?, ?, ?, ?, ?, ?)", (paquete.codigo, paquete.nombre, paquete.duracion, paquete.precio_base, tipo, pasaporte_valido, impuesto_puerto))  # Inserta de forma parametrizada y usa activo=1 por defecto.
        self.conexion.commit()  # Confirma la inserción para que el paquete quede guardado de forma persistente.

    def actualizar_paquete(self, paquete: Paquete_Turistico, codigo: int) -> int:  # Modifica un paquete activo sin cambiar su identificador histórico.
        """Actualiza los datos del paquete y conserva su estado de publicación."""
        tipo, pasaporte_valido, impuesto_puerto = self._specific_fields(paquete)  # Serializa el subtipo y sus atributos propios.
        self.cursor.execute(  # Modifica exclusivamente campos editables y exige que el registro siga activo.
            """
            UPDATE paquetes
            SET nombre = ?, duracion = ?, precio_base = ?, tipo = ?,
                pasaporte_valido = ?, impuesto_puerto = ?
            WHERE codigo = ? AND activo = 1
            """,
            (paquete.nombre, paquete.duracion, paquete.precio_base, tipo, pasaporte_valido, impuesto_puerto, codigo),  # Parametriza los valores y mantiene fijo el código.
        )  # Una baja lógica previa no puede revertirse por accidente con un PUT.
        self.conexion.commit()  # Confirma de manera atómica los cambios del paquete.
        return self.cursor.rowcount  # Indica si se actualizó un paquete publicable.

    def obtener_paquetes(self, incluir_inactivos: bool = False) -> list[tuple]:  # Define la consulta pública y la opción de incluir bajas para administración.
        """Retorna los registros almacenados para que la aplicación los presente."""
        query = "SELECT codigo, nombre, duracion, precio_base, tipo, pasaporte_valido, impuesto_puerto FROM paquetes"  # Mantiene sin cambios el formato de filas consumido por el catálogo.
        if not incluir_inactivos:  # Oculta las bajas en el catálogo y en los listados habituales.
            query += " WHERE activo = 1"  # Solo permite descubrir paquetes publicados.
        query += " ORDER BY codigo"  # Garantiza orden estable en las respuestas.
        self.cursor.execute(query)  # Ejecuta la consulta usando la forma de fila ya conocida.
        return self.cursor.fetchall()  # Entrega las filas consultadas, cada una como una tupla en una lista.

    def obtener_paquetes_admin(self) -> list[tuple]:  # Lista activos e inactivos con su estado de publicación para administradores.
        """Retorna el catálogo completo sin alterar la consulta pública."""
        self.cursor.execute("SELECT codigo, nombre, duracion, precio_base, tipo, pasaporte_valido, impuesto_puerto, activo FROM paquetes ORDER BY codigo")  # Incluye la columna activo en el contrato interno administrativo.
        return self.cursor.fetchall()  # Devuelve filas completas ordenadas por el código.

    def obtener_paquete(self, codigo: int, *, incluir_inactivo: bool = False) -> tuple | None:  # Busca un paquete concreto para cambios administrativos.
        """Busca un registro activo o incluye bajas si se pide expresamente."""
        query = "SELECT codigo, nombre, duracion, precio_base, tipo, pasaporte_valido, impuesto_puerto, activo FROM paquetes WHERE codigo = ?"  # Consulta una clave primaria con parámetro seguro.
        if not incluir_inactivo:  # Por defecto permite encontrar solo recursos visibles/actualizables.
            query += " AND activo = 1"  # Impide que una baja aparezca como paquete editable.
        return self.cursor.execute(query, (codigo,)).fetchone()  # Devuelve la fila o None cuando no aplica.

    def eliminar_paquete(self, codigo: int) -> int:  # Conserva la firma pública histórica y ahora realiza baja lógica.
        """Oculta el paquete del catálogo sin borrar reservas, pagos ni historial."""
        self.cursor.execute("UPDATE paquetes SET activo = 0 WHERE codigo = ? AND activo = 1", (codigo,))  # Desactiva solo una fila actualmente publicada.
        self.conexion.commit()  # Persiste la baja lógica sin alterar referencias.
        return self.cursor.rowcount  # Indica si el paquete cambió de activo a inactivo.

    @staticmethod  # Permite reutilizar la conversión de subtipos en creación y edición.
    def _specific_fields(paquete: Paquete_Turistico) -> tuple[str, int | None, float | None]:  # Convierte el subtipo de dominio a columnas SQL.
        """Prepara discriminador, validez de pasaporte e impuesto portuario."""
        tipo = "turistico"  # El modelo base no aporta campos específicos.
        pasaporte_valido = None  # Solo se guarda en paquetes internacionales.
        impuesto_puerto = None  # Solo se guarda en cruceros.
        if isinstance(paquete, Paquete_Internacional):  # Comprueba primero el subtipo con atributo de pasaporte.
            tipo = "internacional"  # Marca la categoría para reconstrucción posterior.
            pasaporte_valido = int(paquete.pasaporte_valido)  # Transforma el booleano a INTEGER persistible en SQLite.
        elif isinstance(paquete, Paquete_Crucero):  # Comprueba el subtipo con impuesto portuario.
            tipo = "crucero"  # Registra la categoría de crucero.
            impuesto_puerto = paquete.impuesto_puerto  # Recupera el importe validado por el modelo.
        elif isinstance(paquete, Paquete_Nacional):  # Comprueba el paquete nacional sin atributos extra.
            tipo = "nacional"  # Persiste el subtipo para lecturas polimórficas.
        return tipo, pasaporte_valido, impuesto_puerto  # Devuelve el discriminador y las dos columnas opcionales.
