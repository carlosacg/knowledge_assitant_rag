---
title: Visión general de la plataforma Nimbus Labs
tech_stack: [aws, gcp, fastapi, django, postgresql]
doc_type: architecture
environment: all
last_updated: 2026-03-18
owner: platform-architecture
---

# Visión general de la plataforma Nimbus Labs

Este documento describe la arquitectura de alto nivel de la plataforma de Nimbus Labs: qué servicios existen, dónde corren, cómo se comunican y qué documentos detallan cada pieza. Es el punto de entrada recomendado para personas que se incorporan a cualquier equipo de backend o infraestructura.

## Principios de arquitectura

1. **Servicios pequeños con dueño claro.** Cada servicio tiene un equipo propietario y una base de datos propia; ningún servicio lee directamente la base de datos de otro.
2. **Comunicación asíncrona por defecto.** Los cambios de estado relevantes se publican como eventos en el bus de eventos (AWS SNS + SQS). Las llamadas HTTP síncronas entre servicios se reservan para lecturas que requieren consistencia inmediata.
3. **Multi-nube con propósito.** El núcleo transaccional (pagos, cuentas, ledger) vive en AWS; la analítica, las notificaciones y el scoring de riesgo con ML viven en GCP.
4. **Infraestructura como código.** Todo recurso de nube se declara en Terraform en el repositorio `nimbus-infra`. No se aceptan cambios manuales en consola salvo durante un incidente, y deben reconciliarse después.
5. **Dinero como enteros.** Todos los montos se representan como `amount_cents BIGINT` + `currency CHAR(3)`; nunca float.

## Catálogo de servicios

| Servicio | Stack | Nube / runtime | Base de datos | Equipo |
|---|---|---|---|---|
| `payments-service` | FastAPI, Python 3.11 | AWS ECS Fargate | RDS PostgreSQL 15 `payments` | payments-core |
| `accounts-service` | Django 4.2 LTS + DRF | AWS ECS Fargate | RDS PostgreSQL `accounts` | accounts-team |
| `ledger-service` | FastAPI | AWS ECS Fargate | RDS PostgreSQL `ledger` | ledger-team |
| `notifications-service` | FastAPI | GCP Cloud Run (`nimbus-analytics-prod`, us-central1) | Cloud SQL PostgreSQL 15 `notifications-db` | engagement-team |
| `ml-risk-scoring` | Python | GCP GKE Autopilot `risk-scoring-gke` | — | risk-ml |
| `event-bridge-relay` | Python | AWS ECS Fargate | — | data-platform |
| `nimbus-auth` | FastAPI | AWS ECS Fargate | RDS PostgreSQL `auth` | identity-team |

### payments-service

Orquesta la creación, captura y reembolso de pagos contra el gateway externo **PayCore**. Expone `POST /api/v1/payments`, `GET /api/v1/payments/{payment_id}` y `POST /api/v1/payments/refunds`. Ver `docs/architecture/payments-service.md`.

### accounts-service

Gestiona clientes, comercios, datos KYC y configuración de cuentas. Es el único servicio Django del núcleo; sigue los estándares de `docs/backend/django-standards.md`.

### ledger-service

Mantiene un libro contable de **partida doble** (double-entry). Consume eventos de pagos como `payments.payment.captured` y `payments.refund.completed` y genera los asientos correspondientes. Nunca se modifica un asiento: las correcciones se hacen con asientos de reversa.

### notifications-service

Envía correos, SMS y push a clientes finales a partir de eventos de dominio. Corre en Cloud Run con mínimo 2 instancias, máximo 50 y concurrencia 80 por instancia. Ver `docs/infra/gcp-deployment.md`.

### ml-risk-scoring

Modelo de scoring de riesgo de fraude. Corre en el clúster GKE Autopilot `risk-scoring-gke` y es consultado por `payments-service` antes de capturar pagos de alto monto.

### event-bridge-relay

Reenvía eventos del bus de AWS (SNS/SQS) hacia GCP Pub/Sub, desde donde se cargan en el dataset de BigQuery `analytics_events`. Ver `docs/architecture/event-bus.md`.

### nimbus-auth

Servicio interno de identidad. Emite JWT firmados con RS256 y tokens de servicio vía OAuth2 client credentials. Ver `docs/backend/api-authentication.md`.

## Diagrama lógico

```text
                 +-----------------+
 clientes ---->  |  API Gateway /  |  JWT (nimbus-auth)
                 |  ALB (AWS)      |
                 +--------+--------+
                          |
       +------------------+-------------------+
       |                  |                   |
+------v------+   +-------v-------+   +-------v-------+
| payments-   |   | accounts-     |   | nimbus-auth   |
| service     |   | service       |   |               |
+------+------+   +-------+-------+   +---------------+
       | eventos (SNS)    |
       v                  v
+---------------------------------+        +----------------------+
|  Bus de eventos AWS SNS + SQS   | -----> | event-bridge-relay   |
+------+--------------------------+        +----------+-----------+
       |                                              | Pub/Sub (GCP)
+------v------+                          +------------v-----------+
| ledger-     |                          | notifications-service  |
| service     |                          | BigQuery analytics_    |
+-------------+                          | events                 |
                                         +------------------------+
```

## Regiones y entornos

- **AWS producción:** región primaria `us-east-1`, región de recuperación ante desastres (DR) `us-west-2`. Clúster ECS `nimbus-prod`.
- **AWS staging:** clúster ECS `nimbus-staging`, con entornos de preview efímeros por PR en `pr-<number>.staging.nimbus.internal`. Ver `docs/infra/aws-staging-environment.md`.
- **GCP producción:** proyecto `nimbus-analytics-prod`, región `us-central1`.

## Estándares transversales

- Servicios FastAPI: `docs/backend/fastapi-standards.md`.
- Servicios Django: `docs/backend/django-standards.md`.
- Bases de datos: `docs/database/postgresql-conventions.md`.
- CI/CD: `docs/infra/ci-cd-pipeline.md`.
- Incidentes de base de datos: `docs/infra/runbook-database-incidents.md`.

Todos los servicios exponen `/healthz` (liveness) y `/readyz` (readiness), propagan el header `X-Request-ID` y emiten logs JSON a stdout.

## Ejemplo: contrato mínimo de un servicio nuevo

```yaml
service:
  name: example-service
  owner: example-team
  runtime: aws-ecs-fargate        # o gcp-cloud-run
  language: python3.11
  framework: fastapi
  database:
    engine: postgresql15
    name: example
  health:
    liveness: /healthz
    readiness: /readyz
  events:
    publishes:
      - example.widget.created
    consumes:
      - payments.payment.captured
  oncall:
    pagerduty_service: example-service
```

Todo servicio nuevo debe registrar este contrato en el catálogo y pasar una revisión de arquitectura antes de su primer despliegue a producción.
