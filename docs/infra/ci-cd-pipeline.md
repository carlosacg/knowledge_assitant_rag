---
title: Pipeline de CI/CD
tech_stack: [aws, python]
doc_type: deployment
environment: all
last_updated: 2026-08-28
owner: platform-infra
---

# Pipeline de CI/CD

Todos los servicios de Nimbus Labs usan **GitHub Actions**. Cada repositorio de servicio tiene dos workflows: `ci.yml` (validación) y `deploy.yml` (build y despliegue).

## Resumen del flujo

```text
PR abierto ──> ci.yml (ruff, mypy, pytest) ──> preview pr-<number>.staging.nimbus.internal
merge a main ──> deploy.yml ──> build + push ECR (tag = git SHA)
                             ──> staging: migrate + rolling deploy (automático)
                             ──> production: aprobación manual (environment "production")
                                             ──> migrate + rolling deploy
```

## ci.yml

Se ejecuta en cada pull request y en cada push a `main`. Pasos obligatorios:

1. **ruff** — lint y verificación de formato.
2. **mypy** — chequeo de tipos.
3. **pytest** — tests con cobertura mínima de 85%.

```yaml
name: ci
on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
        ports: ["5432:5432"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements-dev.txt
      - run: ruff check . && ruff format --check .
      - run: mypy app
      - run: pytest --cov=app --cov-fail-under=85
```

Un PR no puede mergearse si `ci.yml` falla (branch protection en `main`).

## deploy.yml

### Imágenes

- Se publican en ECR: **`123456789012.dkr.ecr.us-east-1.amazonaws.com/<service>`**.
- El tag es siempre el **SHA de git** del commit. **Nunca se usa `latest`**.

### Staging

Todo **merge a `main` despliega automáticamente a staging** (clúster `nimbus-staging`).

### Producción

El despliegue a producción requiere **aprobación manual** a través del **GitHub environment `production`**, que exige la aprobación de un miembro del equipo dueño del servicio.

### Migraciones

Antes del rolling deploy se ejecutan las migraciones como una **tarea one-off de ECS llamada `migrate`**, usando la misma imagen. Si `migrate` falla, el despliegue se aborta y el servicio sigue con la versión anterior.

```yaml
name: deploy
on:
  push:
    branches: [main]

permissions:
  id-token: write
  contents: read

env:
  SERVICE: payments-service
  ECR: 123456789012.dkr.ecr.us-east-1.amazonaws.com

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789012:role/github-deploy
          aws-region: us-east-1
      - uses: aws-actions/amazon-ecr-login@v2
      - run: |
          docker build -t $ECR/$SERVICE:${{ github.sha }} .
          docker push $ECR/$SERVICE:${{ github.sha }}

  staging:
    needs: build
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - run: nimbus deploy migrate --cluster nimbus-staging --service $SERVICE --image-tag ${{ github.sha }}
      - run: nimbus deploy rollout --cluster nimbus-staging --service $SERVICE --image-tag ${{ github.sha }}

  production:
    needs: staging
    runs-on: ubuntu-latest
    environment: production   # requiere aprobación manual
    steps:
      - run: nimbus deploy check-freeze
      - run: nimbus deploy migrate --cluster nimbus-prod --service $SERVICE --image-tag ${{ github.sha }}
      - run: nimbus deploy rollout --cluster nimbus-prod --service $SERVICE --image-tag ${{ github.sha }}
```

La autenticación en AWS se hace con OIDC (rol `github-deploy`); no hay credenciales de larga duración en GitHub.

## Rollback

### Automático

Los servicios ECS tienen habilitado el **deployment circuit breaker con rollback automático**: si las nuevas tareas no pasan los health checks, ECS revierte a la task definition anterior sin intervención.

### Manual

Si el problema se detecta después de que el deploy se completó (por ejemplo, aumento de errores 5xx):

```bash
# 1. Ver revisiones recientes de la task definition
aws ecs list-task-definitions --family-prefix payments-service --sort DESC --max-items 5

# 2. Hacer rollback a la revisión anterior conocida como buena
nimbus deploy rollback --service payments-service --to 142
```

El comando es `nimbus deploy rollback --service <name> --to <task-def-revision>`. Un rollback **no revierte migraciones**; por eso las migraciones deben ser compatibles hacia atrás (ver `docs/database/postgresql-conventions.md`).

## Deploy freeze

Hay **congelamiento de despliegues a producción los viernes después de las 15:00 (UTC-3)**. El paso `nimbus deploy check-freeze` bloquea el job `production` durante la ventana. Los hotfixes durante el freeze requieren aprobación del incident commander y se registran en `#deploys`.

## Buenas prácticas

- Un PR = un cambio desplegable. Evitar mezclar migraciones destructivas con cambios de código.
- Vigilar dashboards y errores durante 15 minutos después de cada deploy a producción.
- No re-taggear imágenes: si hay que redeployar, usar el mismo SHA.
