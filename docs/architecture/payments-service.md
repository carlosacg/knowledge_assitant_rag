---
title: Arquitectura de payments-service
tech_stack: [fastapi, postgresql, aws]
doc_type: architecture
environment: all
last_updated: 2026-05-07
owner: payments-core
---

# Arquitectura de payments-service

`payments-service` es el servicio responsable de crear, capturar y reembolsar pagos de Nimbus Labs. Está escrito en FastAPI (Python 3.11), corre en AWS ECS Fargate (clúster `nimbus-prod`) y persiste en la base RDS PostgreSQL 15 `payments`. El procesamiento con tarjetas se delega al gateway externo **PayCore**.

## Componentes

- **API HTTP** (`app/api/v1/routers/`): routers `payments.py` y `refunds.py`.
- **Capa de servicios** (`app/services/`): `payment_service.py`, `refund_service.py`, `paycore_client.py`.
- **Persistencia**: SQLAlchemy 2.0 async con asyncpg, a través de RDS Proxy `payments-rds-proxy`.
- **Outbox**: los eventos se escriben en la tabla `outbox_events` dentro de la misma transacción que el cambio de estado; un worker (`outbox-publisher`) los publica en SNS usando `EVENT_BUS_TOPIC_ARN`.
- **Cliente PayCore**: `httpx.AsyncClient` compartido, timeout de 5 s, autenticado con `PAYCORE_API_KEY`.

## Endpoints

| Método | Ruta | Scope requerido | Descripción |
|---|---|---|---|
| `POST` | `/api/v1/payments` | `payments:write` | Crea y autoriza un pago |
| `GET` | `/api/v1/payments/{payment_id}` | `payments:read` | Consulta un pago |
| `POST` | `/api/v1/payments/refunds` | `payments:refund` | Solicita un reembolso |

Todos los endpoints de escritura (`POST`) requieren el header `Idempotency-Key`.

## Flujo de pago

1. El cliente llama `POST /api/v1/payments` con `Idempotency-Key`.
2. El servicio valida el JWT (scope `payments:write`) y la clave de idempotencia.
3. Para montos altos consulta a `ml-risk-scoring`.
4. Autoriza y captura contra PayCore.
5. En una única transacción persiste el pago en `payments` y el evento `payments.payment.captured` en `outbox_events`.
6. El `outbox-publisher` publica el evento en SNS; `ledger-service` y `notifications-service` lo consumen.

## Flujo de reembolso

El endpoint `POST /api/v1/payments/refunds` aplica las siguientes reglas de negocio:

- **Ventana de reembolso:** solo se permite reembolsar dentro de los **180 días** posteriores a la captura. Fuera de esa ventana se responde `REFUND_WINDOW_EXPIRED`.
- **Monto máximo:** el monto del reembolso debe ser ≤ monto capturado menos lo ya reembolsado. Si no, se responde `REFUND_AMOUNT_EXCEEDED`.
- **Aprobación manual:** los reembolsos de más de **5.000 USD** (`REFUND_APPROVAL_THRESHOLD_CENTS=500000`) quedan en estado `pending` y generan un registro en `refund_approvals`. Deben ser aprobados por un usuario con rol `finance-ops` antes de enviarse a PayCore.
- **Scope:** el token debe incluir `payments:refund`.

### Estados del reembolso

```text
pending --> processing --> succeeded
                      \--> failed
```

- `pending`: creado, a la espera de aprobación (si aplica) o de envío a PayCore.
- `processing`: enviado a PayCore, esperando confirmación.
- `succeeded` / `failed`: estados terminales.

Al crearse se emite `payments.refund.requested`; al confirmarse PayCore se emite `payments.refund.completed` (o `payments.refund.failed`).

### Ejemplo de request

```bash
curl -X POST https://api.nimbus.internal/api/v1/payments/refunds \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Idempotency-Key: 3f1c9a52-7d0e-4b8e-9a51-1c2d3e4f5a6b" \
  -H "Content-Type: application/json" \
  -d '{"payment_id": "8b7e...", "amount_cents": 125000, "currency": "USD", "reason": "customer_request"}'
```

### Implementación simplificada

```python
async def request_refund(session: AsyncSession, cmd: RefundRequest) -> Refund:
    payment = await payments_repo.get_for_update(session, cmd.payment_id)
    if payment.captured_at < utcnow() - timedelta(days=180):
        raise DomainError("REFUND_WINDOW_EXPIRED", "Refund window of 180 days has expired")

    refundable = payment.captured_amount_cents - payment.refunded_amount_cents
    if cmd.amount_cents > refundable:
        raise DomainError("REFUND_AMOUNT_EXCEEDED", "Refund exceeds refundable amount")

    refund = Refund(payment_id=payment.id, amount_cents=cmd.amount_cents,
                    currency=payment.currency, status="pending")
    session.add(refund)

    if cmd.amount_cents > settings.REFUND_APPROVAL_THRESHOLD_CENTS:
        session.add(RefundApproval(refund_id=refund.id, status="awaiting_approval"))

    session.add(OutboxEvent.build("payments.refund.requested", refund))
    await session.commit()
    return refund
```

## Idempotencia

- Header obligatorio `Idempotency-Key` (UUID recomendado) en todos los `POST`.
- Las claves se guardan **24 horas** en la tabla `idempotency_keys` junto con el hash del request y la respuesta.
- Si llega la misma clave con el mismo cuerpo, se devuelve la respuesta almacenada.
- Si llega la misma clave con un cuerpo distinto, se responde `409` con `IDEMPOTENCY_KEY_REUSED`.

## Eventos emitidos

| Evento | Cuándo |
|---|---|
| `payments.payment.captured` | Pago capturado en PayCore |
| `payments.payment.failed` | Autorización o captura rechazada |
| `payments.refund.requested` | Reembolso creado |
| `payments.refund.completed` | PayCore confirmó el reembolso |
| `payments.refund.failed` | PayCore rechazó el reembolso |

El formato del sobre está definido en `docs/architecture/event-bus.md`.

## Códigos de error

| Código | HTTP | Significado |
|---|---|---|
| `REFUND_WINDOW_EXPIRED` | 422 | Pasaron más de 180 días desde la captura |
| `REFUND_AMOUNT_EXCEEDED` | 422 | Monto mayor al reembolsable |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Clave reutilizada con otro cuerpo |
| `IDEMPOTENCY_KEY_MISSING` | 400 | Falta el header `Idempotency-Key` |
| `PAYMENT_NOT_FOUND` | 404 | `payment_id` inexistente |
| `INSUFFICIENT_SCOPE` | 403 | El token no tiene el scope requerido |
| `PAYCORE_UNAVAILABLE` | 503 | Timeout o error 5xx de PayCore |

Ejemplo de cuerpo de error estándar:

```json
{
  "error": {
    "code": "REFUND_WINDOW_EXPIRED",
    "message": "Refund window of 180 days has expired",
    "request_id": "c0a8012e-5b1f-4e2a-9d1b-7f3e2a1b0c9d"
  }
}
```

## Configuración

| Variable | Origen | Valor |
|---|---|---|
| `PAYMENTS_DB_URL` | Secrets Manager `prod/payments/db-url` (staging: `staging/payments/db-url`) | — |
| `PAYMENTS_DB_POOL_SIZE` | Task definition | prod 20, staging 5 |
| `PAYCORE_API_KEY` | Secrets Manager `prod/payments/paycore-api-key` | — |
| `EVENT_BUS_TOPIC_ARN` | Task definition | ARN del tópico SNS de pagos |
| `LOG_LEVEL` | Task definition | `INFO` en prod |
| `REFUND_APPROVAL_THRESHOLD_CENTS` | Task definition | `500000` |
