"""Crea usuarios y paquetes de demostración para evaluar el frontend y la API.

Uso:
    python scripts/seed_demo.py

Requiere AGENCIA_JWT_SECRET (igual que la API). Opcionales: AGENCIA_DB_PATH,
AGENCIA_DEMO_CLIENT_PASSWORD, AGENCIA_DEMO_ADMIN_PASSWORD y
AGENCIA_DEMO_TRAVEL_DATE. Si no se entregan claves, se generan al azar y se
imprimen una sola vez para que el evaluador pueda iniciar sesión.
"""

from __future__ import annotations

import os
import secrets
from contextlib import closing
from datetime import date, timedelta

from conectar import crear_conexion
from dao.paquete_dao import PaqueteDao
from model.paquete_crucero import Paquete_Crucero
from model.paquete_internacional import Paquete_Internacional
from model.paquete_nacional import Paquete_Nacional
from model.paquete_turistico import Paquete_Turistico
from services.auth_service import AuthService, UserAlreadyExistsError, UserRole
from services.compra_service import (
    CapacityBelowReservedError,
    CompraService,
    PackageNotFoundError,
)
from services.fx_service import FxService, FxServiceError, resilient_usd_clp_provider

DEMO_CLIENT_RUT = "11.111.111-1"  # RUT de ejemplo válido para el rol cliente.
DEMO_ADMIN_RUT = "22.222.222-2"  # RUT de ejemplo válido para el rol administrador.
FALLBACK_RATE = 950.0  # Tasa de respaldo si no hay proveedor ni caché al sembrar.

PACKAGES: list[tuple[Paquete_Turistico, int]] = [
    (Paquete_Nacional(101, "Chiloé, Isla Grande del Sur de Chile", 4, 189000.0), 20),
    (Paquete_Internacional(102, "Italia Clásica: Roma, Florencia y Venecia", 10, 1500.0, True), 10),
    (Paquete_Crucero(103, "Crucero por el Sudeste Asiático", 12, 2200.0, 180000.0), 8),
]


def _resolve_database_path() -> str:
    """Usa la misma configuración que la API para no sembrar otra base."""
    return os.environ.get("AGENCIA_DB_PATH", "agencia.db")


def _resolve_password(variable: str) -> str:
    """Toma la clave del entorno o genera una aleatoria de longitud suficiente."""
    return os.environ.get(variable) or secrets.token_urlsafe(12)


def _resolve_travel_date() -> date:
    """Acepta una fecha ISO o usa una salida a sesenta días por defecto."""
    value = os.environ.get("AGENCIA_DEMO_TRAVEL_DATE")
    if value:
        return date.fromisoformat(value)
    return date.today() + timedelta(days=60)


def _seed_users(auth: AuthService) -> list[str]:
    """Aprovisiona cliente y administrador sin sobrescribir cuentas existentes."""
    results: list[str] = []
    accounts = (
        (DEMO_CLIENT_RUT, _resolve_password("AGENCIA_DEMO_CLIENT_PASSWORD"), UserRole.CLIENTE),
        (DEMO_ADMIN_RUT, _resolve_password("AGENCIA_DEMO_ADMIN_PASSWORD"), UserRole.ADMINISTRADOR),
    )
    for rut, password, role in accounts:
        try:
            identity = auth.create_user(rut, password, role)
            results.append(f"Usuario {identity.rut} ({identity.role.value}) creado. Clave: {password}")
        except UserAlreadyExistsError:
            results.append(f"Usuario {rut} ya existe; se conserva su clave actual.")
    return results


def _seed_packages(database_path: str, travel_date: date) -> list[str]:
    """Inserta paquetes de ejemplo y configura sus cupos para la fecha indicada."""
    results: list[str] = []
    with closing(crear_conexion(database_path)) as connection:
        dao = PaqueteDao(connection)
        dao.crear_tabla()
        for package, _ in PACKAGES:
            if dao.obtener_paquete(package.codigo, incluir_inactivo=True) is None:
                dao.insertar_paquete(package)
                results.append(f"Paquete {package.codigo} '{package.nombre}' creado.")
            else:
                results.append(f"Paquete {package.codigo} ya existe; se conserva.")

    compra = CompraService(database_path)  # La capacidad no necesita cotización FX.
    for package, capacity in PACKAGES:
        try:
            available = compra.configure_capacity(package.codigo, travel_date, capacity)
            results.append(f"Cupos del paquete {package.codigo} para {travel_date.isoformat()}: {available}.")
        except (PackageNotFoundError, CapacityBelowReservedError) as error:
            results.append(f"No se configuraron cupos del paquete {package.codigo}: {error}")
    return results


def _seed_exchange_rate(database_path: str) -> str:
    """Guarda una tasa USD/CLP desde el proveedor o una de respaldo si no hay red."""
    try:
        rate = FxService(database_path, resilient_usd_clp_provider).get_usd_clp_rate()
        return f"Tasa USD/CLP obtenida del proveedor: {rate}."
    except FxServiceError:
        rate = FxService(database_path, lambda: FALLBACK_RATE).get_usd_clp_rate()
        return f"Sin proveedor disponible; se sembró una tasa de demostración: {rate}."


def main() -> int:
    """Ejecuta el aprovisionamiento de demostración y resume los resultados."""
    database_path = _resolve_database_path()
    jwt_secret = os.environ.get("AGENCIA_JWT_SECRET")
    if not jwt_secret:  # El aprovisionamiento no emite tokens: basta un secreto efímero.
        jwt_secret = secrets.token_urlsafe(48)
        print("Nota: AGENCIA_JWT_SECRET no estaba definido; se usa uno efímero solo para aprovisionar (no afecta el login).")

    travel_date = _resolve_travel_date()
    auth = AuthService(database_path, jwt_secret)
    print(f"Base de datos: {database_path}")
    for line in _seed_users(auth):
        print(f"  {line}")
    for line in _seed_packages(database_path, travel_date):
        print(f"  {line}")
    print(f"  {_seed_exchange_rate(database_path)}")
    print(f"\nListo. Usa la fecha de viaje {travel_date.isoformat()} al reservar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
