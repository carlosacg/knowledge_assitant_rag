---
title: Estándares de desarrollo FastAPI
tech_stack: [fastapi, python, postgresql]
doc_type: standard
environment: all
last_updated: 2026-05-20
owner: backend-guild
---

# Estándares de desarrollo FastAPI

Aplica a todos los servicios FastAPI de Nimbus Labs: `payments-service`, `ledger-service`, `notifications-service` y `nimbus-auth`. Python 3.11, Pydantic v2 y SQLAlchemy 2.0 async son obligatorios.

## Estructura del proyecto

```text
app/
├── main.py
├── api/
│   └── v1/
│       └── routers/
│           ├── payments.py
│           └── refunds.py
├── core/
│   ├── config.py        # pydantic-settings
│   ├── errors.py
│   └── http.py          # httpx.AsyncClient compartido
├── db/
│   ├── session.py       # engine + get_db_session
│   └── models/
├── services/            # lógica de negocio
└── schemas/             # modelos Pydantic v2 de request/response
alembic/
tests/
```

- Routers en `app/api/v1/routers/`. Todas las rutas públicas se versionan bajo `/api/v1`.
- Configuración en `app/core/config.py` con **pydantic-settings**.
- Sesión de base de datos en `app/db/session.py`.
- Lógica de negocio en `app/services/`; los routers solo orquestan.
- Schemas Pydantic v2 en `app/schemas/`.

## Configuración

```python
# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PAYMENTS_DB_URL: str
    PAYMENTS_DB_POOL_SIZE: int = 5
    PAYCORE_API_KEY: str
    EVENT_BUS_TOPIC_ARN: str
    LOG_LEVEL: str = "INFO"
    REFUND_APPROVAL_THRESHOLD_CENTS: int = 500_000


settings = Settings()
```

Los secretos nunca tienen valor por defecto en código; en AWS se inyectan desde Secrets Manager y en GCP desde Secret Manager.

## Base de datos: SQLAlchemy 2.0 async

- Driver **asyncpg**.
- La dependencia `get_db_session` entrega un `AsyncSession` por request.
- Migraciones con **Alembic**.

```python
# app/db/session.py
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.PAYMENTS_DB_URL,
    pool_size=settings.PAYMENTS_DB_POOL_SIZE,
    pool_pre_ping=True,
    connect_args={"server_settings": {"statement_timeout": "5000"}},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

## Routers delgados

```python
# app/api/v1/routers/refunds.py
router = APIRouter(prefix="/api/v1/payments", tags=["refunds"])


@router.post("/refunds", response_model=RefundOut, status_code=201)
async def create_refund(
    body: RefundIn,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    principal: Annotated[Principal, Security(require_scopes, scopes=["payments:refund"])],
) -> RefundOut:
    refund = await refund_service.request_refund(session, body, idempotency_key)
    return RefundOut.model_validate(refund)
```

## Formato de errores

Todos los errores siguen el cuerpo estándar:

```json
{"error": {"code": "REFUND_AMOUNT_EXCEEDED", "message": "Refund exceeds refundable amount", "request_id": "c0a8012e-..."}}
```

```python
@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message,
                           "request_id": request.state.request_id}},
    )
```

Nunca se devuelven stack traces ni mensajes de excepciones de la base de datos al cliente.

## Health checks

- `/healthz` — **liveness**: responde 200 si el proceso está vivo. No consulta dependencias.
- `/readyz` — **readiness**: verifica conexión a la base de datos (`SELECT 1`) y dependencias críticas. Si falla, el balanceador deja de enviar tráfico.

Ambos endpoints quedan fuera de `/api/v1` y no requieren autenticación.

## HTTP saliente

- Usar un único `httpx.AsyncClient` compartido creado en el `lifespan` de la app, con **timeout de 5 s**.
- Prohibido crear un cliente por request.
- Propagar siempre el header `X-Request-ID`.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    yield
    await app.state.http.aclose()
```

## Concurrencia: nada bloqueante en endpoints async

Dentro de un endpoint `async def` está prohibido hacer llamadas bloqueantes: `requests`, `time.sleep`, `psycopg2`, `boto3` síncrono o lectura de archivos grandes. Si una librería es síncrona, usar `run_in_threadpool` o declarar el endpoint con `def`.

## X-Request-ID

Un middleware lee `X-Request-ID` del request entrante (o genera un UUID si no existe), lo guarda en `request.state.request_id`, lo agrega a todos los logs y lo devuelve en la respuesta. Las llamadas salientes y los eventos publicados lo propagan.

## Testing y calidad

- `pytest` + `pytest-asyncio`, con base de datos PostgreSQL real en contenedor.
- `ruff` para lint y formato, `mypy --strict` en `app/`.
- Cobertura mínima de 85%, igual que en Django.
