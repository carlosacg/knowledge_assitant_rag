---
title: Autenticación y autorización de APIs
tech_stack: [fastapi, django]
doc_type: standard
environment: all
last_updated: 2026-07-14
owner: identity-team
---

# Autenticación y autorización de APIs

Todas las APIs de Nimbus Labs se autentican con tokens emitidos por **nimbus-auth**, el servicio interno de identidad. Este documento define cómo validar tokens de usuario, cómo obtener tokens de servicio a servicio y qué scopes existen.

## Tokens de usuario (JWT)

- Formato: **JWT firmado con RS256**.
- **Access token**: vida útil de **15 minutos**.
- **Refresh token**: vida útil de **7 días**. Se rota en cada uso; un refresh token reutilizado revoca toda la familia de tokens.
- Claves públicas publicadas en el JWKS: `https://auth.nimbus.internal/.well-known/jwks.json`.
- El token se envía en el header `Authorization: Bearer <token>`.

### Claims relevantes

```json
{
  "iss": "https://auth.nimbus.internal",
  "sub": "user_7f3a9c",
  "aud": "nimbus-api",
  "exp": 1760000900,
  "iat": 1760000000,
  "scope": "payments:read payments:write",
  "merchant_id": "mrc_1029"
}
```

### Validación

Cada servicio valida localmente el token: firma RS256 contra el JWKS (cacheado 10 minutos), `iss`, `aud`, `exp` y scopes. Nunca se acepta `alg: none` ni HS256.

## Autenticación servicio a servicio

Las llamadas entre servicios usan **OAuth2 client credentials**:

1. El servicio llamante obtiene un token en `POST https://auth.nimbus.internal/oauth/token` con `grant_type=client_credentials`, su `client_id` y `client_secret` (almacenado en el gestor de secretos).
2. Envía el token en el header **`X-Nimbus-Service-Token`**.
3. El servicio receptor lo valida contra el mismo JWKS.

```bash
curl -s -X POST https://auth.nimbus.internal/oauth/token \
  -d grant_type=client_credentials \
  -d client_id=ledger-service \
  -d client_secret="$LEDGER_CLIENT_SECRET" \
  -d scope="payments:read"
```

```python
resp = await http.get(
    f"{PAYMENTS_BASE_URL}/api/v1/payments/{payment_id}",
    headers={"X-Nimbus-Service-Token": service_token, "X-Request-ID": request_id},
)
```

Los tokens de servicio se cachean en memoria hasta 60 segundos antes de su expiración.

## Scopes

| Scope | Permite |
|---|---|
| `payments:read` | Consultar pagos (`GET /api/v1/payments/{payment_id}`) |
| `payments:write` | Crear pagos (`POST /api/v1/payments`) |
| `payments:refund` | Solicitar reembolsos (`POST /api/v1/payments/refunds`) |
| `accounts:admin` | Operaciones administrativas en `accounts-service` |

Un token sin el scope requerido recibe `403` con código `INSUFFICIENT_SCOPE`. La aprobación de reembolsos de más de 5.000 USD además exige el rol `finance-ops`.

## Implementación en FastAPI

```python
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, SecurityScopes
import jwt

JWKS_URL = "https://auth.nimbus.internal/.well-known/jwks.json"
jwks_client = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=600)
bearer = HTTPBearer(auto_error=False)


async def require_scopes(
    security_scopes: SecurityScopes,
    request: Request,
    credentials=Depends(bearer),
) -> Principal:
    token = request.headers.get("X-Nimbus-Service-Token") or (credentials and credentials.credentials)
    if not token:
        raise HTTPException(401, detail="UNAUTHENTICATED")
    key = jwks_client.get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, key, algorithms=["RS256"], audience="nimbus-api",
                        issuer="https://auth.nimbus.internal")
    granted = set(claims.get("scope", "").split())
    if not set(security_scopes.scopes) <= granted:
        raise HTTPException(403, detail="INSUFFICIENT_SCOPE")
    return Principal.from_claims(claims)
```

Uso: `Security(require_scopes, scopes=["payments:refund"])`.

## Implementación en Django / DRF

`accounts-service` usa la clase `apps.core.auth.NimbusJWTAuthentication` configurada en `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]` y un permiso por scope:

```python
class HasScope(BasePermission):
    required_scope: str = ""

    def has_permission(self, request, view) -> bool:
        return self.required_scope in request.auth.get("scope", "").split()


class IsAccountsAdmin(HasScope):
    required_scope = "accounts:admin"
```

## Reglas

- Nunca loguear tokens completos; solo los últimos 6 caracteres.
- Nunca validar tokens llamando a nimbus-auth en cada request; usar el JWKS.
- Los `client_secret` de servicio se rotan cada 90 días.
