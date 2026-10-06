# Agencia de Viajes - Sistema de Gestión (POO & DAO)

Aplicación de consola para registrar y administrar paquetes turísticos. El proyecto organiza las responsabilidades siguiendo un enfoque MVC/DAO: los modelos representan el dominio, `main.py` gestiona el menú y la interacción con el usuario, y la capa DAO concentra el acceso a SQLite. La base de datos local se guarda en `agencia.db`.

## Arquitectura

- **Modelo:** `Paquete_Turistico` contiene los datos comunes y los modelos `Paquete_Nacional`, `Paquete_Internacional` y `Paquete_Crucero` especializan su comportamiento.
- **Vista y flujo de aplicación:** `main.py` presenta el menú de consola, valida entradas y coordina las operaciones.
- **API REST local:** `main_api.py` expone catálogo público, login JWT, consulta FX y rutas protegidas de reserva e inventario.
- **Persistencia:** `Dao` comparte la conexión y el cursor; `PaqueteDao` implementa las operaciones de almacenamiento para paquetes.
- **Base de datos:** `conectar.py` abre la base SQLite y activa `PRAGMA foreign_keys = ON` en la conexión.

## Características principales

- Crear la tabla de paquetes si todavía no existe.
- Registrar paquetes nacionales, internacionales y cruceros.
- Consultar el listado con detalles y precio calculado según el tipo de paquete.
- Dar de baja paquetes sin borrar reservas, pagos ni historial.
- Encapsular atributos del dominio mediante propiedades y atributos privados.
- Validar invariantes de paquetes al construirlos: código y duración positivos,
  nombre no vacío, precio finito y positivo, impuesto portuario finito y no
  negativo, y pasaporte estrictamente booleano.
- Validar entradas numéricas del menú y del formulario de registro.
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
| `GET /paquetes` | Público | Lista paquetes, precios calculados por los modelos actuales y cupos conocidos. |
| `POST /reservas` | JWT requerido | Compra `package_code` y `quantity`; asocia el RUT autenticado. |
| `GET /reservas` | JWT requerido | Lista únicamente las reservas asociadas al RUT del token. |
| `POST /reservas/{reservation_id}/cancelar` | JWT requerido | Cancela una reserva propia; un administrador puede cancelar cualquier reserva. |
| `GET /admin/reservas` | JWT de administrador | Lista el historial de reservas de todos los clientes. |
| `GET /admin/paquetes` | JWT de administrador | Lista paquetes activos e inactivos, incluidos sus campos específicos. |
| `POST /admin/paquetes` | JWT de administrador | Crea un paquete nacional, internacional, crucero o genérico. |
| `PUT /admin/paquetes/{package_code}` | JWT de administrador | Edita un paquete activo sin cambiar su código. |
| `DELETE /admin/paquetes/{package_code}` | JWT de administrador | Lo oculta mediante baja lógica; una repetición es segura e idempotente. |
| `GET /reservas/{reservation_id}/pago` | JWT requerido | Consulta el estado del pago propio; administración puede consultar cualquier pago. |
| `POST /admin/reservas/{reservation_id}/pago` | JWT de administrador | Simula el resultado local `confirmed` o `failed`; no cobra dinero. |
| `GET /tipo-cambio` | Público | Retorna USD/CLP del proveedor FX o su caché SQLite. |
| `PUT /admin/paquetes/{package_code}/inventario` | JWT de administrador | Configura capacidad local de un paquete. |

`services.compra_service.CompraService` registra inventario y reservas en
`package_inventory` y `reservas`. Ejecuta la compra bajo `BEGIN IMMEDIATE` y
confirma descuento de cupos más recibo en una única transacción SQLite. El
catálogo existente no define inventario de fábrica: un administrador debe
configurar cupos antes de aceptar compras. Este flujo local no integra una
pasarela de pago, retenciones externas de inventario ni emisión de boletas. El
registro local de pagos solo permite ensayar estados y no procesa tarjetas,
transferencias ni cobros reales.

Las reservas nuevas quedan en estado `confirmed`. La cancelación cambia el
estado a `cancelled`, registra `cancelled_at` y devuelve los cupos en la misma
transacción `BEGIN IMMEDIATE`; si cualquiera de esas escrituras falla, ambas
se revierten. Repetir una cancelación devuelve el mismo recibo cancelado y no
libera inventario una segunda vez. Los clientes solo pueden listar o cancelar
sus propias reservas; el rol administrador puede consultar `/admin/reservas`
y cancelar cualquier reserva. Al iniciar, `CompraService` añade las columnas
de estado a bases SQLite existentes y marca sus reservas anteriores como
confirmadas, sin eliminar registros.

### Simulación local del estado de pago

Al crear una reserva, el backend retiene los cupos y crea en la misma
transacción un registro de pago con estado `pending`, el monto congelado del
recibo y un plazo de quince minutos (`expires_at`). La respuesta de reserva
incluye `payment_expires_at`. El cliente puede consultar su estado con
`GET /reservas/{reservation_id}/pago`. Un administrador simula la respuesta de
un proveedor con `POST /admin/reservas/{reservation_id}/pago` y uno de estos
cuerpos:

```json
{"status": "confirmed"}
```

```json
{"status": "failed"}
```

La transición permitida es `pending` a `confirmed` o `failed`; los resultados
terminales no se pueden revertir. Confirmar conserva la reserva y sus cupos.
Marcar fallido cancela la reserva y devuelve su capacidad en una única
transacción. Repetir el mismo resultado terminal es idempotente; intentar el
resultado opuesto devuelve `409 Conflict`. La simulación está restringida a
administradores para no permitir que un cliente declare pagado su propio
pedido. Cancelar una reserva con pago pendiente marca también el pago como
fallido; cancelar una ya confirmada no realiza reembolsos. Las reservas previas
a esta función reciben un registro de pago histórico confirmado (o fallido si
la reserva ya estaba cancelada), sin afectar de nuevo su inventario.

FastAPI realiza un barrido al iniciar y luego cada 30 segundos mientras el
servidor esté activo. Si vence un pago pendiente, queda identificado como
`expired`, su reserva se cancela y los cupos se liberan atómicamente. La demora
máxima después del plazo es, por tanto, aproximadamente un intervalo de
barrido; al reiniciar la aplicación se procesan inmediatamente vencimientos
ocurridos mientras estaba detenida. Si el worker no puede completar el ciclo,
el error queda registrado y visible en el log del servidor; las transiciones
manuales también ejecutan primero un barrido y no pueden confirmar un pago
fuera de plazo.

### Reintentos e idempotencia de reservas

`POST /reservas` acepta el encabezado opcional `X-Idempotency-Key` (1–128
caracteres ASCII imprimibles). Se recomienda generar una clave distinta por
intento lógico de compra y conservarla cuando se reintenta por timeout o pérdida
de respuesta:

```http
POST /reservas
Authorization: Bearer <token>
X-Idempotency-Key: checkout-2026-000123
Content-Type: application/json

{"package_code": 101, "quantity": 2}
```

La creación de reserva responde `201` con pago `pending`; un reenvío con el
mismo RUT, clave y cuerpo devuelve el recibo actual con `200` sin consumir más
cupos. Reutilizar
la clave con otro paquete o cantidad produce `409 Conflict`. La clave, la huella
del cuerpo y el recibo se confirman junto con el descuento de inventario; la
migración del esquema añade columnas e índice sin borrar reservas existentes.
Las compras sin encabezado conservan el comportamiento anterior y no son
idempotentes.

El servicio reintenta hasta tres veces por defecto solo si SQLite informa
`SQLITE_BUSY` o `SQLITE_LOCKED`, con timeout breve y espera incremental. Los
errores SQL no relacionados con bloqueos no se reintentan; se devuelven como
fallo de persistencia. Si se agotan los intentos, la API responde `503` y
`Retry-After: 1`; si el cliente usa clave idempotente, debe conservarla al
reintentar.

Las pruebas locales de FX y rate limiter están en
`tests/test_blindaje_completo.py`; las pruebas de API, autenticación y compra
están en `tests/test_api_local.py`; los escenarios de idempotencia, expiración
de JWT, bloqueos SQLite, ciclo de vida de reservas y pagos locales están en
`tests/test_api_edge_cases.py`. Ejecuta las tres suites con:

```powershell
python -m unittest discover -s tests -v
```

Este checkout no contiene una suite previa de 161 pruebas, por lo que no es
posible certificar su estado.

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
