---
title: Convenciones de PostgreSQL
tech_stack: [postgresql, django, fastapi]
doc_type: standard
environment: all
last_updated: 2026-04-09
owner: db-guild
---

# Convenciones de PostgreSQL

Convenciones obligatorias para todas las bases PostgreSQL de Nimbus Labs (RDS en AWS y Cloud SQL en GCP). Las define y mantiene el DB guild. Cualquier excepción requiere aprobación del DB guild en el PR.

## Nomenclatura

- Tablas en **snake_case y en plural**: `payments`, `refund_approvals`, `idempotency_keys`.
- Columnas en snake_case singular.
- Claves foráneas: `<entidad>_id` (por ejemplo `payment_id`).
- Índices: `ix_<table>__<columns>` (doble guion bajo entre tabla y columnas), por ejemplo `ix_payments__merchant_id_created_at`.
- Índices / constraints únicos: `uq_<table>__<columns>`.
- Claves foráneas: `fk_<table>__<column>`.
- Checks: `ck_<table>__<descripcion>`.

## Columnas estándar

Toda tabla debe tener:

```sql
id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
```

- La clave primaria es siempre `id UUID DEFAULT gen_random_uuid()`. No se usan `SERIAL` ni `BIGSERIAL` en tablas nuevas.
- Las fechas siempre son `TIMESTAMPTZ`, nunca `TIMESTAMP` sin zona.

## Dinero

El dinero se almacena **siempre** como `amount_cents BIGINT` + `currency CHAR(3)` (código ISO 4217). **Nunca** se usa `FLOAT`, `REAL`, `DOUBLE PRECISION` ni `NUMERIC` para montos.

```sql
amount_cents  BIGINT  NOT NULL CHECK (amount_cents >= 0),
currency      CHAR(3) NOT NULL
```

En Python, los montos viajan como `int` en centavos; la conversión a unidades para mostrar se hace solo en la capa de presentación.

## Timeouts

| Contexto | Parámetro | Valor |
|---|---|---|
| Aplicación | `statement_timeout` | 5 s |
| Migraciones | `lock_timeout` | 3 s |

Una migración que no obtiene el lock en 3 s falla y se reintenta, en lugar de bloquear el tráfico de producción.

## Migraciones zero-downtime

Las migraciones se ejecutan con **Alembic** en servicios FastAPI y con **Django migrations** en servicios Django. Toda migración debe ser compatible con la versión anterior del código, porque durante el rolling deploy conviven ambas versiones.

### Agregar una columna NOT NULL

1. Agregar la columna como **nullable** (sin default volátil).
2. Desplegar código que escriba la columna.
3. **Backfill en lotes de 5.000 filas**.
4. Agregar el constraint como **`NOT VALID`**.
5. Ejecutar **`VALIDATE CONSTRAINT`** en una migración separada.

```sql
SET lock_timeout = '3s';

-- Paso 1
ALTER TABLE payments ADD COLUMN channel TEXT;

-- Paso 3 (repetir hasta 0 filas)
UPDATE payments SET channel = 'api'
WHERE id IN (SELECT id FROM payments WHERE channel IS NULL LIMIT 5000);

-- Paso 4
ALTER TABLE payments
    ADD CONSTRAINT ck_payments__channel_not_null CHECK (channel IS NOT NULL) NOT VALID;

-- Paso 5
ALTER TABLE payments VALIDATE CONSTRAINT ck_payments__channel_not_null;
```

### Índices

`CREATE INDEX CONCURRENTLY` es **obligatorio en tablas con más de 1 millón de filas**. No puede ejecutarse dentro de una transacción.

```python
# Alembic
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_refunds__status_created_at",
            "refunds",
            ["status", "created_at"],
            postgresql_concurrently=True,
        )
```

En Django se usa `AddIndexConcurrently` con `atomic = False` (ver `docs/backend/django-standards.md`).

### Operaciones prohibidas en caliente

- `ALTER TABLE ... ALTER COLUMN TYPE` en tablas grandes (reescribe la tabla).
- Renombrar columnas o tablas en un solo paso: usar expand/contract.
- `ADD COLUMN ... DEFAULT now()` u otros defaults volátiles.
- `CREATE INDEX` sin `CONCURRENTLY` en tablas > 1M filas.

## Consultas

- SQL crudo desde código de aplicación solo con aprobación del DB guild.
- Toda consulta nueva en caminos críticos debe revisarse con `EXPLAIN (ANALYZE, BUFFERS)` en staging.
- Paginación por cursor (keyset), no `OFFSET`.

```sql
SELECT id, amount_cents, currency, created_at
FROM payments
WHERE merchant_id = $1
  AND (created_at, id) < ($2, $3)
ORDER BY created_at DESC, id DESC
LIMIT 50;
```

## Conexiones

- En AWS, los servicios se conectan a través de **RDS Proxy** (por ejemplo `payments-rds-proxy`).
- Tamaño del pool configurado por servicio (por ejemplo `PAYMENTS_DB_POOL_SIZE`).
- Django usa `CONN_MAX_AGE=60`.
