"""Acceso a datos de clientes sobre SQLite, separado del modelo y de la API."""

from __future__ import annotations

from dao.dao import Dao
from model.cliente import Cliente


class ClienteDao(Dao):
    """Implementa el CRUD de clientes con baja lógica y consultas parametrizadas."""

    def crear_tabla(self) -> None:
        """Crea la tabla de clientes si no existe, sin borrar datos previos."""
        self.conexion.execute("SAVEPOINT cliente_schema_migration")
        try:
            self.cursor.execute(
                "CREATE TABLE IF NOT EXISTS clientes (id_cliente INTEGER PRIMARY KEY, rut TEXT NOT NULL, nombre TEXT NOT NULL, telefono_movil TEXT NOT NULL, pasaporte TEXT, correo TEXT NOT NULL, activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)))"
            )  # Crea la tabla con el estado de publicación para la baja lógica.
            columns = {column[1] for column in self.cursor.execute("PRAGMA table_info(clientes)").fetchall()}  # Reconoce esquemas anteriores sin la columna activo.
            if "activo" not in columns:
                self.cursor.execute("ALTER TABLE clientes ADD COLUMN activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))")  # Migra sin eliminar registros existentes.
            self.conexion.execute("RELEASE SAVEPOINT cliente_schema_migration")
        except Exception:
            self.conexion.execute("ROLLBACK TO SAVEPOINT cliente_schema_migration")
            self.conexion.execute("RELEASE SAVEPOINT cliente_schema_migration")
            raise

    def insertar_cliente(self, cliente: Cliente) -> None:
        """Guarda un cliente nuevo; usa activo=1 por defecto."""
        self.cursor.execute(
            "INSERT INTO clientes (id_cliente, rut, nombre, telefono_movil, pasaporte, correo) VALUES (?, ?, ?, ?, ?, ?)",
            (cliente.id_cliente, cliente.rut, cliente.nombre, cliente.telefono_movil, cliente.pasaporte, cliente.correo),
        )  # Parametriza todos los valores; el texto del usuario nunca se concatena.
        self.conexion.commit()

    def obtener_cliente(self, id_cliente: int, *, incluir_inactivo: bool = False) -> tuple | None:
        """Busca un cliente activo o incluye la baja si se pide expresamente."""
        query = "SELECT id_cliente, rut, nombre, telefono_movil, pasaporte, correo, activo FROM clientes WHERE id_cliente = ?"
        if not incluir_inactivo:
            query += " AND activo = 1"
        return self.cursor.execute(query, (id_cliente,)).fetchone()  # Devuelve la fila o None.

    def listar_clientes(self, incluir_inactivos: bool = False) -> list[tuple]:
        """Lista clientes activos o todos, con orden estable por identificador."""
        query = "SELECT id_cliente, rut, nombre, telefono_movil, pasaporte, correo, activo FROM clientes"
        if not incluir_inactivos:
            query += " WHERE activo = 1"
        query += " ORDER BY id_cliente"
        self.cursor.execute(query)
        return self.cursor.fetchall()

    def actualizar_cliente(self, cliente: Cliente, id_cliente: int) -> int:
        """Actualiza los datos de un cliente activo conservando su identificador."""
        self.cursor.execute(
            """
            UPDATE clientes
            SET rut = ?, nombre = ?, telefono_movil = ?, pasaporte = ?, correo = ?
            WHERE id_cliente = ? AND activo = 1
            """,
            (cliente.rut, cliente.nombre, cliente.telefono_movil, cliente.pasaporte, cliente.correo, id_cliente),
        )  # Exige activo=1 para que una baja lógica no se revierta por accidente.
        self.conexion.commit()
        return self.cursor.rowcount

    def eliminar_cliente(self, id_cliente: int) -> int:
        """Realiza baja lógica: conserva el registro y lo oculta de los listados."""
        self.cursor.execute("UPDATE clientes SET activo = 0 WHERE id_cliente = ? AND activo = 1", (id_cliente,))
        self.conexion.commit()
        return self.cursor.rowcount  # Indica si el cliente pasó de activo a inactivo.
