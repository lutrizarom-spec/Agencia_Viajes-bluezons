"""Menú interactivo de consola para gestionar paquetes turísticos."""

from __future__ import annotations

import math
import sqlite3
from datetime import date

from conectar import crear_conexion
from dao.paquete_dao import PaqueteDao
from model.anticipo_insuficiente_error import AnticipoInsuficienteError
from model.cliente import Cliente
from model.pago import Pago
from model.paquete_crucero import Paquete_Crucero
from model.paquete_factory import paquete_desde_columnas
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.paquete_turistico import Paquete_Turistico
from model.pasaporte_requerido_error import PasaporteRequeridoError
from model.reserva import Reserva

TEXTO_MENU = """
=== Agencia de Viajes: menú principal ===
  1) Crear paquete
  2) Listar paquetes
  3) Modificar paquete
  4) Eliminar paquete (baja lógica)
  5) Ver demostración del modelo
  6) Salir
"""


class SalirMenu(Exception):
    """Señala que el usuario pidió salir o cerró la entrada de consola."""


def formatear_monto(monto: float) -> str:
    """Formatea importes con punto para miles y coma decimal."""
    return f"{monto:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _solicitar(leer, mostrar, mensaje: str) -> str:
    """Muestra un mensaje, lee una línea y convierte EOF/Ctrl-C en salida limpia."""
    mostrar(mensaje)
    try:
        return leer()
    except (EOFError, KeyboardInterrupt, StopIteration) as error:
        raise SalirMenu() from error


def leer_texto(leer, mostrar, mensaje: str, etiqueta: str) -> str:
    """Pide un texto no vacío y repite mientras la entrada no sea válida."""
    while True:
        crudo = _solicitar(leer, mostrar, mensaje).strip()
        if crudo:
            return crudo
        mostrar(f"  Entrada inválida: {etiqueta} no puede estar vacío/a.")


def leer_entero(leer, mostrar, mensaje: str, etiqueta: str, minimo: int = 1) -> int:
    """Pide un entero dentro de un mínimo y repite si es inválido."""
    while True:
        crudo = _solicitar(leer, mostrar, mensaje).strip()
        try:
            valor = int(crudo)
        except ValueError:
            mostrar(f"  Entrada inválida: {etiqueta} debe ser un número entero.")
            continue
        if valor < minimo:
            mostrar(f"  Entrada inválida: {etiqueta} debe ser mayor o igual a {minimo}.")
            continue
        return valor


def leer_flotante(
    leer,
    mostrar,
    mensaje: str,
    etiqueta: str,
    *,
    permitir_cero: bool = False,
) -> float:
    """Pide un número finito válido y repite si es inválido."""
    while True:
        crudo = _solicitar(leer, mostrar, mensaje).strip().replace(",", ".")
        try:
            valor = float(crudo)
        except ValueError:
            mostrar(f"  Entrada inválida: {etiqueta} debe ser un número.")
            continue
        if not math.isfinite(valor) or valor < 0 or (valor == 0 and not permitir_cero):
            limite = "mayor o igual a 0" if permitir_cero else "mayor que cero"
            mostrar(f"  Entrada inválida: {etiqueta} debe ser finito y {limite}.")
            continue
        return valor


def leer_si_no(leer, mostrar, mensaje: str, etiqueta: str) -> bool:
    """Pide una confirmación s/n y repite mientras la respuesta no sea válida."""
    afirmativos = {"s", "si", "sí", "y", "yes"}
    negativos = {"n", "no"}
    while True:
        crudo = _solicitar(leer, mostrar, mensaje).strip().casefold()
        if crudo in afirmativos:
            return True
        if crudo in negativos:
            return False
        mostrar(f"  Entrada inválida: {etiqueta} debe responderse con 's' o 'n'.")


def leer_tipo(leer, mostrar) -> str:
    """Pide el subtipo de paquete y retorna su clave interna."""
    mostrar("  Tipos disponibles: 1) Nacional  2) Internacional  3) Crucero")
    opciones = {"1": "nacional", "2": "internacional", "3": "crucero"}
    while True:
        crudo = _solicitar(leer, mostrar, "Tipo [1-3]: ").strip()
        if crudo in opciones:
            return opciones[crudo]
        mostrar("  Entrada inválida: elija 1, 2 o 3.")


def construir_paquete(fila: tuple) -> Paquete_Turistico:
    """Reconstruye el subtipo correcto a partir de una fila del catálogo."""
    return paquete_desde_columnas(*fila[:7])  # Fuente única del mapeo tipo→modelo.


def _pedir_campos_comunes(leer, mostrar) -> tuple[str, int, float]:
    """Solicita nombre, duración y precio base válidos."""
    nombre = leer_texto(leer, mostrar, "Nombre del paquete: ", "el nombre")
    duracion = leer_entero(leer, mostrar, "Duración (días): ", "la duración")
    precio_base = leer_flotante(
        leer, mostrar, "Precio base: ", "el precio base"
    )
    return nombre, duracion, precio_base


def _crear_paquete_desde_tipo(leer, mostrar, codigo: int, tipo: str) -> Paquete_Turistico:
    """Construye un paquete del subtipo indicado pidiendo sus campos propios."""
    nombre, duracion, precio_base = _pedir_campos_comunes(leer, mostrar)
    if tipo == "internacional":
        pasaporte = leer_si_no(
            leer,
            mostrar,
            "¿El paquete requiere pasaporte válido? [s/n]: ",
            "el pasaporte",
        )
        return Paquete_Internacional(codigo, nombre, duracion, precio_base, pasaporte)
    if tipo == "crucero":
        impuesto = leer_flotante(
            leer,
            mostrar,
            "Impuesto portuario: ",
            "el impuesto portuario",
            permitir_cero=True,
        )
        return Paquete_Crucero(codigo, nombre, duracion, precio_base, impuesto)
    return Paquete_Nacional(codigo, nombre, duracion, precio_base)


def crear_paquete(dao: PaqueteDao, leer, mostrar) -> None:
    """Crea un paquete del subtipo elegido y lo persiste."""
    tipo = leer_tipo(leer, mostrar)
    codigo = leer_entero(leer, mostrar, "Código del paquete: ", "el código")
    paquete = _crear_paquete_desde_tipo(leer, mostrar, codigo, tipo)
    try:
        dao.insertar_paquete(paquete)
    except sqlite3.IntegrityError:
        mostrar("  No se pudo crear: ya existe un paquete con ese código.")
        return
    mostrar(f"  Paquete {codigo} creado correctamente.")


def listar_paquetes(dao: PaqueteDao, mostrar) -> None:
    """Lista los paquetes activos con su precio calculado."""
    filas = dao.obtener_paquetes()
    if not filas:
        mostrar("  No hay paquetes registrados.")
        return
    mostrar("  Paquetes registrados:")
    for fila in filas:
        paquete = construir_paquete(fila)
        mostrar(
            f"  [{paquete.codigo}] {paquete.nombre} | {paquete.duracion} días | "
            f"{type(paquete).__name__} | "
            f"${formatear_monto(paquete.calcular_precio())} CLP"
        )


def modificar_paquete(dao: PaqueteDao, leer, mostrar) -> None:
    """Edita un paquete activo conservando su código y su subtipo."""
    codigo = leer_entero(leer, mostrar, "Código a modificar: ", "el código")
    fila = dao.obtener_paquete(codigo)
    if fila is None:
        mostrar("  No existe un paquete activo con ese código.")
        return
    actual = construir_paquete(fila)
    mostrar(
        f"  Actual: {actual.nombre} | {actual.duracion} días | "
        f"${formatear_monto(actual.precio_base)} base"
    )
    if isinstance(actual, Paquete_Internacional):
        nuevo = _crear_paquete_desde_tipo(leer, mostrar, codigo, "internacional")
    elif isinstance(actual, Paquete_Crucero):
        nuevo = _crear_paquete_desde_tipo(leer, mostrar, codigo, "crucero")
    else:
        nuevo = _crear_paquete_desde_tipo(leer, mostrar, codigo, "nacional")
    dao.actualizar_paquete(nuevo, codigo)
    mostrar("  Paquete actualizado correctamente.")


def eliminar_paquete(dao: PaqueteDao, leer, mostrar) -> None:
    """Da de baja lógica un paquete activo."""
    codigo = leer_entero(leer, mostrar, "Código a eliminar: ", "el código")
    eliminados = dao.eliminar_paquete(codigo)
    if eliminados == 0:
        mostrar("  No existe un paquete activo con ese código.")
        return
    mostrar("  Paquete dado de baja correctamente.")


def demostracion_modelo() -> None:
    """Muestra herencia, polimorfismo y reglas del dominio sin usar base de datos."""
    paquetes: list[Paquete_Turistico] = [
        Paquete_Nacional(101, "Valle del Elqui", 4, 180000.0),
        Paquete_Internacional(102, "Buenos Aires", 5, 250.0, True),
        Paquete_Crucero(103, "Fiordos del Sur", 7, 400.0, 25000.0),
    ]

    print("=== Paquetes: herencia y cálculo polimórfico ===")
    reglas_precio = {
        Paquete_Nacional: ("CLP", "precio base sin conversión"),
        Paquete_Internacional: ("USD", "precio base × 900 CLP/USD"),
        Paquete_Crucero: ("USD", "precio base × 900 CLP/USD"),
    }
    for paquete in paquetes:
        moneda_base, regla = reglas_precio[type(paquete)]
        if isinstance(paquete, Paquete_Crucero):
            regla += (
                f" + impuesto portuario de "
                f"${formatear_monto(paquete.impuesto_puerto)}"
            )
        print(
            f"{type(paquete).__name__}: {paquete.nombre} | "
            f"Precio base: ${formatear_monto(paquete.precio_base)} {moneda_base} | "
            f"Regla aplicada: {regla} | "
            f"Precio calculado: ${formatear_monto(paquete.calcular_precio(900))} CLP"
        )

    cliente = Cliente(
        1,
        "10.000.013-K",
        "Camila Rojas",
        "+56912345678",
        "AB123456",
        "camila@example.com",
    )
    print("\n=== Validación de setter ===")
    try:
        cliente.telefono_movil = 123  # type: ignore[assignment]  # Valor inválido deliberado para demostrar la validación del setter.
    except ValueError as error:
        print(f"Error controlado en Cliente.telefono_movil (heredado de Persona): {error}")

    fecha_reserva = date(2026, 10, 6)
    fecha_viaje = date(2026, 12, 15)
    reserva = Reserva(
        "RES-001",
        fecha_reserva,
        fecha_viaje,
        cliente,
        paquetes[0],
    )
    reserva.agregar_detalle(1)
    reserva.agregar_detalle(2)

    print("\n=== Reserva: agregación y composición ===")
    print(f"Cliente agregado: {reserva.cliente.nombre} ({reserva.cliente.rut})")
    print(f"Paquete agregado: {reserva.paquete.nombre}")
    print("Detalles compuestos:")
    for detalle in reserva.detalles:
        paquete_detalle = detalle.paquete
        assert paquete_detalle is not None  # En la demostración cada detalle referencia un paquete.
        print(
            f"  Detalle {detalle.id_detalle}: {detalle.cantidad} x "
            f"{paquete_detalle.nombre} = "
            f"${formatear_monto(detalle.calcular_subtotal())} CLP"
        )
    print(f"Total de la reserva: ${formatear_monto(reserva.calcular_total())} CLP")

    print("\n=== Regla de pasaporte para paquete internacional ===")
    cliente_sin_pasaporte = Cliente(
        2,
        "12.345.678-5",
        "Diego Soto",
        "+56987654321",
        "",
        "diego@example.com",
    )
    reserva_internacional = Reserva(
        "RES-002",
        fecha_reserva,
        fecha_viaje,
        cliente_sin_pasaporte,
        paquetes[1],
    )
    print(
        "Resultado Cliente.validar_pasaporte(): "
        f"{cliente_sin_pasaporte.validar_pasaporte()}"
    )
    try:
        reserva_internacional.agregar_detalle(1)
    except PasaporteRequeridoError as error:
        print(
            "PasaporteRequeridoError lanzada en "
            f"Reserva.agregar_detalle: {error}"
        )

    print("\n=== Regla de anticipo mínimo del 50 % ===")
    monto_insuficiente = reserva.total * 0.4
    minimo_anticipo = reserva.total * 0.5
    print(
        f"Monto intentado: ${formatear_monto(monto_insuficiente)} CLP | "
        f"Mínimo exigido: ${formatear_monto(minimo_anticipo)} CLP"
    )
    pago_insuficiente = Pago(
        "PAGO-ERROR",
        fecha_reserva,
        monto_insuficiente,
        fecha_viaje,
        reserva,
        9001,
    )
    try:
        pago_insuficiente.procesar_pago()
    except AnticipoInsuficienteError:
        print(
            "AnticipoInsuficienteError lanzada en "
            "Reserva.validar_anticipo: el monto intentado es inferior "
            "al mínimo exigido."
        )

    pago = Pago(
        "PAGO-001",
        fecha_reserva,
        minimo_anticipo,
        fecha_viaje,
        reserva,
        9002,
    )
    pago.procesar_pago()
    print(
        f"Pago confirmado: {pago.id_pago} | "
        f"${formatear_monto(pago.monto)} CLP | "
        f"estado: {pago.estado}"
    )
    print(
        f"Boleta compuesta: N° {pago.boleta.num_boleta} | "
        f"${formatear_monto(pago.boleta.monto)} CLP | "
        f"emitida: {'sí' if pago.boleta.emitida else 'no'}"
    )


def ejecutar_menu(dao: PaqueteDao, leer=None, mostrar=None) -> None:
    """Ejecuta el bucle del menú hasta que el usuario elija salir."""
    if leer is None:
        leer = input
    if mostrar is None:
        mostrar = print
    operaciones = {
        "1": lambda: crear_paquete(dao, leer, mostrar),
        "2": lambda: listar_paquetes(dao, mostrar),
        "3": lambda: modificar_paquete(dao, leer, mostrar),
        "4": lambda: eliminar_paquete(dao, leer, mostrar),
        "5": demostracion_modelo,
    }
    while True:
        mostrar(TEXTO_MENU)
        try:
            opcion = _solicitar(leer, mostrar, "Opción [1-6]: ").strip()
        except SalirMenu:
            mostrar("Saliendo...")
            return
        if opcion == "6":
            mostrar("Saliendo...")
            return
        operacion = operaciones.get(opcion)
        if operacion is None:
            mostrar("  Opción inválida: elija un número del 1 al 6.")
            continue
        try:
            operacion()
        except SalirMenu:
            mostrar("Saliendo...")
            return
        except (ValueError, sqlite3.Error) as error:
            mostrar(f"  Operación cancelada: {error}")


def main(db_path: str | None = None) -> None:
    """Abre la base, prepara el esquema y presenta el menú interactivo."""
    conexion = crear_conexion(db_path)
    try:
        dao = PaqueteDao(conexion)
        dao.crear_tabla()
        ejecutar_menu(dao)
    finally:
        conexion.close()


if __name__ == "__main__":
    main()
