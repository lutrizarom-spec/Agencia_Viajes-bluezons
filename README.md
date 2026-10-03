# Agencia de Viajes - Sistema de Gestión (POO & DAO)

Aplicación de consola para registrar y administrar paquetes turísticos. El proyecto organiza las responsabilidades siguiendo un enfoque MVC/DAO: los modelos representan el dominio, `main.py` gestiona el menú y la interacción con el usuario, y la capa DAO concentra el acceso a SQLite. La base de datos local se guarda en `agencia.db`.

## Arquitectura

- **Modelo:** `Paquete_Turistico` contiene los datos comunes y los modelos `Paquete_Nacional`, `Paquete_Internacional` y `Paquete_Crucero` especializan su comportamiento.
- **Vista y flujo de aplicación:** `main.py` presenta el menú de consola, valida entradas y coordina las operaciones.
- **Persistencia:** `Dao` comparte la conexión y el cursor; `PaqueteDao` implementa las operaciones de almacenamiento para paquetes.
- **Base de datos:** `conectar.py` abre la base SQLite y activa `PRAGMA foreign_keys = ON` en la conexión.

## Características principales

- Crear la tabla de paquetes si todavía no existe.
- Registrar paquetes nacionales, internacionales y cruceros.
- Consultar el listado con detalles y precio calculado según el tipo de paquete.
- Eliminar paquetes por código.
- Encapsular atributos del dominio mediante propiedades y atributos privados.
- Validar entradas numéricas del menú y del formulario de registro.
- Usar consultas SQL parametrizadas para insertar y eliminar registros.

## Bitácora de Avances

### 2026-10-03 — Configuración del proyecto y estructura base

Se preparó la estructura de carpetas `model/` y `dao/`, junto con los scripts de conexión y entrada de la aplicación. Se configuró SQLite como persistencia local y se definieron las responsabilidades iniciales de cada componente.

### 2026-10-03 — Modelos de dominio, encapsulamiento y polimorfismo

Se implementó `Paquete_Turistico` con los datos comunes y las clases `Paquete_Nacional`, `Paquete_Internacional` y `Paquete_Crucero`. Los atributos privados y propiedades encapsulan el estado; cada subtipo redefine `calcular_precio()` para aplicar su comportamiento específico.

### 2026-10-03 — Persistencia con Dao y PaqueteDao

Se creó `Dao` como clase base para compartir la conexión y el cursor, y `PaqueteDao` para crear la tabla, insertar, consultar y eliminar paquetes. La conexión SQLite ejecuta `PRAGMA foreign_keys = ON` para habilitar la comprobación de claves foráneas.

### 2026-10-03 — Menú interactivo en main.py

Se incorporó un menú de consola para preparar la tabla, registrar paquetes, listar detalles y precios, eliminar por código y salir. También se añadieron validaciones para las entradas numéricas y la selección del tipo de paquete.

### 2026-10-03 — Documentación línea por línea de los scripts

Se añadieron comentarios explicativos a las instrucciones de los scripts de conexión, modelos, DAO y menú principal para facilitar el seguimiento de su propósito y funcionamiento.
