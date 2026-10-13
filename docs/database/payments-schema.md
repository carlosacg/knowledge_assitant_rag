---
title: Esquema de la base de datos payments
tech_stack: [postgresql]
doc_type: schema
environment: all
last_updated: 2026-06-25
owner: payments-core
---

# Esquema de la base de datos payments

La base `payments` (RDS PostgreSQL 15) es propiedad exclusiva de `payments-service`. Ningún otro servicio se conecta a ella; los demás reciben los cambios a través de eventos publicados desde la tabla `outbox_events`. El esquema sigue `docs/database/postgresql-conventions.md`.

## Tablas

| Tabla | Propósito |
|---|---|
| `payments` | Un registro por pago autorizado/capturado en PayCore |
| `refunds` | Reembolsos asociados a un pago |
| `refund_approvals` | Aprobaciones manuales de reembolsos de más de 5.000 USD |
| `idempotency_keys` | Claves de idempotencia de los `POST`, retenidas 24 h |
| `outbox_events` | Eventos pendientes de publicar en SNS (patrón outbox) |

## DDL completo

```sql
-- Extensiones
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =====================================================================
-- payments
-- =====================================================================
CREATE TABLE payments (
    id                      UUID        NOT NULL DEFAULT gen_random_uuid(),
    merchant_id             UUID        NOT NULL,
    customer_id             UUID        NOT NULL,
    amount_cents            BIGINT      NOT NULL,
    captured_amount_cents   BIGINT      NOT NULL DEFAULT 0,
    refunded_amount_cents   BIGINT      NOT NULL DEFAULT 0,
    currency                CHAR(3)     NOT NULL,
    status                  TEXT        NOT NULL,
    paycore_charge_id       TEXT,
    risk_score              SMALLINT,
    captured_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_payments PRIMARY KEY (id),
    CONSTRAINT ck_payments__amount_positive CHECK (amount_cents > 0),
    CONSTRAINT ck_payments__refunded_le_captured
        CHECK (refunded_amount_cents <= captured_amount_cents),
    CONSTRAINT ck_payments__status CHECK (
        status IN ('authorized', 'captured', 'failed', 'voided',
                   'partially_refunded', 'refunded')
    )
);

CREATE UNIQUE INDEX uq_payments__paycore_charge_id
    ON payments (paycore_charge_id) WHERE paycore_charge_id IS NOT NULL;
CREATE INDEX ix_payments__merchant_id_created_at
    ON payments (merchant_id, created_at DESC);
CREATE INDEX ix_payments__customer_id
    ON payments (customer_id);
CREATE INDEX ix_payments__status
    ON payments (status) WHERE status IN ('authorized', 'captured');

-- =====================================================================
-- refunds
-- =====================================================================
CREATE TABLE refunds (
    id                  UUID        NOT NULL DEFAULT gen_random_uuid(),
    payment_id          UUID        NOT NULL,
    amount_cents        BIGINT      NOT NULL,
    currency            CHAR(3)     NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'pending',
    reason              TEXT        NOT NULL,
    paycore_refund_id   TEXT,
    failure_code        TEXT,
    requested_by        TEXT        NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_refunds PRIMARY KEY (id),
    CONSTRAINT fk_refunds__payment_id
        FOREIGN KEY (payment_id) REFERENCES payments (id),
    CONSTRAINT ck_refunds__amount_positive CHECK (amount_cents > 0),
    CONSTRAINT ck_refunds__status CHECK (
        status IN ('pending', 'processing', 'succeeded', 'failed')
    )
);

CREATE INDEX ix_refunds__payment_id ON refunds (payment_id);
CREATE INDEX ix_refunds__status_created_at
    ON refunds (status, created_at) WHERE status IN ('pending', 'processing');
CREATE UNIQUE INDEX uq_refunds__paycore_refund_id
    ON refunds (paycore_refund_id) WHERE paycore_refund_id IS NOT NULL;

-- =====================================================================
-- refund_approvals
-- =====================================================================
CREATE TABLE refund_approvals (
    id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    refund_id       UUID        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'awaiting_approval',
    approver_id     TEXT,
    approver_role   TEXT,
    decision_note   TEXT,
    decided_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_refund_approvals PRIMARY KEY (id),
    CONSTRAINT fk_refund_approvals__refund_id
        FOREIGN KEY (refund_id) REFERENCES refunds (id),
    CONSTRAINT uq_refund_approvals__refund_id UNIQUE (refund_id),
    CONSTRAINT ck_refund_approvals__status CHECK (
        status IN ('awaiting_approval', 'approved', 'rejected')
    ),
    CONSTRAINT ck_refund_approvals__role CHECK (
        approver_role IS NULL OR approver_role = 'finance-ops'
    )
);

CREATE INDEX ix_refund_approvals__status_created_at
    ON refund_approvals (status, created_at);

-- =====================================================================
-- idempotency_keys
-- =====================================================================
CREATE TABLE idempotency_keys (
    id               UUID        NOT NULL DEFAULT gen_random_uuid(),
    key              TEXT        NOT NULL,
    client_id        TEXT        NOT NULL,
    request_path     TEXT        NOT NULL,
    request_hash     CHAR(64)    NOT NULL,
    response_status  SMALLINT,
    response_body    JSONB,
    locked_at        TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '24 hours',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_idempotency_keys PRIMARY KEY (id),
    CONSTRAINT uq_idempotency_keys__client_id_key UNIQUE (client_id, key)
);

CREATE INDEX ix_idempotency_keys__expires_at ON idempotency_keys (expires_at);

-- =====================================================================
-- outbox_events
-- =====================================================================
CREATE TABLE outbox_events (
    id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    event_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    event_type      TEXT        NOT NULL,
    schema_version  SMALLINT    NOT NULL DEFAULT 1,
    aggregate_id    UUID        NOT NULL,
    payload         JSONB       NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_outbox_events PRIMARY KEY (id),
    CONSTRAINT uq_outbox_events__event_id UNIQUE (event_id)
);

CREATE INDEX ix_outbox_events__unpublished
    ON outbox_events (created_at) WHERE published_at IS NULL;
CREATE INDEX ix_outbox_events__aggregate_id ON outbox_events (aggregate_id);
```

## Notas por tabla

### payments

- `captured_amount_cents` y `refunded_amount_cents` permiten calcular el monto reembolsable: `captured_amount_cents - refunded_amount_cents`. El check `ck_payments__refunded_le_captured` es la última línea de defensa contra `REFUND_AMOUNT_EXCEEDED`.
- `captured_at` se usa para la ventana de reembolso de 180 días.

### refunds

Estados permitidos: `pending`, `processing`, `succeeded`, `failed`. `requested_by` guarda el `sub` del token que solicitó el reembolso.

### refund_approvals

Una fila por reembolso que supera `REFUND_APPROVAL_THRESHOLD_CENTS` (500000, es decir 5.000 USD). Solo puede decidirla un usuario con rol `finance-ops`.

```sql
-- Reembolsos esperando aprobación de finance-ops
SELECT r.id, r.amount_cents, r.currency, ra.created_at
FROM refund_approvals ra
JOIN refunds r ON r.id = ra.refund_id
WHERE ra.status = 'awaiting_approval'
ORDER BY ra.created_at;
```

### idempotency_keys

Las claves expiran a las 24 horas (`expires_at`). Un job programado (`idempotency-keys-cleanup`, cada hora) borra filas vencidas en lotes de 5.000:

```sql
DELETE FROM idempotency_keys
WHERE id IN (
    SELECT id FROM idempotency_keys
    WHERE expires_at < now()
    LIMIT 5000
);
```

### outbox_events

El `outbox-publisher` lee filas con `published_at IS NULL` usando `FOR UPDATE SKIP LOCKED`, las publica en SNS y marca `published_at`. El `event_id` de esta tabla es el `event_id` del sobre del evento. Las filas publicadas se purgan a los 7 días.

```sql
SELECT id, event_id, event_type, payload
FROM outbox_events
WHERE published_at IS NULL
ORDER BY created_at
LIMIT 100
FOR UPDATE SKIP LOCKED;
```
