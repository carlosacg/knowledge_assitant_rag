---
title: Runbook de incidentes de base de datos (payments)
tech_stack: [postgresql, aws]
doc_type: runbook
environment: production
last_updated: 2026-09-15
owner: payments-core
---

# Runbook de incidentes de base de datos (payments)

Runbook para incidentes de la base RDS PostgreSQL 15 `payments` (instancia `db.r6g.xlarge` Multi-AZ en `us-east-1`, accedida vía RDS Proxy `payments-rds-proxy`). Aplica a la persona de guardia de payments-core.

## Escalamiento

- Canal de Slack: **`#inc-database`**. Abrir un hilo por incidente con la alerta y la hora de inicio.
- PagerDuty: servicio **`payments-db`**.
- Si hay impacto en clientes por más de 15 minutos, declarar incidente SEV-2 y avisar al incident commander de turno.

## Alertas

| Alerta | Condición | Severidad |
|---|---|---|
| `PaymentsDBHighConnections` | Conexiones > **80% de `max_connections`** durante 5 min | Alta |
| `PaymentsDBReplicationLag` | Lag de réplica > **30 s** | Alta |
| `PaymentsDBLongRunningQuery` | Consulta activa > **60 s** | Media |

### PaymentsDBHighConnections

Indica que la base se está quedando sin conexiones. Causas típicas: sesiones `idle in transaction` que no se liberan, un deploy con `PAYMENTS_DB_POOL_SIZE` mal configurado, o un escalado de tareas ECS (máx 20 tareas × pool 20) que excede la capacidad sin pasar por RDS Proxy.

### PaymentsDBReplicationLag

Indica que la réplica de lectura (incluida la réplica cross-region en `us-west-2`) está más de 30 s atrasada respecto del primario. Causas típicas: backfills masivos, `VACUUM FULL`, transacciones muy largas. Mientras dure, los reportes que leen de la réplica pueden mostrar datos desactualizados y el RPO de DR se degrada.

### PaymentsDBLongRunningQuery

Una consulta lleva más de 60 s ejecutándose. Como el `statement_timeout` de la aplicación es 5 s, normalmente se trata de una sesión manual, un job batch o una migración.

## Diagnóstico

Conectarse a través del bastion con un usuario de solo lectura de guardia (`oncall_ro`) y ejecutar:

### Conexiones por estado

```sql
SELECT state, usename, application_name, count(*)
FROM pg_stat_activity
WHERE datname = 'payments'
GROUP BY state, usename, application_name
ORDER BY count(*) DESC;

SHOW max_connections;
```

### Consultas largas

```sql
SELECT pid, usename, state,
       now() - query_start AS duration,
       wait_event_type, wait_event,
       left(query, 200) AS query
FROM pg_stat_activity
WHERE datname = 'payments'
  AND state <> 'idle'
  AND now() - query_start > interval '60 seconds'
ORDER BY duration DESC;
```

### Bloqueos

```sql
SELECT blocked.pid          AS blocked_pid,
       blocked.query        AS blocked_query,
       blocking.pid         AS blocking_pid,
       blocking.query       AS blocking_query,
       now() - blocking.xact_start AS blocking_xact_age
FROM pg_stat_activity blocked
JOIN pg_locks bl ON bl.pid = blocked.pid AND NOT bl.granted
JOIN pg_locks kl ON kl.locktype = bl.locktype
                AND kl.database IS NOT DISTINCT FROM bl.database
                AND kl.relation IS NOT DISTINCT FROM bl.relation
                AND kl.transactionid IS NOT DISTINCT FROM bl.transactionid
                AND kl.pid <> bl.pid
                AND kl.granted
JOIN pg_stat_activity blocking ON blocking.pid = kl.pid;
```

### Lag de replicación

```sql
-- En la réplica
SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;
```

```bash
aws cloudwatch get-metric-statistics --region us-east-1 \
  --namespace AWS/RDS --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=payments-prod-replica \
  --start-time "$(date -u -d '-30 min' +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
  --period 60 --statistics Maximum
```

## Mitigación

### Terminar sesiones idle in transaction

Las sesiones en estado **`idle in transaction` por más de 5 minutos** pueden terminarse sin aprobación adicional usando `pg_terminate_backend`:

```sql
SELECT pid, usename, application_name,
       pg_terminate_backend(pid) AS terminated
FROM pg_stat_activity
WHERE datname = 'payments'
  AND state = 'idle in transaction'
  AND now() - state_change > interval '5 minutes';
```

Para terminar sesiones activas (no idle) se requiere confirmación de un segundo ingeniero en `#inc-database`. Primero intentar `pg_cancel_backend(pid)`.

### Reducir presión de conexiones

1. Verificar que todas las tareas usen `payments-rds-proxy`.
2. Si un deploy reciente cambió `PAYMENTS_DB_POOL_SIZE`, hacer rollback (ver `docs/infra/ci-cd-pipeline.md`).
3. Pausar jobs batch no críticos (`idempotency-keys-cleanup`, reportes).

### Failover de RDS Multi-AZ

Usar solo si el primario no responde o está degradado por hardware/AZ. El failover a la instancia standby suele tardar entre 60 y 120 segundos; RDS Proxy reduce el impacto reconectando automáticamente.

```bash
aws rds reboot-db-instance \
  --region us-east-1 \
  --db-instance-identifier payments-prod \
  --force-failover

aws rds describe-events --region us-east-1 \
  --source-type db-instance --source-identifier payments-prod --duration 30
```

Requisitos antes del failover:

1. Avisar en `#inc-database` y en PagerDuty `payments-db`.
2. Confirmar que la instancia es Multi-AZ (`MultiAZ: true`).
3. Tras el failover, verificar `/readyz` de `payments-service` y la métrica `DatabaseConnections`.

## Post-incidente

- Escribir el postmortem en un plazo de 5 días hábiles.
- Documentar las consultas o jobs que causaron el incidente y abrir tickets para corregirlos.
- Si se terminó una migración, revisar que siga el patrón zero-downtime de `docs/database/postgresql-conventions.md`.
