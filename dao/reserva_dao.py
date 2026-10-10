"""Persistencia SQLite de reservas del modelo con paquetes y servicios."""

from __future__ import annotations

from datetime import date

from dao.dao import Dao
from dao.paquete_dao import PaqueteDao
from model.cliente import Cliente
from model.detalle_reserva import Detalle_Reserva
from model.excursion import Excursion
from model.hotel_estancia import Hotel_Estancia
from model.linea_vuelo import Linea_Vuelo
from model.paquete_crucero import Paquete_Crucero
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.paquete_turistico import Paquete_Turistico
from model.reserva import Reserva
from model.seguro import Seguro
from model.servicio_turistico import Servicio_Turistico

_TIPOS_SERVICIO = {
    Hotel_Estancia: "hotel",
    Linea_Vuelo: "vuelo",
    Excursion: "excursion",
    Seguro: "seguro",
}


class ReservaDao(Dao):
    """Guarda y recupera reservas del modelo junto con sus servicios."""

    def crear_tabla(self) -> None:
        """Crea las tablas propias sin tocar las de la API (reservas/payments)."""
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS servicios (
                id_servicio INTEGER PRIMARY KEY,
                tipo TEXT NOT NULL,
                nombre TEXT NOT NULL,
                destino TEXT NOT NULL,
                zona TEXT NOT NULL,
                precio_base REAL NOT NULL,
                noches INTEGER,
                aerolinea TEXT,
                duracion_horas INTEGER,
                cobertura TEXT
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS reservas_modelo (
                numero_reserva TEXT PRIMARY KEY,
                fecha_reserva TEXT NOT NULL,
                fecha_viaje TEXT NOT NULL,
                estado TEXT NOT NULL,
                tasa_cambio_aplicada REAL NOT NULL,
                cliente_id INTEGER NOT NULL,
                cliente_rut TEXT NOT NULL,
                cliente_nombre TEXT NOT NULL,
                cliente_telefono TEXT NOT NULL,
                cliente_pasaporte TEXT,
                cliente_correo TEXT NOT NULL,
                paquete_codigo INTEGER NOT NULL,
                paquete_nombre TEXT NOT NULL,
                paquete_duracion INTEGER NOT NULL,
                paquete_precio_base REAL NOT NULL,
                paquete_tipo TEXT NOT NULL,
                paquete_pasaporte INTEGER,
                paquete_impuesto REAL
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS detalle_reserva (
                id_detalle INTEGER NOT NULL,
                numero_reserva TEXT NOT NULL,
                orden INTEGER NOT NULL,
                tipo_item TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                subtotal REAL NOT NULL,
                paquete_codigo INTEGER,
                id_servicio INTEGER,
                PRIMARY KEY (numero_reserva, id_detalle),
                FOREIGN KEY (numero_reserva) REFERENCES reservas_modelo (numero_reserva)
                    ON DELETE CASCADE,
                FOREIGN KEY (id_servicio) REFERENCES servicios (id_servicio)
            )
            """
        )
        self.conexion.commit()

    def guardar_servicio(self, servicio: Servicio_Turistico) -> None:
        """Inserta o actualiza un servicio de forma independiente."""
        self._upsert_servicio(servicio)
        self.conexion.commit()

    def guardar_reserva(self, reserva: Reserva) -> None:
        """Persiste la cabecera, los servicios y todas las líneas de la reserva."""
        self._upsert_reserva(reserva)
        self.cursor.execute(
            "DELETE FROM detalle_reserva WHERE numero_reserva = ?",
            (reserva.numero_reserva,),
        )
        for orden, detalle in enumerate(reserva.detalles, start=1):
            referencia = detalle.referencia
            if isinstance(referencia, Servicio_Turistico):
                self._upsert_servicio(referencia)
                tipo_item = "servicio"
                paquete_codigo = None
                id_servicio = referencia.id_servicio
            else:
                tipo_item = "paquete"
                paquete_codigo = referencia.codigo
                id_servicio = None
            self.cursor.execute(
                """
                INSERT INTO detalle_reserva (
                    id_detalle, numero_reserva, orden, tipo_item,
                    cantidad, subtotal, paquete_codigo, id_servicio
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detalle.id_detalle,
                    reserva.numero_reserva,
                    orden,
                    tipo_item,
                    detalle.cantidad,
                    detalle.subtotal,
                    paquete_codigo,
                    id_servicio,
                ),
            )
        self.conexion.commit()

    def obtener_servicio(self, id_servicio: int) -> Servicio_Turistico | None:
        """Recupera un servicio concreto reconstruyendo su subtipo."""
        row = self.cursor.execute(
            """
            SELECT id_servicio, tipo, nombre, destino, zona, precio_base,
                   noches, aerolinea, duracion_horas, cobertura
            FROM servicios WHERE id_servicio = ?
            """,
            (id_servicio,),
        ).fetchone()
        return None if row is None else self._servicio_from_row(row)

    def obtener_servicios_de_reserva(
        self, numero_reserva: str
    ) -> list[Servicio_Turistico]:
        """Recupera los servicios asociados a una misma reserva."""
        rows = self.cursor.execute(
            """
            SELECT s.id_servicio, s.tipo, s.nombre, s.destino, s.zona,
                   s.precio_base, s.noches, s.aerolinea, s.duracion_horas, s.cobertura
            FROM detalle_reserva AS d
            JOIN servicios AS s ON s.id_servicio = d.id_servicio
            WHERE d.numero_reserva = ? AND d.tipo_item = 'servicio'
            ORDER BY d.orden
            """,
            (numero_reserva,),
        ).fetchall()
        return [self._servicio_from_row(row) for row in rows]

    def obtener_reserva(self, numero_reserva: str) -> Reserva | None:
        """Reconstruye una reserva completa con su paquete y sus servicios."""
        row = self.cursor.execute(
            """
            SELECT numero_reserva, fecha_reserva, fecha_viaje, estado,
                   tasa_cambio_aplicada,
                   cliente_id, cliente_rut, cliente_nombre, cliente_telefono,
                   cliente_pasaporte, cliente_correo,
                   paquete_codigo, paquete_nombre, paquete_duracion,
                   paquete_precio_base, paquete_tipo, paquete_pasaporte,
                   paquete_impuesto
            FROM reservas_modelo WHERE numero_reserva = ?
            """,
            (numero_reserva,),
        ).fetchone()
        if row is None:
            return None

        (
            numero,
            fecha_reserva,
            fecha_viaje,
            estado,
            tasa,
            cliente_id,
            cliente_rut,
            cliente_nombre,
            cliente_telefono,
            cliente_pasaporte,
            cliente_correo,
            paquete_codigo,
            paquete_nombre,
            paquete_duracion,
            paquete_precio_base,
            paquete_tipo,
            paquete_pasaporte,
            paquete_impuesto,
        ) = row

        cliente = Cliente(
            int(cliente_id),
            cliente_rut,
            cliente_nombre,
            cliente_telefono,
            cliente_pasaporte or "",
            cliente_correo,
        )
        paquete = self._paquete_from_values(
            int(paquete_codigo),
            paquete_nombre,
            int(paquete_duracion),
            float(paquete_precio_base),
            paquete_tipo,
            paquete_pasaporte,
            paquete_impuesto,
        )
        reserva = Reserva(
            numero,
            date.fromisoformat(fecha_reserva),
            date.fromisoformat(fecha_viaje),
            cliente,
            paquete,
            float(tasa),
        )

        detalle_rows = self.cursor.execute(
            """
            SELECT id_detalle, tipo_item, cantidad, id_servicio
            FROM detalle_reserva
            WHERE numero_reserva = ?
            ORDER BY orden
            """,
            (numero,),
        ).fetchall()
        detalles: list[Detalle_Reserva] = []
        for id_detalle, tipo_item, cantidad, id_servicio in detalle_rows:
            if tipo_item == "servicio":
                referencia = self.obtener_servicio(int(id_servicio))
                if referencia is None:
                    continue
            else:
                referencia = paquete
            detalles.append(
                Detalle_Reserva(int(id_detalle), referencia, int(cantidad), float(tasa))
            )
        reserva._restaurar_desde_persistencia(detalles, estado)
        return reserva

    def _upsert_servicio(self, servicio: Servicio_Turistico) -> None:
        tipo = _TIPOS_SERVICIO.get(type(servicio), "generico")
        self.cursor.execute(
            """
            INSERT INTO servicios (
                id_servicio, tipo, nombre, destino, zona, precio_base,
                noches, aerolinea, duracion_horas, cobertura
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id_servicio) DO UPDATE SET
                tipo = excluded.tipo,
                nombre = excluded.nombre,
                destino = excluded.destino,
                zona = excluded.zona,
                precio_base = excluded.precio_base,
                noches = excluded.noches,
                aerolinea = excluded.aerolinea,
                duracion_horas = excluded.duracion_horas,
                cobertura = excluded.cobertura
            """,
            (
                servicio.id_servicio,
                tipo,
                servicio.nombre,
                servicio.destino,
                servicio.zona,
                servicio.precio_base,
                getattr(servicio, "noches", None),
                getattr(servicio, "aerolinea", None),
                getattr(servicio, "duracion_horas", None),
                getattr(servicio, "cobertura", None),
            ),
        )

    def _upsert_reserva(self, reserva: Reserva) -> None:
        cliente = reserva.cliente
        paquete = reserva.paquete
        tipo, pasaporte, impuesto = PaqueteDao._specific_fields(paquete)
        self.cursor.execute(
            """
            INSERT INTO reservas_modelo (
                numero_reserva, fecha_reserva, fecha_viaje, estado,
                tasa_cambio_aplicada,
                cliente_id, cliente_rut, cliente_nombre, cliente_telefono,
                cliente_pasaporte, cliente_correo,
                paquete_codigo, paquete_nombre, paquete_duracion,
                paquete_precio_base, paquete_tipo, paquete_pasaporte,
                paquete_impuesto
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (numero_reserva) DO UPDATE SET
                fecha_reserva = excluded.fecha_reserva,
                fecha_viaje = excluded.fecha_viaje,
                estado = excluded.estado,
                tasa_cambio_aplicada = excluded.tasa_cambio_aplicada,
                cliente_id = excluded.cliente_id,
                cliente_rut = excluded.cliente_rut,
                cliente_nombre = excluded.cliente_nombre,
                cliente_telefono = excluded.cliente_telefono,
                cliente_pasaporte = excluded.cliente_pasaporte,
                cliente_correo = excluded.cliente_correo,
                paquete_codigo = excluded.paquete_codigo,
                paquete_nombre = excluded.paquete_nombre,
                paquete_duracion = excluded.paquete_duracion,
                paquete_precio_base = excluded.paquete_precio_base,
                paquete_tipo = excluded.paquete_tipo,
                paquete_pasaporte = excluded.paquete_pasaporte,
                paquete_impuesto = excluded.paquete_impuesto
            """,
            (
                reserva.numero_reserva,
                reserva.fecha_reserva.isoformat(),
                reserva.fecha_viaje.isoformat(),
                reserva.estado,
                reserva.tasa_cambio_aplicada,
                cliente.id_cliente,
                cliente.rut,
                cliente.nombre,
                cliente.telefono_movil,
                cliente.pasaporte,
                cliente.correo,
                paquete.codigo,
                paquete.nombre,
                paquete.duracion,
                paquete.precio_base,
                tipo,
                pasaporte,
                impuesto,
            ),
        )

    @staticmethod
    def _servicio_from_row(row: tuple) -> Servicio_Turistico:
        (
            id_servicio,
            tipo,
            nombre,
            destino,
            zona,
            precio_base,
            noches,
            aerolinea,
            duracion_horas,
            cobertura,
        ) = row
        if tipo == "hotel":
            return Hotel_Estancia(
                int(id_servicio), nombre, destino, zona, float(precio_base), int(noches)
            )
        if tipo == "vuelo":
            return Linea_Vuelo(
                int(id_servicio), nombre, destino, zona, float(precio_base), aerolinea
            )
        if tipo == "excursion":
            return Excursion(
                int(id_servicio),
                nombre,
                destino,
                zona,
                float(precio_base),
                int(duracion_horas),
            )
        if tipo == "seguro":
            return Seguro(
                int(id_servicio), nombre, destino, zona, float(precio_base), cobertura
            )
        return Servicio_Turistico(int(id_servicio), nombre, destino, zona, float(precio_base))

    @staticmethod
    def _paquete_from_values(
        codigo: int,
        nombre: str,
        duracion: int,
        precio_base: float,
        tipo: str,
        pasaporte: object,
        impuesto: object,
    ) -> Paquete_Turistico:
        if tipo == "internacional":
            return Paquete_Internacional(
                codigo, nombre, duracion, precio_base, bool(pasaporte)
            )
        if tipo == "crucero":
            return Paquete_Crucero(codigo, nombre, duracion, precio_base, impuesto)
        if tipo == "nacional":
            return Paquete_Nacional(codigo, nombre, duracion, precio_base)
        return Paquete_Turistico(codigo, nombre, duracion, precio_base)
