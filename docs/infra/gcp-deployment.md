---
title: Despliegue en GCP
tech_stack: [gcp, fastapi, postgresql]
doc_type: deployment
environment: production
last_updated: 2026-09-04
owner: platform-infra
---

# Despliegue en GCP

En GCP corren las cargas de analítica, notificaciones y scoring de riesgo. Todo vive en el proyecto **`nimbus-analytics-prod`**, región **`us-central1`**, y se gestiona con Terraform en `nimbus-infra/envs/gcp-prod`.

## Servicios en GCP

| Servicio | Runtime | Datos |
|---|---|---|
| `notifications-service` | Cloud Run | Cloud SQL PostgreSQL 15 `notifications-db` |
| `ml-risk-scoring` | GKE Autopilot, clúster `risk-scoring-gke` | — |
| Pipeline de analítica | Pub/Sub + BigQuery | dataset `analytics_events` |

## Reglas de seguridad obligatorias

- **Workload Identity es obligatorio.** Las claves JSON de cuentas de servicio están **prohibidas**; una política de organización (`iam.disableServiceAccountKeyCreation`) bloquea su creación.
- Los secretos se almacenan en **GCP Secret Manager** y se montan como variables de entorno en Cloud Run o vía CSI en GKE.
- Las imágenes solo se pueden desplegar desde **Artifact Registry `us-central1-docker.pkg.dev/nimbus-analytics-prod/services`**.

## notifications-service en Cloud Run

Configuración de escalado:

- **Mínimo 2 instancias** (evita cold starts).
- **Máximo 50 instancias**.
- **Concurrencia 80** requests por instancia.
- 1 vCPU, 1 GiB de memoria.

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: notifications-service
  annotations:
    run.googleapis.com/ingress: internal
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "2"
        autoscaling.knative.dev/maxScale: "50"
        run.googleapis.com/cloudsql-instances: nimbus-analytics-prod:us-central1:notifications-db
    spec:
      serviceAccountName: notifications-sa@nimbus-analytics-prod.iam.gserviceaccount.com
      containerConcurrency: 80
      containers:
        - image: us-central1-docker.pkg.dev/nimbus-analytics-prod/services/notifications-service:3e9a1f2
          ports:
            - containerPort: 8000
          resources:
            limits:
              cpu: "1"
              memory: 1Gi
          env:
            - name: NOTIFICATIONS_DB_URL
              valueFrom:
                secretKeyRef:
                  name: notifications-db-url
                  key: latest
          startupProbe:
            httpGet:
              path: /readyz
          livenessProbe:
            httpGet:
              path: /healthz
```

### Base de datos

- Cloud SQL PostgreSQL 15, instancia **`notifications-db`**, alta disponibilidad regional.
- Conexión mediante el conector de Cloud SQL con IAM database authentication.
- Mismas convenciones que en AWS (`docs/database/postgresql-conventions.md`).

### Despliegue

```bash
gcloud run deploy notifications-service \
  --project nimbus-analytics-prod \
  --region us-central1 \
  --image us-central1-docker.pkg.dev/nimbus-analytics-prod/services/notifications-service:$GIT_SHA \
  --min-instances 2 --max-instances 50 --concurrency 80
```

El pipeline de GitHub Actions se autentica en GCP mediante Workload Identity Federation (OIDC de GitHub), nunca con claves JSON.

## ml-risk-scoring en GKE Autopilot

- Clúster **`risk-scoring-gke`** (GKE Autopilot, `us-central1`).
- Expuesto internamente; `payments-service` lo consulta antes de capturar pagos de alto monto.
- Cada Kubernetes ServiceAccount se vincula a una cuenta de servicio de GCP con Workload Identity:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  risk-scoring@nimbus-analytics-prod.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:nimbus-analytics-prod.svc.id.goog[risk/risk-scoring]"

kubectl annotate serviceaccount risk-scoring -n risk \
  iam.gke.io/gcp-service-account=risk-scoring@nimbus-analytics-prod.iam.gserviceaccount.com
```

## Terraform

```hcl
resource "google_cloud_run_v2_service" "notifications" {
  name     = "notifications-service"
  project  = "nimbus-analytics-prod"
  location = "us-central1"
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account                  = google_service_account.notifications.email
    max_instance_request_concurrency = 80
    scaling {
      min_instance_count = 2
      max_instance_count = 50
    }
    containers {
      image = "us-central1-docker.pkg.dev/nimbus-analytics-prod/services/notifications-service:${var.image_sha}"
    }
  }
}
```

## Analítica

Los eventos llegan desde AWS a Pub/Sub mediante `event-bridge-relay` (ver `docs/architecture/event-bus.md`) y se escriben en BigQuery, dataset **`analytics_events`**.
