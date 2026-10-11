"""Autenticación local por RUT con bcrypt y tokens JWT."""

from __future__ import annotations

import re  # Normaliza la escritura del RUT y valida su formato chileno.
import secrets  # Genera un hash ficticio para igualar el coste de logins con RUT inexistente.
import sqlite3  # Guarda las cuentas locales en la base de datos de la aplicación.
from contextlib import closing  # Cierra cada conexión aunque una consulta lance una excepción.
from dataclasses import dataclass  # Modela una identidad autenticada inmutable.
from datetime import UTC, datetime, timedelta  # Define expiraciones JWT en UTC.
from enum import StrEnum  # Define roles textuales estables para la API y el token.
from pathlib import Path  # Acepta rutas SQLite portables.

import bcrypt  # Deriva y verifica hashes de contraseña resistentes a fuerza bruta.
import jwt  # Firma y valida tokens de acceso sin persistir sesiones en la base.


class UserRole(StrEnum):
    """Roles admitidos por la aplicación local."""

    CLIENTE = "cliente"  # Rol de una persona que puede consultar y solicitar reservas.
    ADMINISTRADOR = "administrador"  # Rol autorizado a gestionar inventario de paquetes.


class InvalidCredentialsError(ValueError):
    """Señala RUT, contraseña o cuenta inválidos sin revelar cuál falló."""


class InvalidTokenError(ValueError):
    """Señala un JWT inválido, vencido o con claims incompatibles."""


class UserAlreadyExistsError(ValueError):
    """Señala que ya existe una cuenta asociada al RUT normalizado."""


@dataclass(frozen=True)
class UserIdentity:
    """Representa los datos mínimos confiables de una identidad autenticada."""

    rut: str  # Conserva el RUT canónico con guion, utilizado como subject JWT.
    role: UserRole  # Conserva el rol validado contra los roles soportados.


def normalize_rut(rut: str) -> str:
    """Valida el dígito verificador y retorna el RUT en formato cuerpo-DV."""
    if not isinstance(rut, str):  # Evita intentar normalizar objetos que no sean texto.
        raise ValueError("El RUT debe ser texto.")  # Informa al llamador que el tipo de entrada es incorrecto.

    compact = re.sub(r"[.-]", "", rut.strip()).upper()  # Acepta puntos y guion opcionales y normaliza k a K.
    if not re.fullmatch(r"[0-9]{1,8}[0-9K]", compact):  # Exige dígitos ASCII: RUT Unicode visualmente similares no se aceptan.
        raise ValueError("El RUT no tiene un formato válido.")  # Rechaza letras, espacios interiores o longitudes inesperadas.

    body = compact[:-1]  # Separa el cuerpo numérico del carácter de verificación final.
    supplied_digit = compact[-1]  # Conserva el dígito verificador entregado por la persona.
    if int(body) == 0:  # Un cuerpo de solo ceros no identifica un RUT utilizable.
        raise ValueError("El cuerpo del RUT debe ser mayor que cero.")  # Impide registrar la identidad reservada cero.

    total = sum(  # Suma productos de dígitos con la secuencia cíclica oficial 2..7.
        int(digit) * (index % 6 + 2)  # Multiplica cada dígito por su ponderador.
        for index, digit in enumerate(reversed(body))  # Recorre desde las unidades del cuerpo hacia la izquierda.
    )  # Completa el total para calcular el dígito módulo 11.
    remainder = 11 - total % 11  # Convierte el residuo en el dígito verificador esperado.
    expected_digit = "0" if remainder == 11 else "K" if remainder == 10 else str(remainder)  # Representa los casos especiales 0 y K.
    if supplied_digit != expected_digit:  # Comprueba el carácter recibido contra el cálculo oficial.
        raise ValueError("El dígito verificador del RUT no es válido.")  # Evita asociar cuentas a identificadores mal escritos.

    return f"{int(body)}-{expected_digit}"  # Quita ceros iniciales y entrega una sola forma canónica.


class AuthService:
    """Gestiona cuentas locales, hashes bcrypt y JWT de acceso HS256."""

    def __init__(
        self,
        database_path: str | Path,
        jwt_secret: str,
        *,
        token_ttl_minutes: int = 30,
    ) -> None:
        """Valida la clave de firma, prepara bcrypt y crea el esquema de usuarios."""
        if not isinstance(jwt_secret, str) or len(jwt_secret.encode("utf-8")) < 32:  # Requiere una clave de firma no trivial.
            raise ValueError("AGENCIA_JWT_SECRET debe contener al menos 32 bytes.")  # Evita arrancar con una clave débil o vacía.
        if isinstance(token_ttl_minutes, bool) or token_ttl_minutes < 1:  # Valida una duración positiva sin aceptar True como entero.
            raise ValueError("La duración del token debe ser al menos un minuto.")  # Evita JWT que expiren inmediatamente o nunca.

        self._database_path = str(database_path)  # Normaliza la ruta de la base compartida por la aplicación local.
        self._jwt_secret = jwt_secret  # Conserva el secreto solo en memoria para firmar y verificar JWT.
        self._token_ttl = timedelta(minutes=token_ttl_minutes)  # Convierte la duración configurada a un intervalo estándar.
        self._dummy_hash = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt())  # Prepara un hash ficticio para igualar el coste de usuario inexistente.
        self._ensure_schema()  # Crea la tabla de usuarios si todavía no existe.

    def create_user(self, rut: str, password: str, role: UserRole) -> UserIdentity:
        """Aprovisiona localmente una cuenta; no expone registro público por HTTP."""
        normalized_rut = normalize_rut(rut)  # Valida el RUT y lo fija en una forma única antes de almacenarlo.
        normalized_role = UserRole(role)  # Rechaza roles no admitidos y normaliza el rol a enum.
        password_bytes = self._validate_password(password)  # Aplica las reglas de longitud antes del hash.
        password_hash = bcrypt.hashpw(password_bytes, bcrypt.gensalt())  # Deriva una contraseña irreversible con salt aleatoria.

        try:  # Traduce el conflicto de unicidad de SQLite a un error útil para el aprovisionamiento.
            with closing(sqlite3.connect(self._database_path, timeout=10)) as connection:  # Abre una conexión acotada y asegura su cierre.
                with connection:  # Confirma la inserción o revierte la transacción si falla.
                    connection.execute(  # Inserta el RUT canónico, el hash y el rol, nunca la contraseña original.
                        """
                        INSERT INTO users (rut, password_hash, role)
                        VALUES (?, ?, ?)
                        """,
                        (normalized_rut, password_hash.decode("ascii"), normalized_role.value),  # Parametriza valores para que sean datos y no SQL.
                    )  # Persiste la cuenta para permitir futuros inicios de sesión.
        except sqlite3.IntegrityError as error:  # Detecta el RUT duplicado mediante la clave primaria de users.
            raise UserAlreadyExistsError("Ya existe una cuenta para ese RUT.") from error  # No filtra detalles internos de SQLite.

        return UserIdentity(normalized_rut, normalized_role)  # Devuelve la identidad no sensible recién aprovisionada.

    def authenticate(self, rut: str, password: str) -> tuple[str, UserIdentity]:
        """Verifica credenciales y devuelve un JWT junto con la identidad validada."""
        try:  # Convierte errores de formato en el mismo resultado genérico que una clave incorrecta.
            normalized_rut = normalize_rut(rut)  # Evita cuentas duplicadas por puntuación o uso de k minúscula.
        except ValueError as error:  # No filtra si falló el formato ni permite que llegue una excepción de validación cruda.
            self._check_dummy_password(password)  # Realiza trabajo bcrypt también para entradas de RUT inválidas.
            raise InvalidCredentialsError("RUT o contraseña incorrectos.") from error  # Mantiene una respuesta indistinguible para el login.

        with closing(sqlite3.connect(self._database_path, timeout=10)) as connection:  # Abre conexión de lectura y garantiza su liberación.
            row = connection.execute(  # Busca una sola cuenta por su RUT canónico.
                "SELECT password_hash, role FROM users WHERE rut = ?", (normalized_rut,)  # Usa SQL parametrizado para el identificador.
            ).fetchone()  # Obtiene hash y rol, o None si no existe una cuenta.

        password_bytes = password.encode("utf-8") if isinstance(password, str) else b""  # Convierte la clave a bytes sin aceptar tipos inesperados.
        if row is None or len(password_bytes) > 72:  # Bcrypt solo procesa hasta 72 bytes, por lo que rechaza claves más largas.
            self._check_dummy_password(password)  # Igualiza el trabajo criptográfico en rutas de fallo.
            raise InvalidCredentialsError("RUT o contraseña incorrectos.")  # Responde con mensaje genérico para no revelar usuarios existentes.

        password_matches = bcrypt.checkpw(password_bytes, row[0].encode("ascii"))  # Compara la clave provista con el hash almacenado.
        if not password_matches:  # Detiene el flujo antes de emitir un token si la contraseña no coincide.
            raise InvalidCredentialsError("RUT o contraseña incorrectos.")  # No diferencia entre RUT válido y contraseña incorrecta.

        try:  # Valida que el rol persistido todavía sea uno de los valores admitidos.
            role = UserRole(row[1])  # Construye el enum desde el valor guardado en la tabla.
        except ValueError as error:  # Trata corrupción o cambio incompatible de rol como fallo de autenticación.
            raise InvalidCredentialsError("RUT o contraseña incorrectos.") from error  # No emite tokens con permisos ambiguos.

        identity = UserIdentity(normalized_rut, role)  # Crea la identidad confiable que se incluirá en el token.
        return self._create_access_token(identity), identity  # Firma el JWT y devuelve también la identidad para la respuesta.

    def authenticate_token(self, token: str) -> UserIdentity:
        """Verifica firma, algoritmo, expiración y claims del token recibido."""
        try:  # Convierte errores de JWT en una excepción de dominio controlada por la API.
            claims = jwt.decode(  # Verifica la firma y la fecha de expiración con la clave privada del backend.
                token,  # Token extraído del encabezado Authorization.
                self._jwt_secret,  # Secreto compartido que no se entrega al cliente.
                algorithms=["HS256"],  # Limita explícitamente el algoritmo permitido para evitar confusión de algoritmos.
                options={"require": ["exp", "iat", "sub", "role"]},  # Exige todos los claims necesarios para identidad y vigencia.
            )  # Retorna claims solo si token, firma y exp son válidos.
            normalized_rut = normalize_rut(claims["sub"])  # Valida el subject y lo normaliza antes de confiar en él.
            role = UserRole(claims["role"])  # Acepta solo los dos roles definidos por la aplicación.
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:  # Agrupa token vencido, mal formado o claims incorrectos.
            raise InvalidTokenError("El token de acceso no es válido.") from error  # Devuelve un error genérico sin detalles de firma.

        return UserIdentity(normalized_rut, role)  # Entrega una identidad verificada a las dependencias de FastAPI.

    def _create_access_token(self, identity: UserIdentity) -> str:
        """Firma un JWT corto que contiene subject, rol y tiempos estándar."""
        issued_at = datetime.now(UTC)  # Usa UTC para evitar ambigüedades de huso horario.
        claims = {  # Construye el conjunto mínimo de afirmaciones necesarias para autorizar.
            "sub": identity.rut,  # Subject estable de usuario expresado en RUT normalizado.
            "role": identity.role.value,  # Rol usado por la API para autorizar acciones administrativas.
            "iat": issued_at,  # Marca la emisión para auditoría temporal y validación.
            "exp": issued_at + self._token_ttl,  # Hace que el token caduque automáticamente.
        }  # Finaliza el payload firmado sin datos personales adicionales.
        return jwt.encode(claims, self._jwt_secret, algorithm="HS256")  # Firma el payload y devuelve el token listo para Authorization Bearer.

    def _validate_password(self, password: str) -> bytes:
        """Exige una contraseña de al menos 12 caracteres y no más de 72 bytes."""
        if not isinstance(password, str) or len(password) < 12:  # Pide una longitud mínima para desalentar claves débiles.
            raise ValueError("La contraseña debe contener al menos 12 caracteres.")  # No permite crear cuentas con una clave demasiado corta.
        password_bytes = password.encode("utf-8")  # Mide bytes porque ese es el límite operativo de bcrypt.
        if len(password_bytes) > 72:  # Evita truncamiento o error de bcrypt en contraseñas multibyte extensas.
            raise ValueError("La contraseña no puede superar 72 bytes en UTF-8.")  # Rechaza la clave antes de persistir un hash ambiguo.
        return password_bytes  # Retorna los bytes validados que bcrypt recibirá.

    def _check_dummy_password(self, password: str) -> None:
        """Ejecuta bcrypt con un hash ficticio para reducir diferencias temporales."""
        candidate = password.encode("utf-8") if isinstance(password, str) else b""  # Convierte una contraseña disponible o usa entrada vacía.
        if len(candidate) > 72:  # Protege bcrypt frente a entradas demasiado largas.
            candidate = b"invalid-password"  # Sustituye la entrada para que el cálculo ficticio sea seguro y acotado.
        bcrypt.checkpw(candidate, self._dummy_hash)  # Consume el mismo tipo de operación criptográfica que una verificación real.

    def _ensure_schema(self) -> None:
        """Crea la tabla de cuentas locales si no existe y conserva su contenido."""
        with closing(sqlite3.connect(self._database_path, timeout=10)) as connection:  # Abre SQLite y garantiza cierre del recurso.
            with connection:  # Confirma la creación de tabla como una transacción.
                connection.execute(  # Define persistencia mínima para identidad, hash y rol.
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        rut TEXT PRIMARY KEY,
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('cliente', 'administrador')),
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )  # La restricción CHECK evita roles distintos de los soportados.
