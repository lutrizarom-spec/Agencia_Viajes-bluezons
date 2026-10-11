# Agencia de Viajes - Sistema de Gestión (POO & DAO)

Aplicación de consola de una agencia de viajes. `main.py` presenta un menú interactivo para crear, listar, modificar y dar de baja paquetes usando `PaqueteDao` y SQLite, e incluye como opción la demostración automática del modelo de clases. Las capas de API y servicios siguen disponibles por separado en `main_api.py`.

## Arquitectura

- **Modelo:** `Paquete_Turistico` contiene los datos comunes y los subtipos especializan su cálculo. `Cliente` hereda los datos comunes de `Persona`. `Reserva` recibe objetos `Cliente` y `Paquete_Turistico` existentes y crea sus `Detalle_Reserva`; `Pago` crea su `Boleta`. El diagrama editable en Mermaid está en `diagrama_agencia_completo.mmd`.
- **Consola:** `main.py` ofrece el menú CRUD de paquetes (opciones 1 a 4) y la opción de demostración del modelo, que imprime los tres precios polimórficos, prueba una validación de setter, presenta una reserva con sus detalles y controla los errores de pasaporte y anticipo.
- **API REST local:** `main_api.py` expone catálogo público, login JWT, consulta FX y rutas protegidas de reserva e inventario.
- **Persistencia:** `Dao` comparte la conexión y el cursor; `PaqueteDao` implementa las operaciones de almacenamiento para paquetes.
- **Base de datos:** `conectar.py` abre la base SQLite y activa `PRAGMA foreign_keys = ON` en la conexión.

### Frontera entre dominio/consola y API

El proyecto mantiene dos capas de reservas con responsabilidades distintas:

- **Dominio/consola:** las clases `Reserva`, `Detalle_Reserva`, `Pago` y `Boleta` (`model/`) y su persistencia `ReservaDao` (`reservas_modelo`, `detalle_reserva`, `servicios`). Las usa el menú `main.py` y las pruebas de modelo. La API no las utiliza.
- **API operativa:** `CompraService` persiste en `reservas`, `payments` y `package_inventory` con transacciones, idempotencia y control de inventario. Comparte las fórmulas de precio y la regla de pasaporte del dominio, pero no `ReservaDao`.

Ambas capas usan los mismos tres estados de pago; el dominio los expresa en español y la API los persiste y expone en inglés:

| Estado del dominio (`Pago.estado`) | Estado persistido/API |
|---|---|
| `pendiente` | `pending` |
| `confirmado` | `confirmed` |
| `fallido` | `failed` |

## Características principales

- Crear la tabla de paquetes si todavía no existe.
- Registrar paquetes nacionales, internacionales y cruceros.
- Consultar el listado con detalles y precio calculado según el tipo de paquete.
- Dar de baja paquetes sin borrar reservas, pagos ni historial.
- Encapsular atributos del dominio mediante propiedades y atributos privados.
- Representar composición `Reserva`–`Detalle_Reserva` y `Pago`–`Boleta`, además de las asociaciones de `Reserva` con `Cliente` y `Paquete_Turistico`.
- Exigir pasaporte registrado para reservar paquetes internacionales y un anticipo mínimo del 50 %.
- Validar invariantes de paquetes al construirlos: código y duración positivos,
  nombre no vacío, precio finito y positivo, impuesto portuario finito y no
  negativo, y pasaporte estrictamente booleano.
- Ejecutar `python main.py` y operar el menú de consola para crear, listar, modificar y dar de baja paquetes; las entradas inválidas se rechazan sin cerrar el programa.
- Usar consultas SQL parametrizadas para crear, editar y dar de baja registros.
- Consultar la tasa USD/CLP con fallback a la última tasa persistida en SQLite.
- Aplicar límites de solicitudes persistentes y configurables por identificador y ámbito.
- Autenticar usuarios locales por RUT con bcrypt y tokens JWT de corta duración.
- Configurar y descontar cupos de paquetes atómicamente al crear reservas.

## Servicios de resiliencia

`services.fx_service.FxService` recibe una función proveedora de la tasa USD/CLP y
la ruta de la base de datos. Una falla del proveedor activa el último valor de
`fx_cache`; si no existe un valor válido, lanza `FxServiceError`. El servicio no
modifica los cálculos estáticos de los modelos. El circuito abre tras tres fallas
consecutivas y vuelve a probar el proveedor tras 30 segundos por defecto; ambos
valores pueden configurarse al crear el servicio. Su estado es por instancia, por
lo que se debe reutilizar la instancia durante la vida de la aplicación.

### Conectar el proveedor real de mindicador.cl

El módulo incluye `mindicador_usd_clp_provider`, que consulta
`https://mindicador.cl/api/dolar` con un timeout de cinco segundos. Para usarlo,
inyecta la función al crear el servicio; no se ejecuta ninguna llamada de red al
importar el módulo ni al usar los modelos de paquetes:

```python
from pathlib import Path

from services.fx_service import FxService, mindicador_usd_clp_provider

# Construye el servicio con una base persistente para conservar el último valor válido.
fx_service = FxService(
    Path("agencia.db"),
    # Inyecta mindicador.cl como proveedor; también se puede entregar otra función compatible.
    provider=mindicador_usd_clp_provider,
)
# Consulta la tasa: intenta proveedor, actualiza caché y recurre a ella si falla el proveedor.
usd_clp = fx_service.get_usd_clp_rate()
```

La función proveedora retorna un número; `FxService` valida que sea finito y
positivo antes de guardarlo. Errores HTTP, de red o de formato se convierten en
un intento de fallback a caché. En producción, conviene seguir los términos y
políticas de disponibilidad de la API externa. Para Fixer u otro proveedor, se
inyecta una función equivalente que realice la solicitud autenticada y retorne
la tasa USD/CLP; los secretos deben leerse de variables de entorno o de un
gestor de secretos, nunca quedar escritos en el código.

`services.rate_limiter.RateLimiter` guarda ventanas por identificador y ámbito
en `rate_limit_buckets`. La API lo conecta a los intentos de login por RUT y a
las solicitudes de reserva autenticadas. Los buckets se actualizan de forma
atómica en SQLite, limpian entradas expiradas y almacenan el identificador como
hash.

## API REST local

La API utiliza SQLite y no requiere servicios cloud ni Docker. Instala las
dependencias en el entorno virtual desde la raíz del proyecto:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Si el entorno virtual no incluye `pip` y ya tienes `uv` instalado, puedes usar:

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

Antes de iniciar la API, configura una clave JWT local con al menos 32 bytes y,
opcionalmente, una ruta alternativa para la base de datos:

```powershell
$env:AGENCIA_JWT_SECRET = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
$env:AGENCIA_DB_PATH = "$PWD\agencia.db"
.\.venv\Scripts\uvicorn.exe main_api:create_app --factory --reload
```

La aplicación falla al crearse si no se configura `AGENCIA_JWT_SECRET`; no
incluye un secreto predeterminado ni crea usuarios con contraseñas conocidas.
El modo factory construye la API y sus servicios SQLite locales. La
documentación interactiva queda disponible en `http://127.0.0.1:8000/docs`.

Las cuentas se aprovisionan localmente con `AuthService`; no existe un endpoint
público de registro. Ejecuta este fragmento desde la raíz, con las mismas
variables `AGENCIA_DB_PATH` y `AGENCIA_JWT_SECRET` configuradas para la API:

```python
import getpass
import os
from pathlib import Path

from services.auth_service import AuthService, UserRole

auth = AuthService(Path(os.environ.get("AGENCIA_DB_PATH", "agencia.db")), os.environ["AGENCIA_JWT_SECRET"])
auth.create_user("10.000.013-K", getpass.getpass("Contraseña: "), UserRole.ADMINISTRADOR)
```

Los RUT se validan con dígito verificador y se guardan normalizados. Las
contraseñas requieren al menos 12 caracteres, se guardan como hashes bcrypt y
no se pueden recuperar desde la base. El JWT se firma con HS256 y dura 30
minutos. `Cliente` puede reservar; `Administrador` configura cupos a través de
`PUT /admin/paquetes/{package_code}/inventario`. El administrador también puede
crear, editar y dar de baja paquetes; la baja es lógica, oculta el producto del
catálogo público y conserva sus reservas y pagos relacionados.

### Rutas disponibles

| Método y ruta | Acceso | Uso |
|---|---|---|
| `POST /auth/login` | Público, limitado por RUT | Autentica con `rut` y `password`; retorna JWT Bearer. |
| `GET /paquetes?travel_date=YYYY-MM-DD` | Público | Lista paquetes y disponibilidad para esa fecha; sin fecha, informa cupos agregados. |
| `POST /reservas` | JWT requerido | Compra `package_code`, `quantity` y `travel_date`; opcionalmente encola una notificación con `notification_email`. |
| `GET /reservas` | JWT requerido | Lista únicamente las reservas asociadas al RUT del token. |
| `POST /reservas/{reservation_id}/cancelar` | JWT requerido | Cancela una reserva propia; un administrador puede cancelar cualquier reserva. |
| `GET /admin/reservas` | JWT de administrador | Lista el historial de reservas de todos los clientes. |
| `GET /admin/paquetes` | JWT de administrador | Lista paquetes activos e inactivos, incluidos sus campos específicos. |
| `GET /admin/outbox?limit=100` | JWT de administrador | Lista estado, intentos y último error sin exponer destinatario ni contenido del correo. |
| `POST /admin/outbox/{event_id}/retry` | JWT de administrador | Reencola un evento dead-letter; rechaza eventos que no estén agotados. |
| `POST /admin/paquetes` | JWT de administrador | Crea un paquete nacional, internacional, crucero o genérico. |
| `PUT /admin/paquetes/{package_code}` | JWT de administrador | Edita un paquete activo sin cambiar su código. |
| `DELETE /admin/paquetes/{package_code}` | JWT de administrador | Lo oculta mediante baja lógica; una repetición es segura e idempotente. |
| `GET /reservas/{reservation_id}/pago` | JWT requerido | Consulta el estado del pago propio; administración puede consultar cualquier pago. |
| `GET /reservas/{reservation_id}/pagos` | JWT requerido | Lista el anticipo y todos los abonos/intentos de pago de una reserva propia. |
| `POST /reservas/{reservation_id}/pagos` | JWT requerido | Crea un abono adicional dentro del saldo pendiente. |
| `POST /admin/reservas/{reservation_id}/pago` | JWT de administrador | Simula el resultado local `confirmed` o `failed`; no cobra dinero. |
| `GET /tipo-cambio` | Público | Retorna USD/CLP del proveedor FX o su caché SQLite. |
| `PUT /admin/paquetes/{package_code}/inventario` | JWT de administrador | Configura capacidad local de un paquete y `travel_date`. |

### Flujo de uso recomendado

1. Aprovisiona por `AuthService` una cuenta `ADMINISTRADOR` y otra `CLIENTE`;
   el ejemplo anterior muestra cómo crear la primera sin exponer registro
   público ni contraseñas iniciales en la API.
2. Inicia sesión en `/auth/login`. Usa el token de administrador para crear un
   paquete en `/admin/paquetes` y configurar sus cupos/fecha en
   `/admin/paquetes/{codigo}/inventario`.
3. Consulta `/paquetes`; el cliente inicia sesión y compra con `POST /reservas`.
   Conserva el mismo `X-Idempotency-Key` si debe reintentar la solicitud.
4. Consulta `/reservas` y los pagos asociados. La cancelación de una compra
   pagada queda bloqueada (`409`) hasta que se implemente un flujo de reembolso.

Los cuerpos, respuestas y credenciales se pueden probar de extremo a extremo
desde `/docs`; las compras solo simulan el estado del pago y no cobran dinero.

La cancelación libera cupos y cierra pagos pendientes en una transacción. Una
reserva con pagos confirmados responde `409 Conflict` y conserva su estado e
inventario; no se permiten cancelaciones pagadas hasta incorporar reembolsos.

`services.compra_service.CompraService` registra inventario y reservas en
`package_inventory`, `reservas` y `payments`. El inventario se identifica por
paquete y fecha de viaje, y la compra descuenta cupos de esa fecha bajo
`BEGIN IMMEDIATE` junto con el recibo y su pago inicial, en una única
transacción SQLite. El catálogo no define inventario de fábrica: un
administrador debe configurar cupos para cada fecha antes de aceptar compras.
Las reservas internacionales y cruceros consultan `FxService` al crearse y
persisten en la reserva la tasa USD/CLP aplicada y el precio resultante; así los
reintentos y pagos posteriores no recalculan el precio con otra cotización.
Este flujo local no integra una pasarela de pago, retenciones externas de
inventario ni emisión de boletas. Los registros locales solo permiten ensayar
estados y no procesan tarjetas, transferencias ni cobros reales.

Las reservas nuevas quedan en estado `confirmed`. La cancelación cambia el
estado a `cancelled`, registra `cancelled_at` y devuelve los cupos en la misma
transacción `BEGIN IMMEDIATE`; si cualquiera de esas escrituras falla, ambas
se revierten. Repetir una cancelación devuelve el mismo recibo cancelado y no
libera inventario una segunda vez. Los clientes solo pueden listar o cancelar
sus propias reservas; el rol administrador puede consultar `/admin/reservas`
y cancelar cualquier reserva. Al iniciar, `CompraService` añade las columnas
de estado a bases SQLite existentes y marca sus reservas anteriores como
confirmadas, sin eliminar registros. En la migración, el inventario antiguo y
las reservas históricas se conservan con fecha `legacy`; el administrador debe
configurar capacidad para fechas concretas antes de vender nuevas salidas.

### Simulación local del estado de pago y abonos

Al crear una reserva, el backend retiene los cupos y crea en la misma
transacción un pago inicial pendiente equivalente al 50 % del total, con un
plazo de quince minutos (`expires_at`). La respuesta incluye el identificador
del pago y `payment_expires_at`. El cliente consulta el último intento con
`GET /reservas/{reservation_id}/pago` y el historial completo con
`GET /reservas/{reservation_id}/pagos`. Un administrador simula el resultado
de un intento específico mediante `POST /admin/reservas/{reservation_id}/pago`:

```json
{"payment_id": "uuid-del-pago", "status": "confirmed"}
```

La transición de cada intento es `pending` a `confirmed` o `failed`; no se
puede revertir un resultado terminal. Tras confirmar el anticipo, el titular
puede crear abonos con `POST /reservas/{reservation_id}/pagos`:

```json
{"amount": 10000}
```

Cada abono debe ser positivo y no superar el saldo restante; solo se permite un
intento pendiente a la vez. Los abonos confirmados se acumulan hasta completar
el total. Si falla o vence el pago inicial antes de cualquier abono confirmado,
la reserva se cancela y se liberan los cupos. Si un intento posterior falla o
vence, solo se cierra ese intento: la reserva y los cupos se conservan para
permitir otro abono. Repetir el mismo resultado terminal es idempotente;
intentar el resultado opuesto devuelve `409 Conflict`. Solo administración
puede simular resultados. Cancelar una reserva no realiza reembolsos. Las
reservas previas a esta función reciben un pago histórico confirmado (o fallido
si la reserva ya estaba cancelada), sin afectar de nuevo su inventario.

FastAPI realiza un barrido al iniciar y luego cada 30 segundos mientras el
servidor esté activo. Si vence el pago inicial sin pagos confirmados, la reserva
se cancela y sus cupos se liberan atómicamente; el vencimiento de un abono
posterior conserva la reserva. La demora máxima después del plazo es
aproximadamente un intervalo de barrido; al reiniciar se procesan
inmediatamente los vencimientos ocurridos mientras estaba detenida. Los
errores del worker quedan registrados y las transiciones manuales también
ejecutan primero un barrido para impedir confirmar un pago vencido.

### Precisión monetaria

Los importes de reservas y pagos se calculan y persisten como centésimos
enteros (`*_minor`); se redondean a dos decimales con `ROUND_HALF_UP`. Las
columnas `REAL` anteriores se conservan por compatibilidad, pero no se usan
para calcular saldos. Al iniciar, las bases existentes reciben las nuevas
columnas y sus importes se convierten con `Decimal`; valores que no caben en el
formato o son menores a un centésimo fallan explícitamente. Las respuestas HTTP
siguen exponiendo importes JSON numéricos.

### Notificaciones por correo con outbox

`POST /reservas` acepta `notification_email` opcional. Si se entrega, la reserva
y el evento de notificación se guardan dentro de la misma transacción SQLite;
un reintento idempotente no genera un segundo evento. El worker reclama eventos
con un lease transaccional, envía fuera de la transacción y registra éxito o
fallo con backoff y dead-letter tras cinco intentos. Si un proceso se cae, otro
puede reclamar el evento al vencer el lease. La entrega es de tipo *at least
once*: una caída después del envío y antes de registrar `sent` todavía puede
causar un correo repetido.
Administración puede inspeccionar eventos y reintentar únicamente los que
agotaron sus intentos mediante las rutas `/admin/outbox`; el listado excluye
payload/destinatario. Las operaciones de correo siguen siendo *at least once*.

Configura `AGENCIA_SMTP_HOST` y `AGENCIA_SMTP_SENDER`; opcionalmente
`AGENCIA_SMTP_PORT` (587), `AGENCIA_SMTP_USERNAME`,
`AGENCIA_SMTP_PASSWORD`, `AGENCIA_SMTP_USE_SSL` y `AGENCIA_SMTP_TIMEOUT` (10).
`AGENCIA_SMTP_USE_SSL` acepta `true`/`false`, `yes`/`no` o `1`/`0`; puerto y
timeout fuera de rango, no finitos o con valores de modo inválidos hacen fallar
la configuración al iniciar, en vez de degradarla silenciosamente.
El servidor inicia sin SMTP, pero deja las notificaciones en cola y registra
una advertencia. No se almacenan credenciales SMTP en SQLite ni en el código.

### Reintentos e idempotencia de reservas

`POST /reservas` y `POST /reservas/{reservation_id}/pagos` aceptan el encabezado
opcional `X-Idempotency-Key` (1–128 caracteres ASCII imprimibles). Se
recomienda generar una clave distinta por operación lógica y conservarla al
reintentar por timeout o pérdida de respuesta. En abonos la clave queda acotada
a la reserva y al monto; repetirla con el mismo monto devuelve el pago existente
y cambiar el monto produce `409 Conflict`.

```http
POST /reservas
Authorization: Bearer <token>
X-Idempotency-Key: checkout-2026-000123
Content-Type: application/json

{"package_code": 101, "quantity": 2, "travel_date": "2026-12-15"}
```

La creación de reserva responde `201` con pago `pending`; un reenvío con el
mismo RUT, clave y cuerpo devuelve el recibo actual con `200` sin consumir más
cupos. Reutilizar
la clave con otro paquete o cantidad produce `409 Conflict`. La clave, la huella
del cuerpo y el recibo se confirman junto con el descuento de inventario; la
migración del esquema añade columnas e índice sin borrar reservas existentes.
Las migraciones locales de reservas, pagos, inventario, paquetes y límites se
ejecutan transaccionalmente; si falla la conversión de datos heredados, los
cambios parciales de esquema se revierten y el error se informa al iniciar.
Las compras sin encabezado conservan el comportamiento anterior y no son
idempotentes.

El servicio reintenta hasta tres veces por defecto solo si SQLite informa
`SQLITE_BUSY` o `SQLITE_LOCKED`, con timeout breve y espera incremental. Los
errores SQL no relacionados con bloqueos no se reintentan; se devuelven como
fallo de persistencia. Si se agotan los intentos, la API responde `503` y
`Retry-After: 1`; si el cliente usa clave idempotente, debe conservarla al
reintentar.

Instala las dependencias de ejecución y desarrollo con:

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Nota de entorno: si `pip` falla al descargar por TLS, comprueba que las variables `SSL_CERT_FILE` y `REQUESTS_CA_BUNDLE` no apunten a un certificado inexistente o inválido; es una incidencia del entorno local, ajena al código del proyecto.

Ejecuta toda la suite desde la raíz con cualquiera de estos comandos:

```powershell
python -m pytest
pytest
```

`pytest.ini` configura la ruta raíz para ambos comandos. GitHub Actions ejecuta
la misma suite al subir cambios a `main` y en cada pull request.

Calidad y cobertura (instaladas en `requirements-dev.txt`):

```powershell
python -m ruff check .
python -m mypy          # informativo: aún no bloquea CI
python -m pytest --cov --cov-report=term
```

`ruff` y la cobertura bloquean el CI; `mypy` se ejecuta como paso no bloqueante mientras se paga la deuda de tipado.

### Copias de seguridad y recuperación local

Con `AGENCIA_DB_PATH` apuntando a la base activa, crea una copia consistente
incluso mientras la aplicación usa SQLite. El destino debe ser nuevo; no se
sobrescriben copias existentes:

```powershell
$backup = Join-Path $env:USERPROFILE "AgenciaBackups\agencia-$(Get-Date -Format 'yyyyMMdd-HHmmss').db"
python scripts\backup_database.py $env:AGENCIA_DB_PATH $backup
```

El script comprueba `PRAGMA integrity_check` antes de informar éxito. Guarda las
copias fuera del repositorio, limita su acceso y cifra el almacenamiento según
la política de datos de la organización: el script no cifra ni replica copias
remotamente. Para restaurar, detén todos los procesos de la API, conserva una
copia aparte del archivo actual y reemplázalo manualmente por una copia
verificada; después inicia la aplicación y comprueba `/docs` y los registros.

Esta guía prepara el uso local; no configura hosting, TLS, gestor de secretos,
alertas ni recuperación ante desastre. Antes de producción, aprovisiona el
secreto JWT y las credenciales SMTP mediante un gestor seguro, publica la API
tras un proxy HTTPS y planifica copias externas cifradas y pruebas periódicas
de restauración. Los pagos siguen siendo simulados; no se cobran fondos reales.

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
