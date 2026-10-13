---
title: Estándares de desarrollo Django
tech_stack: [django, python, postgresql]
doc_type: standard
environment: all
last_updated: 2026-06-03
owner: backend-guild
---

# Estándares de desarrollo Django

Este estándar aplica a todos los servicios Django de Nimbus Labs, en particular a `accounts-service`. Su cumplimiento se verifica en code review y, cuando es posible, en CI.

## Versión y dependencias

- **Django 4.2 LTS** es la única versión soportada. Las actualizaciones de versión mayor se coordinan desde el backend guild.
- **Django REST Framework (DRF)** para APIs.
- Python 3.11.
- Tests con **pytest-django**.

## Estructura del proyecto

```text
accounts-service/
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── local.py
│   │   ├── staging.py
│   │   └── production.py
│   ├── urls.py
│   └── wsgi.py
├── apps/
│   ├── customers/
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── selectors.py
│   │   ├── api/
│   │   │   ├── serializers.py
│   │   │   └── views.py
│   │   └── tests/
│   └── merchants/
└── manage.py
```

- Todas las apps viven bajo `apps/`.
- Los settings se dividen en `config/settings/base.py`, `local.py`, `staging.py` y `production.py`. El archivo activo se selecciona con la variable `DJANGO_SETTINGS_MODULE` (por ejemplo `config.settings.production`).

## Settings

```python
# config/settings/production.py
from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")  # inyectado desde AWS Secrets Manager

DATABASES = {
    "default": {
        **env.db("ACCOUNTS_DB_URL"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"options": "-c statement_timeout=5000"},
    }
}

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.core.auth.NimbusJWTAuthentication",
    ],
}
```

- `DJANGO_SECRET_KEY` siempre proviene del gestor de secretos; nunca se commitea ni se define en `base.py`.
- `CONN_MAX_AGE=60` para reutilizar conexiones persistentes.
- `statement_timeout` de 5 s para la aplicación, alineado con `docs/database/postgresql-conventions.md`.

## Capas: services, selectors y views

- **Lógica de negocio** en `services.py`: funciones que escriben (crear, actualizar, transicionar estados). Reciben argumentos keyword-only y son transaccionales.
- **Consultas de lectura** en `selectors.py`: funciones que devuelven querysets o objetos, sin efectos secundarios.
- **Views delgadas**: solo validan entrada con serializers, llaman a un service o selector y serializan la salida. Nada de lógica de negocio en views, serializers ni signals.

```python
# apps/customers/services.py
from django.db import transaction

@transaction.atomic
def customer_create(*, email: str, full_name: str, merchant_id: str) -> Customer:
    customer = Customer(email=email.lower(), full_name=full_name, merchant_id=merchant_id)
    customer.full_clean()
    customer.save()
    outbox_publish("accounts.customer.created", {"customer_id": str(customer.id)})
    return customer


# apps/customers/selectors.py
def customer_list(*, merchant_id: str) -> QuerySet[Customer]:
    return (
        Customer.objects.filter(merchant_id=merchant_id)
        .select_related("merchant")
        .prefetch_related("addresses")
        .order_by("-created_at")
    )
```

```python
# apps/customers/api/views.py
class CustomerListApi(ListAPIView):
    serializer_class = CustomerOutputSerializer
    pagination_class = CursorPagination  # page size 50

    def get_queryset(self):
        return customer_list(merchant_id=self.request.user.merchant_id)
```

## Acceso a datos

- **`select_related` / `prefetch_related` son obligatorios** cuando se accede a relaciones, para evitar consultas N+1. Los tests de endpoints de listado usan `django_assert_max_num_queries`.
- **SQL crudo prohibido** (`raw()`, `cursor.execute`) salvo aprobación explícita del DB guild, documentada en el PR.
- Migraciones con Django migrations, siguiendo el patrón zero-downtime de `docs/database/postgresql-conventions.md`. Para índices en tablas grandes usar `AddIndexConcurrently` en una migración con `atomic = False`.

```python
from django.contrib.postgres.operations import AddIndexConcurrently

class Migration(migrations.Migration):
    atomic = False
    operations = [
        AddIndexConcurrently(
            "customers",
            models.Index(fields=["merchant", "created_at"], name="ix_customers__merchant_id_created_at"),
        ),
    ]
```

## API con DRF

- Paginación: `CursorPagination` con page size 50 en todos los listados. No se usa `PageNumberPagination` porque es inestable con inserciones concurrentes y costosa con `OFFSET`.
- Versionado en la URL: `/api/v1/...`.
- Errores con el mismo cuerpo estándar que los servicios FastAPI: `{"error": {"code", "message", "request_id"}}`.

## Testing

- pytest-django, con `pytest --cov=apps --cov-fail-under=85`. **La cobertura mínima es 85%**; CI falla por debajo.
- Factories con `factory_boy`; no se usan fixtures JSON.
- Tests de services y selectors se priorizan sobre tests de views.

```bash
DJANGO_SETTINGS_MODULE=config.settings.local pytest -n auto --cov=apps --cov-fail-under=85
```

## Checklist de code review

- [ ] ¿La lógica nueva está en `services.py` / `selectors.py`?
- [ ] ¿Las relaciones usan `select_related` / `prefetch_related`?
- [ ] ¿Hay SQL crudo? Si sí, ¿tiene aprobación del DB guild?
- [ ] ¿Las migraciones son zero-downtime?
- [ ] ¿La cobertura se mantiene ≥ 85%?
