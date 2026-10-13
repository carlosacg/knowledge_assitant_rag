---
title: Despliegue de producción en AWS
tech_stack: [aws, fastapi, postgresql]
doc_type: deployment
environment: production
last_updated: 2026-08-12
owner: platform-infra
---

# Despliegue de producción en AWS

Describe la infraestructura de producción en AWS para los servicios del núcleo transaccional, tomando `payments-service` como referencia. Toda la infraestructura está declarada en Terraform en el repositorio `nimbus-infra`.

## Regiones

- **Primaria:** `us-east-1`.
- **Recuperación ante desastres (DR):** `us-west-2`. Las imágenes ECR se replican a `us-west-2` y existe una réplica cross-region de lectura de RDS que puede promoverse en caso de pérdida de la región primaria.

## Cómputo: ECS Fargate

- Clúster ECS: **`nimbus-prod`**.
- `payments-service`: tarea de **1 vCPU / 2 GB** de memoria.
- Autoscaling: **mínimo 4, máximo 20 tareas**, target tracking al **60% de CPU**.
- Despliegue rolling con **deployment circuit breaker y rollback automático** habilitados.
- Las tareas corren en subredes privadas detrás de un Application Load Balancer; el health check del target group apunta a `/readyz`.

## Base de datos: RDS PostgreSQL

- Motor PostgreSQL 15, instancia **`db.r6g.xlarge` Multi-AZ**.
- **Retención de backups: 14 días** (backups automáticos + PITR).
- Acceso solo a través de **RDS Proxy `payments-rds-proxy`**; las tareas nunca se conectan directo a la instancia.
- Performance Insights habilitado con retención de 7 días.

## Secretos y variables de entorno

Los secretos viven en **AWS Secrets Manager** y se inyectan en la task definition vía `secrets`:

| Variable | Fuente |
|---|---|
| `PAYMENTS_DB_URL` | Secrets Manager `prod/payments/db-url` |
| `PAYCORE_API_KEY` | Secrets Manager `prod/payments/paycore-api-key` |
| `PAYMENTS_DB_POOL_SIZE` | `20` (variable plana) |
| `EVENT_BUS_TOPIC_ARN` | ARN de `nimbus-prod-payments-events` |
| `LOG_LEVEL` | `INFO` |
| `REFUND_APPROVAL_THRESHOLD_CENTS` | `500000` |

## Estado de Terraform

- Backend: bucket S3 **`nimbus-terraform-state`**.
- Lock: tabla DynamoDB **`terraform-locks`**.

```hcl
terraform {
  required_version = ">= 1.6"
  backend "s3" {
    bucket         = "nimbus-terraform-state"
    key            = "prod/payments-service/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}
```

## Módulo Terraform `ecs-service`

Módulo `nimbus-infra/modules/ecs-service/main.tf`, usado por todos los servicios Fargate:

```hcl
variable "service_name"      { type = string }
variable "cluster_name"      { type = string }
variable "image_tag"         { type = string }
variable "cpu"               { type = number }
variable "memory"            { type = number }
variable "min_capacity"      { type = number }
variable "max_capacity"      { type = number }
variable "cpu_target"        { type = number }
variable "container_port"    { type = number  default = 8000 }
variable "subnet_ids"        { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "target_group_arn"  { type = string }
variable "environment"       { type = map(string) }
variable "secrets"           { type = map(string) }

data "aws_ecs_cluster" "this" {
  cluster_name = var.cluster_name
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.cluster_name}/${var.service_name}"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.service_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = var.service_name
    image     = "123456789012.dkr.ecr.us-east-1.amazonaws.com/${var.service_name}:${var.image_tag}"
    essential = true
    portMappings = [{ containerPort = var.container_port, protocol = "tcp" }]
    environment = [for k, v in var.environment : { name = k, value = v }]
    secrets     = [for k, arn in var.secrets : { name = k, valueFrom = arn }]
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fs http://localhost:${var.container_port}/healthz || exit 1"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = "us-east-1"
        awslogs-stream-prefix = var.service_name
      }
    }
  }])
}

resource "aws_ecs_service" "this" {
  name            = var.service_name
  cluster         = data.aws_ecs_cluster.this.arn
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.min_capacity
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = var.service_name
    container_port   = var.container_port
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_appautoscaling_target" "this" {
  service_namespace  = "ecs"
  resource_id        = "service/${var.cluster_name}/${aws_ecs_service.this.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_capacity
  max_capacity       = var.max_capacity
}

resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.service_name}-cpu-target"
  service_namespace  = aws_appautoscaling_target.this.service_namespace
  resource_id        = aws_appautoscaling_target.this.resource_id
  scalable_dimension = aws_appautoscaling_target.this.scalable_dimension
  policy_type        = "TargetTrackingScaling"

  target_tracking_scaling_policy_configuration {
    target_value       = var.cpu_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.service_name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role" "task" {
  name               = "${var.service_name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

output "service_name" { value = aws_ecs_service.this.name }
output "task_definition_arn" { value = aws_ecs_task_definition.this.arn }
```

## Instanciación para payments-service

```hcl
module "payments_service" {
  source       = "../../modules/ecs-service"
  service_name = "payments-service"
  cluster_name = "nimbus-prod"
  image_tag    = var.payments_image_sha
  cpu          = 1024
  memory       = 2048
  min_capacity = 4
  max_capacity = 20
  cpu_target   = 60

  environment = {
    PAYMENTS_DB_POOL_SIZE           = "20"
    EVENT_BUS_TOPIC_ARN             = aws_sns_topic.payments_events.arn
    LOG_LEVEL                       = "INFO"
    REFUND_APPROVAL_THRESHOLD_CENTS = "500000"
  }
  secrets = {
    PAYMENTS_DB_URL = "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/payments/db-url"
    PAYCORE_API_KEY = "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/payments/paycore-api-key"
  }
  subnet_ids         = module.vpc.private_subnet_ids
  security_group_ids = [aws_security_group.payments.id]
  target_group_arn   = aws_lb_target_group.payments.arn
}
```

## Aplicar cambios

```bash
cd nimbus-infra/envs/prod
terraform init
terraform plan -out=plan.tfplan
terraform apply plan.tfplan
```

Los `terraform apply` en producción se ejecutan solo desde el pipeline de `nimbus-infra` tras aprobación de un segundo miembro de platform-infra. El despliegue de imágenes de aplicación se describe en `docs/infra/ci-cd-pipeline.md`.
