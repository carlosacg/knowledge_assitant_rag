---
title: Bus de eventos (SNS + SQS) y relay a GCP
tech_stack: [aws, gcp]
doc_type: architecture
environment: all
last_updated: 2026-04-22
owner: data-platform
---

# Bus de eventos (SNS + SQS) y relay a GCP

Nimbus Labs usa un bus de eventos basado en **AWS SNS** (tópicos por dominio) y **AWS SQS** (una cola por consumidor). Los eventos que interesan a analítica se reenvían a **GCP Pub/Sub** mediante `event-bridge-relay` y terminan en el dataset de BigQuery `analytics_events`.

## Topología

- Un tópico SNS por dominio: `nimbus-prod-payments-events`, `nimbus-prod-accounts-events`, `nimbus-prod-ledger-events`.
- Cada consumidor tiene su propia cola SQS suscrita al tópico, con filtros por `event_type` cuando corresponde.
- Cada cola tiene una DLQ asociada con el nombre `<queue>-dlq`.

```text
payments-service --(outbox)--> SNS nimbus-prod-payments-events
                                   |-> SQS ledger-payments-events        -> ledger-service
                                   |-> SQS notifications-payments-events -> event-bridge-relay -> Pub/Sub -> notifications-service
                                   |-> SQS relay-payments-events          -> event-bridge-relay -> Pub/Sub -> BigQuery analytics_events
```

## Convención de nombres de eventos

Formato: `<domain>.<entity>.<past_tense_verb>`, en minúsculas y con el verbo en pasado.

Ejemplos válidos:

- `payments.payment.captured`
- `payments.refund.requested`
- `payments.refund.completed`
- `accounts.customer.created`

Ejemplos inválidos: `payments.refund.complete` (no está en pasado), `PaymentCaptured` (no sigue el formato), `payments.capture` (falta la entidad).

## Sobre (envelope) del evento

Todo evento publicado debe tener estos campos:

| Campo | Tipo | Descripción |
|---|---|---|
| `event_id` | UUID | Identificador único; clave de idempotencia para consumidores |
| `event_type` | string | Nombre según la convención |
| `occurred_at` | string ISO-8601 UTC | Momento en que ocurrió el hecho |
| `producer` | string | Servicio que publica (`payments-service`) |
| `schema_version` | integer | Versión del esquema de `data` |
| `data` | object | Payload específico del evento |

```json
{
  "event_id": "1d7c3f0e-8a4b-4c2e-9f11-2b3c4d5e6f70",
  "event_type": "payments.refund.completed",
  "occurred_at": "2026-04-10T14:32:05Z",
  "producer": "payments-service",
  "schema_version": 2,
  "data": {
    "refund_id": "5e2f...",
    "payment_id": "8b7e...",
    "amount_cents": 125000,
    "currency": "USD",
    "status": "succeeded"
  }
}
```

Los cambios incompatibles en `data` requieren incrementar `schema_version` y mantener ambas versiones durante al menos un ciclo de despliegue de todos los consumidores.

## Configuración de colas SQS

| Parámetro | Valor |
|---|---|
| Visibility timeout | 60 s |
| `maxReceiveCount` (redrive a DLQ) | 5 |
| Nombre de la DLQ | `<queue>-dlq` |
| Retención de la DLQ | 14 días |

```hcl
resource "aws_sqs_queue" "ledger_payments_events_dlq" {
  name                      = "ledger-payments-events-dlq"
  message_retention_seconds = 1209600 # 14 días
}

resource "aws_sqs_queue" "ledger_payments_events" {
  name                       = "ledger-payments-events"
  visibility_timeout_seconds = 60
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ledger_payments_events_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sns_topic_subscription" "ledger_payments" {
  topic_arn            = aws_sns_topic.payments_events.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.ledger_payments_events.arn
  raw_message_delivery = true
}
```

## Consumidores idempotentes

SQS entrega **al menos una vez**, por lo que todo consumidor debe ser idempotente usando `event_id`. El patrón estándar es una tabla `processed_events` con `event_id` como clave primaria, insertada en la misma transacción que el efecto del evento.

```python
async def handle(session: AsyncSession, envelope: dict) -> None:
    inserted = await session.execute(
        text("INSERT INTO processed_events (event_id) VALUES (:id) "
             "ON CONFLICT (event_id) DO NOTHING RETURNING event_id"),
        {"id": envelope["event_id"]},
    )
    if inserted.first() is None:
        return  # duplicado: ya procesado
    await apply_effect(session, envelope["data"])
    await session.commit()
```

Si el procesamiento tarda más que el visibility timeout (60 s), el mensaje vuelve a ser visible y será reintentado; los handlers deben terminar bien por debajo de ese límite.

## Manejo de DLQ

Tras 5 recepciones fallidas el mensaje pasa a `<queue>-dlq`, donde se retiene 14 días. La alarma CloudWatch `<queue>-dlq-not-empty` notifica al equipo dueño. Para reprocesar:

```bash
aws sqs start-message-move-task \
  --source-arn arn:aws:sqs:us-east-1:123456789012:ledger-payments-events-dlq \
  --destination-arn arn:aws:sqs:us-east-1:123456789012:ledger-payments-events
```

Antes de reprocesar, corregir la causa raíz; de lo contrario los mensajes volverán a la DLQ.

## event-bridge-relay

`event-bridge-relay` consume colas SQS dedicadas y publica cada mensaje, sin modificar el sobre, en tópicos de GCP Pub/Sub del proyecto `nimbus-analytics-prod`. Se autentica en GCP mediante federación de identidad de cargas de trabajo (Workload Identity Federation) desde el rol IAM de la tarea ECS; no usa claves JSON de cuenta de servicio. Una suscripción BigQuery de Pub/Sub escribe los eventos en el dataset `analytics_events`, particionado por `occurred_at`.
