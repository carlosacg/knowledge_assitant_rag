---
title: Entorno de staging en AWS
tech_stack: [aws, fastapi]
doc_type: deployment
environment: staging
last_updated: 2026-07-02
owner: platform-infra
---

# Entorno de staging en AWS

Staging replica la topología de producción a menor escala y con integraciones de prueba. Su propósito es validar cambios antes de producción y alojar los entornos de preview por pull request.

## Diferencias con producción

| Aspecto | Producción | Staging |
|---|---|---|
| Clúster ECS | `nimbus-prod` | `nimbus-staging` |
| Tareas de payments-service | min 4, max 20 | 1 a 2 tareas |
| RDS | `db.r6g.xlarge` Multi-AZ | `db.t4g.medium` single-AZ |
| Retención de backups | 14 días | 3 días |
| `PAYMENTS_DB_POOL_SIZE` | 20 | 5 |
| Secreto de base de datos | `prod/payments/db-url` | `staging/payments/db-url` |
| Gateway de pagos | PayCore producción | **PayCore sandbox** |
| Despliegue | aprobación manual | automático al merge a `main` |

## Base de datos

- RDS PostgreSQL 15, instancia `db.t4g.medium`, **single-AZ**.
- Backups automáticos con **retención de 3 días**.
- Los datos son sintéticos. Está prohibido copiar datos de producción a staging, incluso anonimizados, sin aprobación de seguridad.

## PayCore sandbox

Staging usa el entorno **sandbox de PayCore**. La API key de sandbox está en Secrets Manager `staging/payments/paycore-api-key`. Las tarjetas de prueba documentadas por PayCore permiten simular aprobaciones, rechazos y timeouts.

```bash
aws secretsmanager get-secret-value \
  --secret-id staging/payments/db-url \
  --query SecretString --output text
```

## Feature flags con flagd

Los feature flags se gestionan con **`flagd`**, el servicio interno de feature flags (similar a LaunchDarkly). En staging los flags pueden activarse libremente; en producción los cambios de flags requieren un ticket.

```python
from app.core.flags import flags

if await flags.is_enabled("refunds.auto_approve_small", context={"merchant_id": merchant_id}):
    ...
```

```yaml
# flagd: flags/payments.yaml
flags:
  refunds.auto_approve_small:
    state: ENABLED
    variants:
      on: true
      off: false
    defaultVariant: off
    targeting:
      if:
        - in: [{ var: environment }, ["staging"]]
        - on
        - off
```

## Entornos de preview por PR

Cada pull request a un servicio desplegable crea un **entorno efímero** en el clúster `nimbus-staging`:

- URL: **`pr-<number>.staging.nimbus.internal`** (por ejemplo `pr-1482.staging.nimbus.internal`).
- Se crea automáticamente al abrir el PR y se actualiza con cada push.
- Se **destruye automáticamente a las 72 horas** de su creación, o al cerrar/mergear el PR, lo que ocurra primero.
- Usa un esquema PostgreSQL propio (`pr_<number>`) dentro de la instancia RDS de staging, con migraciones aplicadas al crearse.
- Comparte el bus de eventos de staging, con colas SQS prefijadas `pr-<number>-`.

```yaml
# .github/workflows/preview.yml (extracto)
on:
  pull_request:
    types: [opened, synchronize, reopened, closed]

jobs:
  preview:
    if: github.event.action != 'closed'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          nimbus preview up \
            --service payments-service \
            --pr ${{ github.event.number }} \
            --image-tag ${{ github.sha }} \
            --ttl 72h
```

Para extender la vida de un preview más allá de 72 h se debe re-desplegar (`nimbus preview up` con el mismo PR); no existen previews permanentes.

## Despliegue a staging

Todo merge a `main` dispara `deploy.yml`, que construye la imagen, la publica en ECR con el SHA de git, corre la tarea `migrate` en `nimbus-staging` y actualiza el servicio. No requiere aprobación manual. Ver `docs/infra/ci-cd-pipeline.md`.

## Acceso

- Acceso a la red interna de staging vía VPN corporativa.
- Lectura de logs en CloudWatch: grupo `/ecs/nimbus-staging/<service>`.
- Shell en contenedores con ECS Exec:

```bash
aws ecs execute-command --cluster nimbus-staging \
  --task <task-id> --container payments-service \
  --interactive --command "/bin/sh"
```
