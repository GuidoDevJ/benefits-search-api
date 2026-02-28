# Infraestructura y Permisos — Comafi AI

Recursos, permisos IAM y configuración necesaria para levantar el proyecto en AWS.

---

## 1. Variables de entorno requeridas

Copiar `.env.example` a `.env` y completar:

```env
# Obligatorias
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0

# Opcionales (valores por defecto ya configurados)
AUDIT_ENABLED=true
CW_LOG_GROUP_RECORDS=/comafi/audit/records
CW_LOG_GROUP_SESSIONS=/comafi/audit/sessions
CW_LOG_GROUP_UNHANDLED=/comafi/unhandled-queries
CW_RETENTION_DAYS=90
REDIS_HOST=localhost
REDIS_PORT=6379
CACHE_ENABLED=true

# LangSmith tracing (opcional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=comafi-ai
```

---

## 2. Recursos AWS

### 2.1 Amazon Bedrock

| Atributo | Valor |
|---|---|
| Región | `us-east-1` (obligatorio, el modelo solo está disponible ahí) |
| Modelo | `anthropic.claude-3-haiku-20240307-v1:0` |
| API | Bedrock Converse API |

**Acción requerida:** Solicitar acceso al modelo en la consola AWS:
`Bedrock → Model access → Anthropic Claude 3 Haiku → Request access`

---

### 2.2 Amazon CloudWatch Logs

Los log groups se crean automáticamente al iniciar la app si no existen.

| Log Group | Qué contiene | Retención |
|---|---|---|
| `/comafi/audit/records` | Un evento JSON por cada interacción (LLM call, tool call, error, etc.) | 90 días |
| `/comafi/audit/sessions` | Snapshot de sesión actualizado por conversación | 90 días |
| `/comafi/unhandled-queries` | Queries con `intent=unknown` o sin entidades detectadas | 90 días |

**Log Streams:** rotación diaria automática `YYYY/MM/DD`.

**CloudWatch Logs Insights** se usa para el read path (list_sessions, get_session_records).
Tener en cuenta que tiene **eventual consistency de ~segundos**.

---

### 2.3 Amazon CloudWatch Metrics

Namespace: `comafi/audit`

| Métrica | Dimensiones | Unidad |
|---|---|---|
| `LatencyMs` | `event_type`, `agent_name` | Milliseconds |
| `InputTokens` | `event_type`, `agent_name` | Count |
| `OutputTokens` | `event_type`, `agent_name` | Count |
| `ErrorCount` | `event_type`, `agent_name` | Count |

Se publican automáticamente con cada evento de auditoría.

---

## 3. Política IAM mínima

Crear un usuario IAM o rol con la siguiente política. Reemplazar `ACCOUNT_ID` y `REGION`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"
      ]
    },
    {
      "Sid": "CloudWatchLogsWrite",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:PutRetentionPolicy"
      ],
      "Resource": [
        "arn:aws:logs:REGION:ACCOUNT_ID:log-group:/comafi/*",
        "arn:aws:logs:REGION:ACCOUNT_ID:log-group:/comafi/*:log-stream:*"
      ]
    },
    {
      "Sid": "CloudWatchLogsQuery",
      "Effect": "Allow",
      "Action": [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery"
      ],
      "Resource": [
        "arn:aws:logs:REGION:ACCOUNT_ID:log-group:/comafi/*"
      ]
    },
    {
      "Sid": "CloudWatchMetrics",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricData"
      ],
      "Resource": "*"
    }
  ]
}
```

> **Nota:** `cloudwatch:PutMetricData` no soporta resource-level restrictions,
> por eso el `Resource: "*"` es obligatorio en ese statement.

### 3.1 Error común: AccessDeniedException en StartQuery

Si aparece este error en runtime:
```
AccessDeniedException: User is not authorized to perform StartQuery
on resources /comafi/audit/sessions
```

Significa que falta el statement `CloudWatchLogsQuery` en la política IAM.
Ir a **AWS Console → IAM → Usuarios → [tu usuario] → Añadir permisos en línea**
y pegar exactamente este statement:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchLogsQuery",
      "Effect": "Allow",
      "Action": [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery",
        "logs:DescribeLogGroups"
      ],
      "Resource": "*"
    }
  ]
}
```

> Usar `Resource: "*"` si el wildcard `/comafi/*` no funciona en tu cuenta
> (algunas cuentas tienen restricciones de Organizations que bloquean ARN parciales).

---

## 4. Redis (caché)

Redis se usa para cachear respuestas de la API de beneficios (TTL: 24 hs).

| Opción | Detalle |
|---|---|
| **Local (dev)** | `docker run -p 6379:6379 redis:alpine` |
| **AWS ElastiCache** | Cluster Redis, instancia `cache.t3.micro` para dev |
| **Redis Cloud** | Free tier disponible en `redis.io` |

Si Redis no está disponible, el sistema funciona igual (fallback graceful).

---

## 5. API externa — TeVaBien

La herramienta `benefits_api` consulta la API de beneficios de TeVaBien via HTTP.
No requiere configuración adicional (URL hardcodeada en el código).

Asegurarse de que el entorno tenga **acceso a internet saliente** para alcanzar
el endpoint de la API.

---

## 6. LangSmith (opcional)

Tracing automático de las llamadas LangChain. No es necesario para que el sistema funcione.

1. Crear cuenta en [smith.langchain.com](https://smith.langchain.com)
2. Generar un API key
3. Configurar en `.env`:
   ```env
   LANGCHAIN_TRACING_V2=true
   LANGCHAIN_API_KEY=lsv2_...
   LANGCHAIN_PROJECT=comafi-ai
   ```

---

## 7. Resumen de costos estimados (AWS)

| Servicio | Tier | Costo estimado |
|---|---|---|
| **Bedrock Claude 3 Haiku** | Input: $0.25/M tokens · Output: $1.25/M tokens | Variable por uso |
| **CloudWatch Logs** | Ingest: $0.50/GB · Storage: $0.03/GB/mes | < $5/mes para volumen normal |
| **CloudWatch Metrics** | $0.30/métrica/mes (primeras 10k gratis) | ~$0/mes (< 10k métricas) |
| **CloudWatch Logs Insights** | $0.005/GB escaneado | < $1/mes |
| **ElastiCache Redis** | `cache.t3.micro` ~$12/mes | Opcional |

---

## 8. Checklist de deploy (local / desarrollo)

- [ ] Crear usuario IAM con la política del punto 3
- [ ] Solicitar acceso a Claude 3 Haiku en Bedrock (puede tardar minutos)
- [ ] Configurar `.env` con las credenciales AWS
- [ ] Levantar Redis (local con Docker o servicio gestionado)
- [ ] Ejecutar `python -m src.app` — los log groups se crean solos al primer uso
- [ ] Verificar en CloudWatch Logs que lleguen eventos: `/comafi/audit/records`
- [ ] (Opcional) Configurar CloudWatch Alarm en `ErrorCount > 0` para alertas

---

## 9. Despliegue en AWS ECS (producción)

### 9.1 Infraestructura requerida

Antes de ejecutar el pipeline, crear manualmente (o vía IaC):

| Recurso | Nombre sugerido | Notas |
|---|---|---|
| **ECR Repository** | `comafi-ai` | Guardar las imágenes Docker |
| **ECS Cluster** | `comafi-cluster` | Tipo: Fargate |
| **ECS Service** | `comafi-service` | Vinculado a la task definition |
| **Task Definition** | `comafi-api` | Ver punto 9.2 |
| **IAM Task Role** | `comafi-ecs-task-role` | Ver punto 9.3 |
| **IAM Execution Role** | `ecsTaskExecutionRole` | Rol estándar de ECS |
| **ALB + Target Group** | `comafi-alb` | Health check en `GET /` |

---

### 9.2 Task Definition (fragmento relevante)

```json
{
  "family": "comafi-api",
  "taskRoleArn": "arn:aws:iam::ACCOUNT_ID:role/comafi-ecs-task-role",
  "executionRoleArn": "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "containerDefinitions": [
    {
      "name": "comafi-api",
      "image": "ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com/comafi-ai:latest",
      "portMappings": [{ "containerPort": 8000 }],
      "environment": [
        { "name": "AWS_REGION",              "value": "us-east-1" },
        { "name": "AUDIT_ENABLED",           "value": "true" },
        { "name": "CW_LOG_GROUP_RECORDS",    "value": "/comafi/audit/records" },
        { "name": "CW_LOG_GROUP_SESSIONS",   "value": "/comafi/audit/sessions" },
        { "name": "CW_LOG_GROUP_UNHANDLED",  "value": "/comafi/unhandled-queries" },
        { "name": "CW_RETENTION_DAYS",       "value": "90" },
        { "name": "CACHE_ENABLED",           "value": "false" },
        { "name": "WORKERS",                 "value": "2" }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group":         "/ecs/comafi-api",
          "awslogs-region":        "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -sf http://localhost:8000/ || exit 1"],
        "interval": 30,
        "timeout": 10,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ]
}
```

> **Nota:** No se setean `AWS_ACCESS_KEY_ID` ni `AWS_SECRET_ACCESS_KEY` en ECS.
> boto3 usa automáticamente el IAM Task Role a través del metadata endpoint.
> El código detecta el task role y omite la validación de credenciales estáticas.

---

### 9.3 IAM Task Role (`comafi-ecs-task-role`)

Usar la misma política del **punto 3** de este documento.
No se necesitan políticas adicionales — el task role reemplaza las credenciales estáticas.

---

### 9.4 IAM Role para GitHub Actions (OIDC)

El pipeline usa OIDC para evitar credenciales de larga duración en GitHub Secrets.

**Crear el rol** con este trust policy (reemplazar `ACCOUNT_ID` y `ORG/REPO`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:ORG/REPO:ref:refs/heads/master"
        }
      }
    }
  ]
}
```

**Permisos del rol** (política adjunta):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ECRAuth",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "ECRPush",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:us-east-1:ACCOUNT_ID:repository/comafi-ai"
    },
    {
      "Sid": "ECSDeployReadOnly",
      "Effect": "Allow",
      "Action": [
        "ecs:DescribeTaskDefinition",
        "ecs:DescribeServices"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ECSDeployWrite",
      "Effect": "Allow",
      "Action": [
        "ecs:RegisterTaskDefinition",
        "ecs:UpdateService"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassRoleToECS",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::ACCOUNT_ID:role/comafi-ecs-task-role",
        "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole"
      ]
    }
  ]
}
```

---

### 9.5 GitHub Secrets requeridos

Ir a **GitHub → Repositorio → Settings → Secrets and variables → Actions**:

| Secret | Valor |
|---|---|
| `AWS_ROLE_ARN` | `arn:aws:iam::ACCOUNT_ID:role/comafi-github-actions-role` |

Las variables de entorno de la aplicación van en la **task definition** de ECS,
**no** en los secrets de GitHub.

---

### 9.6 Habilitar OIDC Provider en AWS (una sola vez por cuenta)

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

---

### 9.7 Checklist de deploy en ECS

- [ ] Crear repositorio ECR `comafi-ai`
- [ ] Crear cluster ECS `comafi-cluster` (Fargate)
- [ ] Crear IAM Task Role `comafi-ecs-task-role` con la política del punto 3
- [ ] Registrar la task definition `comafi-api` (punto 9.2)
- [ ] Crear servicio ECS `comafi-service` con ALB en puerto 8000
- [ ] Habilitar OIDC Provider en la cuenta AWS (punto 9.6)
- [ ] Crear rol IAM `comafi-github-actions-role` con OIDC trust (punto 9.4)
- [ ] Agregar secret `AWS_ROLE_ARN` en GitHub
- [ ] Push a `master` → el pipeline hace build + deploy automáticamente
- [ ] Verificar que el servicio estabiliza en ECS Console
- [ ] Verificar logs en `/ecs/comafi-api` (stdout uvicorn) y `/comafi/audit/records` (audit)
