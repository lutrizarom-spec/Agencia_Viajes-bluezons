"""API REST local para autenticación, catálogo, reservas y tipo de cambio."""

import asyncio  # Ejecuta limpieza periódica en el ciclo asíncrono sin bloquear las rutas.
import logging  # Registra fallos del worker de expiración en vez de ocultarlos.
import os  # Lee configuración local del entorno sin almacenar secretos en el repositorio.
import sqlite3  # Inicializa el esquema del catálogo en el archivo SQLite configurado.
from decimal import Decimal
from contextlib import asynccontextmanager, closing  # Administra vida de app y cierra conexiones SQLite.
from collections.abc import Callable  # Tipifica el proveedor FX inyectable sin acoplar la API a una implementación.
from datetime import date  # Valida y transporta la fecha solicitada para el viaje.
from pathlib import Path  # Resuelve rutas de base locales de manera independiente del directorio actual.
from typing import Annotated, Literal  # Expresa dependencias, validaciones y estados admitidos del flujo de pago.

from fastapi import Depends, FastAPI, Header, HTTPException, Path as PathParameter, Query, Response, status  # Define rutas, headers HTTP, respuestas y errores.
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer  # Extrae el token Bearer enviado por Authorization.
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator  # Valida cuerpos JSON y documenta esquemas OpenAPI.

from dao.paquete_dao import PaqueteDao  # Reutiliza la lectura del catálogo ya implementada.
from model.paquete_crucero import Paquete_Crucero  # Calcula precio del subtipo crucero mediante su modelo existente.
from model.paquete_internacional import Paquete_Internacional  # Calcula precio internacional mediante su modelo existente.
from model.paquete_nacional import Paquete_Nacional  # Calcula precio nacional mediante su modelo existente.
from model.paquete_turistico import Paquete_Turistico  # Proporciona precio base para filas genéricas antiguas.
from model.money import from_minor_units, to_minor_units
from services.auth_service import (  # Importa autenticación, normalización y errores de credenciales.
    AuthService,
    InvalidCredentialsError,
    InvalidTokenError,
    UserIdentity,
    UserRole,
    normalize_rut,
)
from services.compra_service import (  # Importa compra transaccional y errores de inventario.
    CapacityBelowReservedError,
    CompraService,
    DatabaseBusyError,
    ExchangeRateUnavailableError,
    IdempotencyConflictError,
    InsufficientCapacityError,
    InventoryNotConfiguredError,
    PackageNotFoundError,
    PaymentIdempotencyConflictError,
    PaymentTransitionConflictError,
    PurchasePersistenceError,
    ReservationNotFoundError,
    SQLITE_INTEGER_MAX,
)
from services.fx_service import (  # Inyecta proveedor externo y traduce falta de cotización.
    FxService,
    FxServiceError,
    mindicador_usd_clp_provider,
)
from services.rate_limiter import RateLimiter  # Aplica límites locales persistidos a las rutas sensibles.
from services.notification_outbox import (
    Mailer,
    OutboxEventNotFoundError,
    OutboxRepository,
    OutboxWorker,
    SmtpMailer,
)

PAYMENT_EXPIRY_SWEEP_SECONDS = 30  # Limita a treinta segundos la demora adicional para liberar un pago vencido.
NOTIFICATION_OUTBOX_POLL_SECONDS = 2
logger = logging.getLogger(__name__)  # Usa el logger estándar para observar fallos del barrido automático.


class LoginRequest(BaseModel):
    """Cuerpo JSON de autenticación por RUT y contraseña."""

    model_config = ConfigDict(extra="forbid")  # Rechaza campos inesperados para evitar entradas ambiguas.
    rut: str = Field(min_length=7, max_length=12)  # Limita el tamaño de la entrada antes de normalizar el RUT.
    password: str = Field(min_length=1, max_length=128)  # Impide contraseña vacía y cuerpos de login excesivos.


class TokenResponse(BaseModel):
    """Respuesta de login con token Bearer y rol autenticado."""

    access_token: str  # Token firmado que el cliente enviará en Authorization.
    token_type: str  # Esquema HTTP de autenticación, siempre Bearer.
    expires_in: int  # Vida del token en segundos.
    rut: str  # RUT canónico asociado al token.
    role: UserRole  # Rol autenticado que permite al cliente conocer su contexto.


class PackageResponse(BaseModel):
    """Representación pública del paquete y su precio actual calculado."""

    codigo: int  # Clave existente del paquete en el catálogo SQLite.
    nombre: str  # Nombre público que se muestra a quien consulta el catálogo.
    duracion: int  # Duración del viaje expresada en días.
    tipo: str  # Subtipo persistido: nacional, internacional, crucero o genérico.
    precio_por_persona: float  # Precio resultante de la regla de cálculo del modelo.
    cupos_disponibles: int | None  # None significa inventario aún no configurado por un administrador.
    travel_date: date | None  # None indica que se informa disponibilidad agregada, no una fecha concreta.


class PackageFields(BaseModel):
    """Campos comunes y específicos que un administrador puede editar."""

    model_config = ConfigDict(extra="forbid")  # Rechaza atributos desconocidos en solicitudes administrativas.
    nombre: str = Field(min_length=1, max_length=200)  # Limita el nombre público del paquete.
    duracion: int = Field(gt=0, le=SQLITE_INTEGER_MAX)  # Exige días positivos dentro del rango de SQLite.
    precio_base: float = Field(gt=0, allow_inf_nan=False)  # Rechaza montos no positivos, infinitos o NaN.
    tipo: Literal["turistico", "nacional", "internacional", "crucero"]  # Restringe a subtipos que ya existen en el dominio.
    pasaporte_valido: bool | None = None  # Solo se exige para paquetes internacionales.
    impuesto_puerto: float | None = Field(default=None, ge=0, allow_inf_nan=False)  # Solo aplica a cruceros y admite impuesto cero.

    @model_validator(mode="after")  # Valida coherencia entre el subtipo elegido y sus campos exclusivos.
    def validar_campos_del_subtipo(self) -> "PackageFields":
        """Exige los datos del subtipo y rechaza atributos ajenos."""
        if not self.nombre.strip():  # Evita nombres vacíos compuestos solo por espacios.
            raise ValueError("El nombre del paquete no puede estar vacío.")  # Mantiene la invariante del modelo de dominio.
        if self.tipo == "internacional" and self.pasaporte_valido is None:  # La propiedad debe informarse incluso cuando sea False.
            raise ValueError("pasaporte_valido es obligatorio para paquetes internacionales.")  # Evita un internacional incompleto.
        if self.tipo != "internacional" and self.pasaporte_valido is not None:  # No acepta un dato irrelevante para otros modelos.
            raise ValueError("pasaporte_valido solo aplica a paquetes internacionales.")  # Previene payloads contradictorios.
        if self.tipo == "crucero" and self.impuesto_puerto is None:  # Los cruceros requieren su recargo específico.
            raise ValueError("impuesto_puerto es obligatorio para paquetes de crucero.")  # Evita guardar un crucero incompleto.
        if self.tipo != "crucero" and self.impuesto_puerto is not None:  # No acepta un impuesto en subtipos que no lo usan.
            raise ValueError("impuesto_puerto solo aplica a paquetes de crucero.")  # Mantiene el contrato por subtipo inequívoco.
        return self  # Permite que Pydantic continúe con el modelo validado.


class PackageCreateRequest(PackageFields):
    """Cuerpo para registrar un nuevo paquete con su código único."""

    codigo: int = Field(gt=0, le=SQLITE_INTEGER_MAX)  # Valida el identificador primario antes de llegar a SQLite.


class AdminPackageResponse(BaseModel):
    """Representación administrativa con estado de publicación y campos del subtipo."""

    codigo: int  # Identificador persistente del paquete.
    nombre: str  # Nombre editable del producto.
    duracion: int  # Duración en días.
    tipo: str  # Discriminador del modelo turístico.
    precio_base: float  # Monto almacenado antes de aplicar la fórmula del subtipo.
    precio_por_persona: float  # Precio calculado por el modelo existente.
    pasaporte_valido: bool | None  # Valor exclusivo de paquetes internacionales.
    impuesto_puerto: float | None  # Recargo exclusivo de cruceros.
    activo: bool  # Indica si el paquete aparece en el catálogo público.


class ReservationRequest(BaseModel):
    """Cuerpo JSON para solicitar cupos de un paquete."""

    model_config = ConfigDict(extra="forbid")  # Rechaza atributos que no formen parte del contrato de compra.
    package_code: int = Field(gt=0, le=SQLITE_INTEGER_MAX)  # Exige código positivo dentro del rango entero de SQLite.
    quantity: int = Field(gt=0, le=SQLITE_INTEGER_MAX)  # Exige cupos positivos que puedan persistirse como INTEGER.
    travel_date: date  # La disponibilidad se consulta y descuenta para este día concreto.
    notification_email: EmailStr | None = None
    passport: str | None = Field(default=None, max_length=32)  # Pasaporte del titular; obligatorio para paquetes internacionales.


class ReservationResponse(BaseModel):
    """Recibo con estado de reserva y estado independiente del pago local."""

    reservation_id: str  # UUID único con que se identifica la reserva.
    rut: str  # RUT autenticado al que queda asociada la compra.
    package_code: int  # Código del paquete adquirido.
    quantity: int  # Cupos que se descontaron atómicamente.
    travel_date: date  # Fecha para la que se reservó inventario.
    unit_price: float  # Precio por persona usado en el cálculo.
    total_price: float  # Total calculado a partir del precio unitario y cantidad.
    exchange_rate_applied: float | None  # Cotización USD/CLP congelada en la reserva cuando aplica.
    total_paid: float  # Suma de abonos confirmados.
    balance_due: float  # Saldo pendiente de pago.
    created_at: str  # Instante de confirmación serializado en UTC.
    status: str  # Estado persistido, confirmado o cancelado.
    cancelled_at: str | None  # Fecha UTC de cancelación o None para reservas vigentes.
    payment_id: str  # Identificador local del registro de pago asociado.
    payment_status: str  # Estado local pendiente, confirmado o fallido.
    payment_expires_at: str | None  # Fecha UTC límite para terminar el pago pendiente.


class PaymentTransitionRequest(BaseModel):
    """Resultado que el administrador asigna al pago simulado local."""

    model_config = ConfigDict(extra="forbid")  # Evita aceptar campos de pago o tarjeta que no se procesan.
    payment_id: str | None = Field(default=None, min_length=1, max_length=64)  # Permite resolver un pago específico; None selecciona el pendiente actual.
    status: Literal["confirmed", "failed"]  # Solo se admite resolver un pago pendiente.


class PaymentCreateRequest(BaseModel):
    """Monto de un abono adicional al anticipo inicial."""

    model_config = ConfigDict(extra="forbid")
    amount: Decimal = Field(gt=0)


class PaymentResponse(BaseModel):
    """Respuesta del estado de pago local, no de una transacción bancaria."""

    payment_id: str  # Identificador interno del registro.
    reservation_id: str  # Reserva vinculada al pago.
    amount: float  # Monto congelado al crear la reserva.
    status: str  # Estado pendiente, confirmado o fallido.
    created_at: str  # Fecha UTC en que se creó el pago.
    updated_at: str  # Fecha UTC de la última transición.
    expires_at: str | None  # Deadline UTC mientras siga pendiente; None cuando se resuelve.


class InventoryRequest(BaseModel):
    """Cuerpo JSON administrativo para configurar la capacidad de un paquete."""

    model_config = ConfigDict(extra="forbid")  # Evita ignorar silenciosamente datos administrativos adicionales.
    travel_date: date  # La capacidad configurada queda asociada a una fecha de salida concreta.
    total_capacity: int = Field(ge=0, le=SQLITE_INTEGER_MAX)  # Permite cero cupos y limita el entero a la capacidad de SQLite.


class InventoryResponse(BaseModel):
    """Disponibilidad calculada después de actualizar los cupos totales."""

    package_code: int  # Código de paquete al que pertenece el inventario.
    travel_date: date  # Fecha cuya disponibilidad se acaba de configurar.
    available_capacity: int  # Cupos todavía no reservados después de la operación.


class ExchangeRateResponse(BaseModel):
    """Cotización obtenida del proveedor FX o de su caché de respaldo."""

    base_currency: str  # Código de moneda base consultada, USD.
    target_currency: str  # Código de moneda cotizada, CLP.
    rate: float  # Valor vigente o último valor conocido si falla el proveedor.


def _build_package(row: tuple[object, ...]) -> Paquete_Turistico:
    """Reconstruye un modelo para conservar las fórmulas de precio actuales."""
    codigo, nombre, duracion, precio_base, tipo, pasaporte, impuesto = row  # Separa las siete columnas entregadas por PaqueteDao.
    if tipo == "internacional":  # Reconoce el subtipo que contiene el estado del pasaporte.
        return Paquete_Internacional(codigo, nombre, duracion, precio_base, bool(pasaporte))  # Delega el cálculo a la fórmula internacional ya existente.
    if tipo == "crucero":  # Reconoce el subtipo que agrega impuesto portuario.
        return Paquete_Crucero(codigo, nombre, duracion, precio_base, impuesto)  # Delega el cálculo al modelo crucero existente.
    if tipo == "nacional":  # Reconoce el subtipo nacional sin multiplicador FX.
        return Paquete_Nacional(codigo, nombre, duracion, precio_base)  # Conserva el cálculo del modelo nacional.
    return Paquete_Turistico(codigo, nombre, duracion, precio_base)  # Presenta paquetes genéricos sin inventar atributos particulares.


def _new_package(codigo: int, payload: PackageFields) -> Paquete_Turistico:
    """Construye un modelo de dominio sin duplicar ni alterar fórmulas de precio."""
    if payload.tipo == "internacional":  # Selecciona el modelo que conserva la regla existente para pasaporte.
        if payload.pasaporte_valido is None:  # Revalida la dependencia del dato para el análisis de tipos y el dominio.
            raise ValueError("pasaporte_valido es obligatorio para paquetes internacionales.")  # Evita construir un subtipo incompleto.
        return Paquete_Internacional(codigo, payload.nombre, payload.duracion, payload.precio_base, payload.pasaporte_valido)  # Crea un paquete internacional ya validado.
    if payload.tipo == "crucero":  # Selecciona el subtipo con impuesto portuario.
        if payload.impuesto_puerto is None:  # Revalida el campo que debe existir en todos los cruceros.
            raise ValueError("impuesto_puerto es obligatorio para paquetes de crucero.")  # Evita construir un subtipo incompleto.
        return Paquete_Crucero(codigo, payload.nombre, payload.duracion, payload.precio_base, payload.impuesto_puerto)  # Deja el cálculo de precio en el modelo existente.
    if payload.tipo == "nacional":  # Selecciona el subtipo nacional.
        return Paquete_Nacional(codigo, payload.nombre, payload.duracion, payload.precio_base)  # Mantiene la fórmula nacional existente.
    return Paquete_Turistico(codigo, payload.nombre, payload.duracion, payload.precio_base)  # Acepta el modelo base genérico previamente soportado.


def _admin_package_response(row: tuple[object, ...]) -> AdminPackageResponse:
    """Convierte una fila administrativa en respuesta y conserva precio polimórfico."""
    package = _build_package(row[:7])  # Reconstruye el tipo desde las siete columnas históricas.
    tipo = str(row[4])  # Conserva el discriminador guardado para informar al administrador.
    return AdminPackageResponse(  # Construye la respuesta validada que también expone el estado de publicación.
        codigo=package.codigo,  # Usa la clave persistida.
        nombre=package.nombre,  # Devuelve el nombre ya normalizado por el dominio.
        duracion=package.duracion,  # Devuelve los días del viaje.
        tipo=tipo,  # Indica el subtipo guardado.
        precio_base=package.precio_base,  # Expone el precio base validado.
        precio_por_persona=float(
            from_minor_units(to_minor_units(package.calcular_precio()))
        ),  # Presenta el mismo importe redondeado que guardaría una compra.
        pasaporte_valido=bool(row[5]) if tipo == "internacional" else None,  # Convierte el INTEGER solo cuando es atributo aplicable.
        impuesto_puerto=row[6] if tipo == "crucero" else None,  # Expone el impuesto únicamente a cruceros.
        activo=bool(row[7]),  # Convierte la bandera SQLite a booleano HTTP.
    )  # Retorna un objeto JSON consistente para las rutas administrativas.


def create_app(
    database_path: str | Path | None = None,
    *,
    jwt_secret: str | None = None,
    fx_provider: Callable[[], float] = mindicador_usd_clp_provider,
    notification_mailer: Mailer | None = None,
) -> FastAPI:
    """Construye e inyecta servicios locales; se usa con Uvicorn en modo factory."""
    logging.basicConfig(level=os.environ.get("AGENCIA_LOG_LEVEL", "INFO").upper())  # Uvicorn solo configura sus loggers; sin esto los INFO de la app no se ven.
    resolved_database = Path(  # Resuelve la base configurada o la agencia.db junto al código de la aplicación.
        database_path
        or os.environ.get("AGENCIA_DB_PATH")
        or Path(__file__).resolve().with_name("agencia.db")
    ).resolve()  # Convierte la ruta en absoluta para que no dependa del directorio desde el que se ejecute Uvicorn.
    resolved_secret = jwt_secret or os.environ.get("AGENCIA_JWT_SECRET")  # Acepta clave de pruebas inyectada o secreto del entorno local.
    if not resolved_secret:  # Impide emitir JWT con una clave fija o insegura por defecto.
        raise RuntimeError("Define AGENCIA_JWT_SECRET antes de crear la API.")  # Falla rápido y explica la configuración requerida.

    resolved_database.parent.mkdir(parents=True, exist_ok=True)  # Crea el directorio local si se eligió una ruta personalizada.
    with closing(sqlite3.connect(str(resolved_database), timeout=10)) as connection:  # Prepara la conexión de catálogo sin conservarla abierta.
        connection.execute("PRAGMA foreign_keys = ON")  # Habilita referencias del esquema en esta conexión de inicialización.
        PaqueteDao(connection).crear_tabla()  # Asegura la tabla de paquetes antes de crear tablas que la referencian.

    auth_service = AuthService(resolved_database, resolved_secret)  # Crea usuarios/JWT con el secreto requerido.
    rate_limiter = RateLimiter(resolved_database)  # Reutiliza la persistencia SQLite existente para limitar intentos.
    if not callable(fx_provider):  # Comprueba la dependencia externa inyectada antes de pasarla al servicio.
        raise TypeError("fx_provider debe ser una función que retorne una tasa numérica.")  # Evita fallos tardíos al consultar FX.
    fx_service = FxService(resolved_database, fx_provider)  # Inyecta el proveedor sin hacer llamadas de red al inicializar.
    compra_service = CompraService(
        resolved_database,
        exchange_rate_provider=fx_service.get_usd_clp_rate,
    )  # Comparte la cotización cacheada al calcular y persistir reservas.
    resolved_mailer = notification_mailer or SmtpMailer.from_environment()
    outbox_worker = (
        OutboxWorker(resolved_database, resolved_mailer)
        if resolved_mailer is not None
        else None
    )

    async def payment_expiry_loop() -> None:
        """Revisa cada intervalo pagos pendientes y registra cualquier error fatal."""
        while True:  # Mantiene un único worker por instancia de FastAPI.
            await asyncio.sleep(PAYMENT_EXPIRY_SWEEP_SECONDS)  # Evita consultas constantes a SQLite entre ciclos.
            try:  # Corre SQLite en un hilo para no bloquear el event loop que atiende requests.
                expired_count = await asyncio.to_thread(compra_service.expire_pending_payments)  # Cancela reservas expiradas de manera transaccional.
            except Exception:  # Un fallo transitorio (p. ej. SQLite ocupada) no debe detener el worker para siempre.
                logger.exception("Falló el barrido periódico de pagos pendientes; se reintentará en el próximo ciclo.")  # Conserva traceback y deja visible el fallo.
                continue  # La espera ocurre al inicio del ciclo, así que no hay bucle apretado.
            if expired_count:  # Evita logs repetidos cuando no había nada que vencer.
                logger.info("Se vencieron %s pagos pendientes y se liberó su inventario.", expired_count)  # Informa el impacto del ciclo.

    async def notification_outbox_loop() -> None:
        """Despacha notificaciones reclamadas sin bloquear el servidor ASGI."""
        if outbox_worker is None:
            return
        while True:
            try:
                result = await asyncio.to_thread(outbox_worker.process_batch)
            except Exception:
                logger.exception("Falló el worker de notificaciones outbox; se reintentará.")
                await asyncio.sleep(NOTIFICATION_OUTBOX_POLL_SECONDS)  # Evita un bucle apretado si el error persiste.
                continue
            if result["sent"] or result["failed"] or result["dead"]:
                logger.info(
                    "Outbox: enviados=%s fallidos=%s definitivos=%s.",
                    result["sent"],
                    result["failed"],
                    result["dead"],
                )
            await asyncio.sleep(NOTIFICATION_OUTBOX_POLL_SECONDS)

    @asynccontextmanager
    async def app_lifespan(_: FastAPI):
        """Expira pagos atrasados al iniciar y administra el worker hasta el shutdown."""
        expired_count = await asyncio.to_thread(compra_service.expire_pending_payments)  # Limpia pagos vencidos antes de aceptar tráfico.
        if expired_count:  # Registra únicamente si el inicio recuperó capacidad retenida.
            logger.info("Al iniciar se vencieron %s pagos y se liberó su inventario.", expired_count)  # Hace visible la recuperación al operador.
        worker = asyncio.create_task(payment_expiry_loop(), name="payment-expiry-worker")  # Agenda barridos mientras el servidor está activo.
        notification_worker_task = None
        if outbox_worker is not None:
            notification_worker_task = asyncio.create_task(
                notification_outbox_loop(), name="notification-outbox-worker"
            )
        else:
            logger.warning(
                "SMTP no está configurado; las notificaciones outbox quedarán pendientes."
            )
        try:  # Mantiene el worker vivo durante el ciclo de vida de FastAPI.
            yield  # Entrega el control al servidor y permite atender solicitudes.
        finally:  # Asegura que no quede una tarea huérfana al apagar la aplicación.
            worker.cancel()  # Solicita detener la espera o el siguiente ciclo.
            if notification_worker_task is not None:
                notification_worker_task.cancel()
            try:  # Espera la terminación ordenada del worker cancelado.
                await worker  # Libera la tarea antes de cerrar el ciclo ASGI.
            except asyncio.CancelledError:  # La cancelación solicitada durante shutdown es el cierre esperado.
                pass  # No representa un fallo del proceso.
            if notification_worker_task is not None:
                try:
                    await notification_worker_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Agencia de Viajes API", version="1.0.0", lifespan=app_lifespan)  # Configura limpieza de pagos en startup y shutdown.
    app.state.auth_service = auth_service  # Conserva los servicios compartidos en el estado de esta aplicación.
    app.state.rate_limiter = rate_limiter  # Evita crear una instancia distinta por cada solicitud.
    app.state.compra_service = compra_service  # Comparte la compra atómica entre solicitudes.
    app.state.fx_service = fx_service  # Comparte circuito/cache del proveedor mientras vive el proceso.
    app.state.outbox_worker = outbox_worker
    app.state.outbox_repository = OutboxRepository(resolved_database)
    app.state.database_path = resolved_database  # Permite a rutas y pruebas inspeccionar la ubicación activa.
    bearer_scheme = HTTPBearer(auto_error=False)  # Deja que la dependencia convierta credenciales ausentes a un 401 uniforme.

    def current_user(  # Define la dependencia que autentica una solicitud protegida.
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],  # Extrae token Bearer si el encabezado existe.
    ) -> UserIdentity:
        """Valida Authorization: Bearer y devuelve la identidad del JWT."""
        if credentials is None or credentials.scheme.lower() != "bearer":  # Rechaza token ausente o esquema de autenticación diferente.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticación requerida.", headers={"WWW-Authenticate": "Bearer"})  # Solicita credenciales Bearer válidas.
        try:  # Traduce la excepción de dominio de JWT a un error HTTP.
            return auth_service.authenticate_token(credentials.credentials)  # Valida firma, expiración, RUT y rol.
        except InvalidTokenError as error:  # Captura únicamente tokens inválidos o vencidos.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o vencido.", headers={"WWW-Authenticate": "Bearer"}) from error  # No filtra detalles internos del JWT.

    def administrator_only(  # Define una segunda dependencia para operaciones de inventario.
        identity: Annotated[UserIdentity, Depends(current_user)],  # Reutiliza identidad ya validada por el token Bearer.
    ) -> UserIdentity:
        """Exige el rol administrador además de la autenticación."""
        if identity.role is not UserRole.ADMINISTRADOR:  # Comprueba autorización, separada de la verificación JWT.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Se requiere rol administrador.")  # Distingue acceso autenticado pero no autorizado.
        return identity  # Entrega la identidad administrativa a la operación protegida.

    @app.get("/admin/outbox", tags=["Administración"])
    def list_outbox_events(
        identity: Annotated[UserIdentity, Depends(administrator_only)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[dict[str, object]]:
        """Lista estado y errores de entrega sin exponer correo ni contenido."""
        _ = identity
        return app.state.outbox_repository.list_events(limit=limit)

    @app.post("/admin/outbox/{event_id}/retry", status_code=status.HTTP_204_NO_CONTENT, tags=["Administración"])
    def retry_outbox_event(
        event_id: Annotated[int, PathParameter(ge=1)],
        identity: Annotated[UserIdentity, Depends(administrator_only)],
    ) -> Response:
        """Reencola únicamente eventos agotados, con autorización administrativa."""
        _ = identity
        try:
            app.state.outbox_repository.retry_dead_event(event_id)
        except OutboxEventNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
            ) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/auth/login", response_model=TokenResponse, tags=["Autenticación"])  # Publica el login de usuarios por RUT y clave.
    def login(payload: LoginRequest) -> TokenResponse:
        """Autentica, aplica límite por RUT y entrega un JWT de acceso."""
        try:  # Normaliza el identificador para que variaciones de formato compartan el mismo límite.
            rate_key = normalize_rut(payload.rut)  # Agrupa con puntos, sin puntos y mayúsculas bajo el mismo RUT válido.
        except ValueError:  # Aun los RUT mal formados deben consumir límite para dificultar fuerza bruta.
            rate_key = payload.rut.strip().casefold() or "rut-vacio"  # Conserva clave acotada por el límite de longitud de Pydantic.
        if not rate_limiter.allow_request(rate_key, limit=5, window_seconds=60, scope="auth:login"):  # Permite cinco intentos por RUT cada minuto.
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Demasiados intentos de autenticación; reintente más tarde.")  # Devuelve respuesta estándar para throttling.
        try:  # Ejecuta bcrypt y firma un token solo ante credenciales válidas.
            token, identity = auth_service.authenticate(payload.rut, payload.password)  # Verifica credenciales con error genérico ante fallo.
        except InvalidCredentialsError as error:  # Convierte fallo de formato, usuario o contraseña en un 401.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="RUT o contraseña incorrectos.", headers={"WWW-Authenticate": "Bearer"}) from error  # No revela si existe una cuenta.
        return TokenResponse(access_token=token, token_type="bearer", expires_in=1800, rut=identity.rut, role=identity.role)  # Devuelve JWT con su identidad y vigencia configurada.

    @app.get("/paquetes", response_model=list[PackageResponse], tags=["Catálogo"])  # Publica una consulta sin autenticación al catálogo local.
    def list_packages(travel_date: date | None = None) -> list[PackageResponse]:
        """Lee catálogo; acepta travel_date como query opcional para consultar disponibilidad exacta."""
        with closing(sqlite3.connect(str(resolved_database), timeout=10)) as connection:  # Abre una lectura acotada de catálogo.
            rows = PaqueteDao(connection).obtener_paquetes()  # Reutiliza la consulta de paquetes implementada en el DAO.
        response: list[PackageResponse] = []  # Prepara una lista tipada para serialización JSON y OpenAPI.
        exchange_rate: float | None = None
        for row in rows:  # Convierte cada fila SQLite en un modelo y una respuesta pública.
            package = _build_package(row)  # Aplica la regla polimórfica existente sin cambiar fórmulas.
            try:
                if isinstance(package, (Paquete_Internacional, Paquete_Crucero)):
                    if exchange_rate is None:
                        exchange_rate = fx_service.get_usd_clp_rate()
                    price = package.calcular_precio(exchange_rate)
                else:
                    price = package.calcular_precio()
            except FxServiceError as error:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
            price = float(from_minor_units(to_minor_units(price)))
            response.append(  # Añade los datos públicos, precio y cupos conocidos.
                PackageResponse(
                    codigo=package.codigo,  # Conserva la clave existente del catálogo.
                    nombre=package.nombre,  # Expone el nombre descriptivo del viaje.
                    duracion=package.duracion,  # Informa la duración ya almacenada.
                    tipo=row[4],  # Mantiene la categoría original persistida.
                    precio_por_persona=price,  # Usa FX para internacional/crucero y la regla local para los demás.
                    cupos_disponibles=compra_service.get_available_capacity(package.codigo, travel_date),  # Filtra por la fecha solicitada cuando viene en query.
                    travel_date=travel_date,
                )  # Crea un modelo Pydantic validado antes de la respuesta.
            )  # Incorpora el paquete actual a la respuesta del catálogo.
        return response  # FastAPI serializa la colección como JSON.

    @app.get("/admin/paquetes", response_model=list[AdminPackageResponse], tags=["Administración"])  # Da a administración vista completa, incluidas bajas lógicas.
    def list_admin_packages(  # La dependencia impide exponer el inventario oculto a usuarios clientes.
        identity: Annotated[UserIdentity, Depends(administrator_only)],  # Reutiliza la validación JWT y de rol existente.
    ) -> list[AdminPackageResponse]:
        """Lista todos los paquetes para que administración distinga los inactivos."""
        _ = identity  # La identidad se utiliza para autorizar, no como filtro del catálogo.
        with closing(sqlite3.connect(str(resolved_database), timeout=10)) as connection:  # Abre y cierra una conexión solo para lectura.
            rows = PaqueteDao(connection).obtener_paquetes_admin()  # Incluye filas activas e inactivas con su bandera.
        return [_admin_package_response(row) for row in rows]  # Reutiliza el modelo de dominio y su fórmula de precio.

    @app.post("/admin/paquetes", response_model=AdminPackageResponse, status_code=status.HTTP_201_CREATED, tags=["Administración"])  # Registra paquetes solo con rol administrador.
    def create_package(  # Protege alta y validación del catálogo.
        payload: PackageCreateRequest,  # Valida código, campos comunes y los datos exclusivos del subtipo.
        identity: Annotated[UserIdentity, Depends(administrator_only)],  # Exige identidad administrativa validada.
    ) -> AdminPackageResponse:
        """Crea un paquete publicado desde un cuerpo validado."""
        _ = identity  # La dependencia se usa para autorización del alta.
        try:  # El dominio comprueba también invariantes que podrían no estar en el esquema HTTP.
            package = _new_package(payload.codigo, payload)  # Construye el modelo de dominio antes de persistir.
        except ValueError as error:  # Convierte valores no válidos del dominio a un error de entrada claro.
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error  # No presenta un dato inválido como fallo interno.
        try:  # Traduce errores esperables de persistencia al contrato HTTP.
            with closing(sqlite3.connect(str(resolved_database), timeout=10)) as connection:  # Aísla la escritura en una conexión corta.
                dao = PaqueteDao(connection)  # Reutiliza las operaciones de catálogo existentes.
                dao.insertar_paquete(package)  # Inserta el paquete publicado con código único.
                row = dao.obtener_paquete(payload.codigo, incluir_inactivo=True)  # Recupera la representación persistida para responder.
        except sqlite3.IntegrityError as error:  # Un código duplicado viola la clave primaria y no debe reintentarse.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un paquete con ese código.") from error  # Reporta el conflicto de forma estable.
        except sqlite3.Error as error:  # Expone el fallo técnico como error del servidor sin filtrar SQL.
            logger.exception("No se pudo crear el paquete %s.", payload.codigo)  # Registra diagnóstico para el operador.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo persistir el paquete.") from error  # No disfraza una falla de persistencia.
        if row is None:  # Protege la consistencia si la lectura posterior inesperadamente no encuentra la inserción.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="El paquete se creó, pero no pudo recuperarse.")  # Informa una anomalía observable.
        return _admin_package_response(row)  # Devuelve datos normalizados desde la base.

    @app.put("/admin/paquetes/{package_code}", response_model=AdminPackageResponse, tags=["Administración"])  # Actualiza datos sin cambiar código o estado.
    def update_package(  # Limita la edición a paquetes todavía activos.
        payload: PackageFields,  # Valida cuerpo común y campos propios del subtipo.
        package_code: Annotated[int, PathParameter(gt=0, le=SQLITE_INTEGER_MAX)],  # Valida la clave que identifica el recurso a modificar.
        identity: Annotated[UserIdentity, Depends(administrator_only)],  # Exige el rol administrador.
    ) -> AdminPackageResponse:
        """Edita un paquete publicado y conserva el identificador histórico."""
        _ = identity  # La identidad queda verificada por la dependencia de autorización.
        try:  # Aplica las mismas invariantes de dominio que durante la creación.
            package = _new_package(package_code, payload)  # Construye el tipo de dominio correspondiente al cuerpo.
        except ValueError as error:  # Evita devolver un 500 ante una combinación semánticamente inválida.
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error  # Expone validación como HTTP 422.
        try:  # Captura únicamente fallos propios de la persistencia SQLite.
            with closing(sqlite3.connect(str(resolved_database), timeout=10)) as connection:  # Abre una conexión corta para la edición y lectura.
                dao = PaqueteDao(connection)  # Usa el DAO de paquetes para mantener SQL centralizado.
                updated = dao.actualizar_paquete(package, package_code)  # Actualiza solo filas que sigan activas.
                row = dao.obtener_paquete(package_code) if updated else None  # Recupera la fila solo si la edición se aplicó.
        except sqlite3.Error as error:  # No trata fallos de base de datos como un recurso inexistente.
            logger.exception("No se pudo actualizar el paquete %s.", package_code)  # Mantiene información de diagnóstico en el log.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo actualizar el paquete.") from error  # Devuelve fallo explícito.
        if row is None:  # El código inexistente o la baja lógica se consideran recurso no editable.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No existe un paquete activo con ese código.")  # Evita reactivar accidentalmente una baja.
        return _admin_package_response(row)  # Devuelve la versión actual persistida.

    @app.delete("/admin/paquetes/{package_code}", response_model=AdminPackageResponse, tags=["Administración"])  # Implementa baja lógica y preserva historial.
    def deactivate_package(  # Exige autorización para ocultar un producto del catálogo.
        package_code: Annotated[int, PathParameter(gt=0, le=SQLITE_INTEGER_MAX)],  # Valida el código de paquete solicitado.
        identity: Annotated[UserIdentity, Depends(administrator_only)],  # Reutiliza el control administrativo.
    ) -> AdminPackageResponse:
        """Desactiva el paquete de forma idempotente sin borrar reservas ni pagos."""
        _ = identity  # La dependencia valida que solo administración gestione disponibilidad.
        try:  # Recupera y desactiva el registro dentro de operaciones DAO parametrizadas.
            with closing(sqlite3.connect(str(resolved_database), timeout=10)) as connection:  # Abre una conexión de trabajo local.
                dao = PaqueteDao(connection)  # Centraliza el acceso a paquetes.
                row = dao.obtener_paquete(package_code, incluir_inactivo=True)  # Distingue la inexistencia de una baja ya realizada.
                if row is None:  # Solo el código realmente inexistente produce 404.
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No existe un paquete con ese código.")  # Mantiene un error de recurso explícito.
                if bool(row[7]):  # Cambia el estado solo en la primera petición de baja.
                    dao.eliminar_paquete(package_code)  # Actualiza activo=0 en vez de ejecutar DELETE.
                    row = dao.obtener_paquete(package_code, incluir_inactivo=True)  # Lee el estado final para responder.
        except sqlite3.Error as error:  # Distingue errores SQLite de respuestas HTTP intencionales.
            logger.exception("No se pudo desactivar el paquete %s.", package_code)  # Permite diagnosticar fallos de almacenamiento.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo desactivar el paquete.") from error  # No oculta la falla.
        if row is None:  # Protege frente a una lectura inconsistente posterior a la baja.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo recuperar el paquete desactivado.")  # Informa anomalía al cliente.
        return _admin_package_response(row)  # Confirma el estado inactivo y preserva el historial asociado.

    @app.put("/admin/paquetes/{package_code}/inventario", response_model=InventoryResponse, tags=["Administración"])  # Permite a un admin preparar cupos de venta.
    def configure_inventory(  # El endpoint requiere token y además rol administrador.
        payload: InventoryRequest,  # Valida la nueva capacidad como entero no negativo.
        package_code: Annotated[int, PathParameter(gt=0, le=SQLITE_INTEGER_MAX)],  # Valida el código de ruta dentro del rango entero de SQLite.
        identity: Annotated[UserIdentity, Depends(administrator_only)],  # Niega la operación si la identidad no es administradora.
    ) -> InventoryResponse:
        """Configura cupos locales para poder vender un paquete del catálogo."""
        _ = identity  # Declara explícitamente que la dependencia se usa solo para autorización.
        try:  # Traduce condiciones de catálogo/capacidad a respuestas HTTP explícitas.
            available = compra_service.configure_capacity(package_code, payload.travel_date, payload.total_capacity)  # Persiste capacidad para el día configurado.
        except PackageNotFoundError as error:  # Detecta que el paquete no existe.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error  # Informa que no se encontró el recurso.
        except CapacityBelowReservedError as error:  # Impide reducir capacidad por debajo de lo ya confirmado.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error  # Informa conflicto con reservas existentes.
        return InventoryResponse(package_code=package_code, travel_date=payload.travel_date, available_capacity=available)  # Devuelve la fecha y disponibilidad resultante.

    @app.post("/reservas", response_model=ReservationResponse, status_code=status.HTTP_201_CREATED, tags=["Reservas"])  # Publica una compra que requiere JWT.
    def create_reservation(  # El endpoint solo puede llegar a ejecutarse si el token se validó.
        payload: ReservationRequest,  # Valida el paquete y la cantidad que se desea comprar.
        identity: Annotated[UserIdentity, Depends(current_user)],  # Vincula la compra al subject verificado del token.
        response: Response,  # Permite marcar una repetición idempotente como HTTP 200.
        idempotency_key: Annotated[str | None, Header(alias="X-Idempotency-Key", min_length=1, max_length=128)] = None,  # Acepta opcionalmente una clave de reintento acotada.
    ) -> ReservationResponse:
        """Compra cupos mediante CompraService y aplica un límite por usuario."""
        if not rate_limiter.allow_request(identity.rut, limit=10, window_seconds=60, scope="reservation:create"):  # Evita abuso de creación de reservas por cuenta.
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Demasiadas solicitudes de reserva; reintente más tarde.")  # Informa límite excedido sin llamar a compra.
        try:  # Ejecuta descuento de cupos y creación de recibo en una transacción atómica.
            purchase_result = compra_service.purchase_idempotently(  # Gestiona compra nueva o reproduce un recibo ya confirmado.
                identity.rut,  # Usa el subject del JWT y no acepta el RUT desde el cuerpo.
                payload.package_code,  # Pasa el paquete solicitado al servicio transaccional.
                payload.quantity,  # Pasa la cantidad a descontar atómicamente.
                idempotency_key,  # Permite recuperar el mismo recibo si el cliente reenvía la clave.
                travel_date=payload.travel_date,  # Descuenta cupos únicamente para el día solicitado.
                notification_email=str(payload.notification_email) if payload.notification_email else None,
                passport=payload.passport,  # Aplica la regla de pasaporte dentro de la transacción de compra.
            )  # El servicio guarda clave/huella junto al recibo y al descuento de inventario.
        except PackageNotFoundError as error:  # Mapea código inexistente a recurso no encontrado.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error  # No devuelve recibo si el paquete no existe.
        except (InventoryNotConfiguredError, InsufficientCapacityError) as error:  # Mapea inventario no configurado o insuficiente.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error  # Informa que no se puede completar la compra actual.
        except IdempotencyConflictError as error:  # Detecta que la misma clave identifica un cuerpo de compra distinto.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error  # Evita reutilizar claves de forma ambigua.
        except DatabaseBusyError as error:  # Trata el agotamiento de reintentos como indisponibilidad temporal.
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error), headers={"Retry-After": "1"}) from error  # Indica que el cliente puede reintentar con la misma clave.
        except PurchasePersistenceError as error:  # Evita exponer SQL o detalles internos al consumidor de la API.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo persistir la reserva.") from error  # Informa fallo técnico sin ocultarlo como éxito.
        except (ExchangeRateUnavailableError, FxServiceError) as error:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
        except ValueError as error:  # Mapea el precio inválido o datos de dominio rechazados.
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error  # Diferencia una solicitud semánticamente inválida de fallos técnicos.
        if purchase_result.replayed:  # Distingue la repetición de una reserva que acaba de crearse.
            response.status_code = status.HTTP_200_OK  # Informa que se recuperó un recibo existente sin una nueva compra.
        return ReservationResponse(**purchase_result.receipt.__dict__)  # Serializa el recibo original confirmado por SQLite.

    @app.get("/reservas", response_model=list[ReservationResponse], tags=["Reservas"])  # Expone al usuario autenticado solo sus reservas.
    def list_my_reservations(  # La identidad se deriva del JWT y no de parámetros controlados por el cliente.
        identity: Annotated[UserIdentity, Depends(current_user)],  # Exige token válido para consultar información personal.
    ) -> list[ReservationResponse]:
        """Lista las reservas del RUT autenticado, sin permitir consultar otras cuentas."""
        receipts = compra_service.list_reservations(identity.rut)  # Filtra en SQLite usando el subject normalizado del token.
        return [ReservationResponse(**receipt.__dict__) for receipt in receipts]  # Convierte recibos propios al contrato HTTP.

    @app.get("/admin/reservas", response_model=list[ReservationResponse], tags=["Administración"])  # Ofrece consulta global solo a administradores.
    def list_all_reservations(  # Reutiliza el control de rol de las operaciones administrativas existentes.
        identity: Annotated[UserIdentity, Depends(administrator_only)],  # Rechaza clientes antes de consultar recibos globales.
    ) -> list[ReservationResponse]:
        """Lista reservas de todos los clientes para tareas administrativas."""
        _ = identity  # La dependencia se usa para validar rol, no para limitar la consulta global.
        receipts = compra_service.list_reservations()  # Omite filtro únicamente después de validar el rol administrador.
        return [ReservationResponse(**receipt.__dict__) for receipt in receipts]  # Expone cantidad, importe y estado de cada reserva.

    @app.post("/reservas/{reservation_id}/cancelar", response_model=ReservationResponse, tags=["Reservas"])  # Cancela la reserva del cliente o una reserva elegida por admin.
    def cancel_reservation(  # El servicio transaccional libera capacidad junto al cambio de estado.
        reservation_id: Annotated[str, PathParameter(min_length=1, max_length=64)],  # Limita el identificador recibido por la ruta.
        identity: Annotated[UserIdentity, Depends(current_user)],  # Exige identidad autenticada para toda cancelación.
    ) -> ReservationResponse:
        """Cancela solo reservas propias; un administrador puede cancelar cualquier reserva."""
        try:  # Traduce errores de dominio y persistencia a estados HTTP explícitos.
            receipt = compra_service.cancel_reservation(  # Cambia estado y devuelve cupos en una única transacción.
                reservation_id,  # Identifica la reserva a cancelar.
                identity.rut,  # Propietario validado para la solicitud.
                is_admin=identity.role is UserRole.ADMINISTRADOR,  # Concede acceso global solo al rol de administrador.
            )  # Un segundo intento devuelve la misma reserva cancelada sin liberar cupos otra vez.
        except ReservationNotFoundError as error:  # Trata inexistencia y propiedad ajena con la misma respuesta.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error  # No revela si el UUID ajeno existe.
        except PaymentTransitionConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except DatabaseBusyError as error:  # Informa que la base requiere un reintento posterior.
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error), headers={"Retry-After": "1"}) from error  # Permite retry sin ocultar el bloqueo.
        except PurchasePersistenceError as error:  # Evita exponer detalles SQLite.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo persistir la cancelación.") from error  # Informa el fallo técnico claramente.
        except ValueError as error:  # Rechaza identificadores o datos de entrada inválidos.
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error  # Usa respuesta estándar para entrada semánticamente inválida.
        return ReservationResponse(**receipt.__dict__)  # Devuelve el estado resultante y la fecha de cancelación.

    @app.get("/reservas/{reservation_id}/pago", response_model=PaymentResponse, tags=["Pagos locales"])  # Permite consultar al cliente su pago o al admin cualquiera.
    def get_reservation_payment(  # El estado se obtiene de SQLite y no consulta una entidad financiera.
        reservation_id: Annotated[str, PathParameter(min_length=1, max_length=64)],  # Limita el identificador antes de consultar la base.
        identity: Annotated[UserIdentity, Depends(current_user)],  # Requiere JWT para evitar exponer pagos públicamente.
        payment_id: str | None = None,
    ) -> PaymentResponse:
        """Consulta el pago propio; el administrador puede consultar el de cualquier reserva."""
        try:  # Convierte acceso denegado o ausencia del pago en una respuesta uniforme.
            payment = compra_service.get_payment(  # Comprueba propiedad dentro del servicio, junto con la consulta.
                reservation_id,  # Identifica la reserva cuyo pago se desea consultar.
                identity.rut,  # Usa el RUT canónico del token, nunca un RUT del cuerpo.
                payment_id=payment_id,
                is_admin=identity.role is UserRole.ADMINISTRADOR,  # Permite consulta global solo al administrador.
            )  # Devuelve el último estado local persistido.
        except ReservationNotFoundError as error:  # No diferencia entre reserva inexistente y ajena.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error  # Evita filtrar existencia de otras cuentas.
        return PaymentResponse(**payment.__dict__)  # Serializa los campos sin exponer datos financieros sensibles.

    @app.get("/reservas/{reservation_id}/pagos", response_model=list[PaymentResponse], tags=["Pagos locales"])
    def list_reservation_payments(
        reservation_id: Annotated[str, PathParameter(min_length=1, max_length=64)],
        identity: Annotated[UserIdentity, Depends(current_user)],
    ) -> list[PaymentResponse]:
        """Lista todos los intentos y abonos asociados a una reserva."""
        try:
            payments = compra_service.list_payments(
                reservation_id,
                identity.rut,
                is_admin=identity.role is UserRole.ADMINISTRADOR,
            )
        except ReservationNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        return [PaymentResponse(**payment.__dict__) for payment in payments]

    @app.post(
        "/reservas/{reservation_id}/pagos",
        response_model=PaymentResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Pagos locales"],
    )
    def create_reservation_payment(
        payload: PaymentCreateRequest,
        reservation_id: Annotated[str, PathParameter(min_length=1, max_length=64)],
        identity: Annotated[UserIdentity, Depends(current_user)],
        idempotency_key: Annotated[
            str | None, Header(alias="X-Idempotency-Key", min_length=1, max_length=128)
        ] = None,
    ) -> PaymentResponse:
        """Crea un abono parcial adicional dentro del saldo de la reserva."""
        try:
            payment = compra_service.create_payment(
                reservation_id,
                identity.rut,
                payload.amount,
                is_admin=identity.role is UserRole.ADMINISTRADOR,
                idempotency_key=idempotency_key,
            )
        except ReservationNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except PaymentTransitionConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except PaymentIdempotencyConflictError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
        except sqlite3.Error as error:
            logger.exception("No se pudo crear un pago para la reserva %s.", reservation_id)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo persistir el pago.") from error
        return PaymentResponse(**payment.__dict__)

    @app.post("/admin/reservas/{reservation_id}/pago", response_model=PaymentResponse, tags=["Pagos locales"])  # Simula una notificación de pago solo para administración local.
    def resolve_reservation_payment(  # El usuario cliente no puede declarar su propio pago confirmado.
        payload: PaymentTransitionRequest,  # Restringe el resultado a confirmado o fallido.
        reservation_id: Annotated[str, PathParameter(min_length=1, max_length=64)],  # Identifica la reserva cuyo pago se resolverá.
        identity: Annotated[UserIdentity, Depends(administrator_only)],  # Exige administrador para simular resultados de proveedor.
    ) -> PaymentResponse:
        """Simula la respuesta terminal de un proveedor, sin ejecutar un cobro real."""
        _ = identity  # La dependencia autentica y autoriza; la identidad no modifica el monto.
        try:  # Traduce errores del ciclo de estados a respuestas HTTP explícitas.
            payment = compra_service.transition_payment(reservation_id, payload.status, payment_id=payload.payment_id)  # Confirma o falla el pago elegido bajo una transacción local.
        except ReservationNotFoundError as error:  # Informa que la reserva o el pago no existe.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error  # No realiza cambios ante un identificador desconocido.
        except PaymentTransitionConflictError as error:  # Rechaza cambios desde un pago ya resuelto.
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error  # Mantiene confirmación/fallo como estados terminales.
        except DatabaseBusyError as error:  # Informa bloqueo temporal después de reintentos acotados.
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error), headers={"Retry-After": "1"}) from error  # Permite reintentar sin duplicar efectos.
        except PurchasePersistenceError as error:  # No expone SQL ni presenta el pago como procesado correctamente.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="No se pudo persistir el estado del pago.") from error  # Reporta el fallo técnico de forma explícita.
        except ValueError as error:  # Rechaza resultados distintos de los estados admitidos.
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error  # Señala que la transición solicitada no es válida.
        return PaymentResponse(**payment.__dict__)  # Devuelve el resultado terminal persistido.

    @app.get("/tipo-cambio", response_model=ExchangeRateResponse, tags=["Indicadores"])  # Publica consulta FX con fallback de servicio.
    def get_exchange_rate() -> ExchangeRateResponse:
        """Retorna la tasa USD/CLP o un 503 si proveedor y caché fallan."""
        try:  # Pide al servicio que aplique proveedor, circuito y caché.
            rate = fx_service.get_usd_clp_rate()  # No altera la tarifa fija que usan los modelos actuales.
        except FxServiceError as error:  # Traduce la falta de tasa disponible a indisponibilidad temporal.
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error  # Permite al cliente reintentar sin exponer traceback.
        return ExchangeRateResponse(base_currency="USD", target_currency="CLP", rate=rate)  # Expone la tasa con códigos explícitos de moneda.

    return app  # Retorna la aplicación configurada para Uvicorn y las pruebas de integración.