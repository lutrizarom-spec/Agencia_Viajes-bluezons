"""Demostración automática del modelo de clases de la agencia."""

from datetime import date

from model.anticipo_insuficiente_error import AnticipoInsuficienteError
from model.cliente import Cliente
from model.pago import Pago
from model.paquete_crucero import Paquete_Crucero
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.paquete_turistico import Paquete_Turistico
from model.pasaporte_requerido_error import PasaporteRequeridoError
from model.reserva import Reserva


def formatear_monto(monto: float) -> str:
    """Formatea importes con punto para miles y coma decimal."""
    return f"{monto:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def main() -> None:
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
        cliente.telefono_movil = 123
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
        print(
            f"  Detalle {detalle.id_detalle}: {detalle.cantidad} x "
            f"{detalle.paquete.nombre} = "
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
        f"Pago procesado: {pago.id_pago} | "
        f"${formatear_monto(pago.monto)} CLP | "
        f"estado: {pago.estado}"
    )
    print(
        f"Boleta compuesta: N° {pago.boleta.num_boleta} | "
        f"${formatear_monto(pago.boleta.monto)} CLP | "
        f"emitida: {'sí' if pago.boleta.emitida else 'no'}"
    )


if __name__ == "__main__":
    main()
