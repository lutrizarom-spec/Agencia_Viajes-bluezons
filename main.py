"""Demostración automática de los modelos y las operaciones de la agencia."""

import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

from dao.paquete_dao import PaqueteDao
from model.paquete_crucero import Paquete_Crucero
from model.reserva import Reserva
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.paquete_turistico import Paquete_Turistico
from services.compra_service import (
    CompraService,
    InsufficientCapacityError,
    InventoryNotConfiguredError,
)


def main() -> None:
    """Ejecuta todos los ejemplos de la actividad sin solicitar entrada."""
    paquetes: list[Paquete_Turistico] = [
        Paquete_Nacional(101, "Valle del Elqui", 4, 180000.0),
        Paquete_Internacional(102, "Buenos Aires", 5, 250.0, True),
        Paquete_Crucero(103, "Fiordos del Sur", 7, 400.0, 25000.0),
    ]

    print("=== Paquetes y cálculo polimórfico ===")
    for paquete in paquetes:
        print(paquete.obtener_detalle())
        print(f"Precio calculado: ${paquete.calcular_precio():.2f}")

    print("\n=== Validación del setter ===")
    paquete_internacional = paquetes[1]
    try:
        paquete_internacional.pasaporte_valido = 1
    except ValueError as error:
        print(f"Error controlado al validar el pasaporte: {error}")

    fecha_viaje = date(2026, 12, 15)
    rut_cliente = "10.000.013-K"
    reserva_modelo = Reserva(rut_cliente, paquetes[0], 2)
    print("\n=== Composición y agregación: reserva ===")
    print(f"Cliente: {reserva_modelo.rut_cliente}")
    print(f"Paquete agregado: {reserva_modelo.paquete.nombre}")
    for detalle in reserva_modelo.detalles:
        print(
            f"Detalle compuesto: {detalle.cantidad} x {detalle.paquete.nombre} "
            f"= ${detalle.subtotal:.2f}"
        )
    print(f"Total de la reserva: ${reserva_modelo.total:.2f}")

    with tempfile.TemporaryDirectory(prefix="agencia-viajes-demo-") as directorio:
        database_path = Path(directorio) / "agencia-demo.db"
        with closing(sqlite3.connect(database_path)) as conexion:
            paquete_dao = PaqueteDao(conexion)
            paquete_dao.crear_tabla()
            for paquete in paquetes:
                paquete_dao.insertar_paquete(paquete)

        servicio_compra = CompraService(database_path)
        print("\n=== Excepciones propias del negocio ===")
        try:
            servicio_compra.purchase(
                rut_cliente,
                101,
                1,
                travel_date=fecha_viaje,
            )
        except InventoryNotConfiguredError as error:
            print(f"Error controlado por inventario no configurado: {error}")

        servicio_compra.configure_capacity(101, fecha_viaje, 3)
        recibo = servicio_compra.purchase(
            rut_cliente,
            101,
            1,
            travel_date=fecha_viaje,
        )
        pago = servicio_compra.get_payment(
            recibo.reservation_id,
            rut_cliente,
            payment_id=recibo.payment_id,
        )

        print("\n=== Transacción: reserva y detalle de pago ===")
        print(
            f"Reserva {recibo.reservation_id}: paquete {recibo.package_code}, "
            f"cantidad {recibo.quantity}, total ${recibo.total_price:.2f}"
        )
        print(
            f"Pago {pago.payment_id} asociado a reserva {pago.reservation_id}: "
            f"${pago.amount:.2f} | estado: {pago.status}"
        )

        try:
            servicio_compra.purchase(
                rut_cliente,
                101,
                99,
                travel_date=fecha_viaje,
            )
        except InsufficientCapacityError as error:
            print(f"Error controlado por falta de cupos: {error}")


if __name__ == "__main__":
    main()
