# AGENTS.md — Agencia de Viajes (bluezons)

## Proyecto
API REST local (FastAPI + SQLite), modelo POO/DAO y menú de consola. Sin cloud ni Docker. CI usa Python 3.12.

## Mapa
- `model/`: dominio (paquetes y subtipos, Cliente, Reserva, Pago, Money).
- `dao/` + `conectar.py`: persistencia SQLite (`crear_conexion`, `foreign_keys = ON`).
- `services/`: `compra_service` (inventario, reservas, pagos), `auth_service`, `fx_service`, `rate_limiter`, `notification_outbox`.
- `main_api.py`: API (`create_app`, modo factory). `main.py`: menú de consola. `scripts/backup_database.py`: copia de la BD.
- `tests/`: pytest, un archivo por área.

## Comandos (PowerShell, desde la raíz)
- Entorno: `.\.venv\Scripts\Activate.ps1`
- Instalar: `python -m pip install -r requirements.txt -r requirements-dev.txt`
- Probar: `python -m pytest` (un archivo: `python -m pytest tests\test_money.py`)
- API: `.\.venv\Scripts\uvicorn.exe main_api:create_app --factory --reload`
- Variables: `AGENCIA_JWT_SECRET` (obligatoria, ≥32 bytes); opcionales `AGENCIA_DB_PATH`, `AGENCIA_SMTP_*`.
- GitHub Actions ejecuta pytest en push a `main` y en cada PR.

## Reglas del proyecto (no romper)
- SQL siempre parametrizado; nunca concatenar entradas.
- Compra: inventario, reserva y pago inicial van en una sola transacción `BEGIN IMMEDIATE`; si una escritura falla, rollback total. Cancelar devuelve cupos en la misma transacción y es idempotente.
- Reintentar solo `SQLITE_BUSY` y `SQLITE_LOCKED`; agotados los intentos, la API responde 503 con `Retry-After`.
- `POST /reservas` con `X-Idempotency-Key`: mismo cuerpo devuelve el mismo recibo; cuerpo distinto, 409. No alterar este comportamiento.
- Baja de paquetes siempre lógica; nunca borrar reservas, pagos ni historial. Las migraciones solo agregan columnas.
- Reservas internacionales y cruceros guardan la tasa USD/CLP y el precio al crearse; no recalcular después.
- Pagos simulados, sin pasarela real. Transición `pending` → `confirmed`/`failed`; un resultado terminal no se revierte (el opuesto da 409).
- Permisos: el cliente solo ve y cancela sus reservas; solo el administrador ve todas, gestiona paquetes, inventario y pagos. No debilitar chequeos de rol.
- Auth: sin secretos ni usuarios por defecto; contraseñas de ≥12 caracteres con bcrypt.
- Outbox de correo: `/admin/outbox` no expone destinatario ni contenido.
- Una sola instancia de `FxService` por aplicación (su circuito es por instancia).
- Nunca leer ni mostrar `AGENCIA_JWT_SECRET`, `AGENCIA_SMTP_PASSWORD` ni `.env`. En archivos `*.db`, solo consultas de lectura y agregadas (conteos); no volcar contenido, RUT ni hashes.
- Para leer una BD SQLite ábrela en solo lectura: `sqlite3.connect("file:RUTA?mode=ro", uri=True)`. Nunca `connect` directo: crea un `.db` vacío si no existe.
- Si la BD o la tabla no existe, responde «no determinable»; no devuelvas 0.
- No borres archivos sin preguntar, tampoco los que tú hayas creado.

## Cómo cambiar código aquí
- Bug: primero una prueba en `tests/` que falle, luego la corrección, luego la suite completa.
- Funcionalidad o cambio de endpoint/regla: criterios de aceptación en 3–5 líneas, prueba, implementación y actualizar su sección del README.
- Mientras desarrollas, corre solo el archivo de pruebas afectado; la suite completa, antes de cerrar.
- `main_api.py` y `services/compra_service.py` son muy grandes: busca con `Select-String` y lee por rangos; no los leas completos.
- No agregar dependencias sin autorización.

## Git
- Rama actual: `fix/errores-sqlite-cache`. No commit, push ni cambio de rama sin autorización.
- El árbol puede mostrar decenas de archivos «modificados» solo por fin de línea (CRLF). Para ver cambios reales usa `git diff --ignore-space-at-eol --stat`. No normalices fin de línea ni «arregles» esos archivos sin permiso.