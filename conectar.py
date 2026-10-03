import sqlite3  # Proporciona el conector SQLite incluido en Python para abrir la base de datos local.


def crear_conexion():  # Centraliza la apertura y configuración de la conexión usada por la aplicación.
    conexion = sqlite3.connect("agencia.db")  # Abre o crea el archivo de base de datos en la raíz del proyecto.
    conexion.execute("PRAGMA foreign_keys = ON")  # Activa la aplicación de claves foráneas en esta conexión SQLite.
    return conexion  # Devuelve la conexión configurada para que la capa DAO la reutilice.
