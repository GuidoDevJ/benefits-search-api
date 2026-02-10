# Infraestructura AWS - ia-comafi

Infraestructura como codigo (IaC) usando **AWS CDK v2** en Python para desplegar la API de beneficios ia-comafi en AWS.

---

## Arquitectura General

```
                         INTERNET
                            |
                    +-------v--------+
                    |      WAF       |  <-- waf_stack.py
                    |  (4 reglas)    |      Filtra trafico malicioso
                    +-------+--------+      antes de llegar al ALB
                            |
                    +-------v--------+
                    |      ALB       |  <-- compute_stack.py
                    | (Load Balancer)|      Distribuye trafico entre
                    |  Puerto 80     |      los containers Fargate
                    +-------+--------+
                            |
                +-----------v-----------+
                |       VPC PRIVADA     |  <-- network_stack.py
                |                       |
                |  +------------------+ |
                |  |  ECS Fargate     | |  <-- compute_stack.py
                |  |  (Subnet Privada)| |
                |  |                  | |
                |  |  +------------+  | |
                |  |  | Container  |  | |
                |  |  | FastAPI    |  | |
                |  |  | :8000     |  | |
                |  |  +-----+------+  | |
                |  +--------|---------+ |
                |           |           |
                |     NAT Gateway       |  Permite salida a internet
                |     (Subnet Publica)  |  desde subnets privadas
                +-----------+-----------+
                            |
          +-----------------+------------------+
          |                 |                  |
  +-------v------+  +------v-------+  +-------v------+
  | AWS Bedrock  |  | Redis (ext.) |  |     S3       |  <-- data_stack.py
  | Claude 3     |  | Tu instancia |  | Logs queries |
  | Haiku        |  | externa      |  | no resueltas |
  +--------------+  +--------------+  +--------------+
```

---

## Stacks (Componentes)

La infraestructura esta dividida en **4 stacks de CloudFormation** independientes pero conectados entre si. Cada stack tiene una responsabilidad unica.

### 1. Network Stack (`network_stack.py`)

**Responsabilidad**: Red base donde vive todo.

**Que crea**:
| Recurso | Detalle |
|---------|---------|
| **VPC** | Red virtual aislada con CIDR automatico |
| **2 Subnets Publicas** | Una por AZ. Aca vive el ALB y el NAT Gateway |
| **2 Subnets Privadas** | Una por AZ. Aca corren los containers Fargate |
| **1 NAT Gateway** | Permite que los containers (privados) accedan a internet |
| **Internet Gateway** | Entrada de trafico desde internet al ALB |
| **VPC Flow Logs** | Registro de todo el trafico de red (auditoría de seguridad) |

**Por que subnets privadas?**
Los containers Fargate no son accesibles directamente desde internet. Solo el ALB (en la subnet publica) recibe trafico externo y lo reenvía a los containers. Esto es una capa extra de seguridad.

**Que expone a otros stacks**: `self.vpc`

---

### 2. Data Stack (`data_stack.py`)

**Responsabilidad**: Almacenamiento de secretos y logs.

**Que crea**:
| Recurso | Detalle |
|---------|---------|
| **Secrets Manager** | Almacena credenciales sensibles (API keys, Redis host/password) |
| **S3 Bucket** | Guarda queries que el sistema no pudo resolver (logs) |

**Secrets Manager** almacena un JSON con las siguientes claves:
```json
{
  "LANGCHAIN_API_KEY": "tu-langchain-api-key",
  "REDIS_HOST": "tu-redis-host.ejemplo.com",
  "REDIS_PORT": "6379",
  "REDIS_PASSWORD": "tu-password-redis"
}
```

**S3 Bucket** tiene:
- Encriptacion SSE-S3 activada
- Bloqueo total de acceso publico
- SSL obligatorio
- Lifecycle: los logs se eliminan automaticamente a los 90 dias

**Que expone a otros stacks**: `self.secret`, `self.logs_bucket`

**Depende de**: Network Stack (recibe el VPC)

---

### 3. Compute Stack (`compute_stack.py`)

**Responsabilidad**: Computo. Donde corre la aplicacion.

**Que crea**:
| Recurso | Detalle |
|---------|---------|
| **ECR Repository** | Registry privado de Docker. Guarda las imagenes del API |
| **ECS Cluster** | Cluster logico que agrupa los servicios Fargate |
| **Task Definition** | Define CPU, memoria, container, env vars, secrets |
| **Fargate Service** | Corre el container y mantiene el desired count |
| **ALB** | Load Balancer publico que recibe trafico HTTP |
| **Target Group** | Conecta el ALB con los containers Fargate |
| **Auto Scaling** | Escala entre 1-3 tasks segun CPU y requests |
| **IAM Roles** | 2 roles: ejecucion (pull image, read secrets) y runtime (Bedrock, S3) |
| **CloudWatch Log Group** | Logs del container con retencion de 14 dias |

**Detalle de los roles IAM**:

```
Task Execution Role (arranque del container)
├── AmazonECSTaskExecutionRolePolicy (pull ECR, write logs)
└── secretsmanager:GetSecretValue (leer secrets)

Task Role (runtime de la app)
├── bedrock:InvokeModel (llamar a Claude 3 Haiku)
├── bedrock:InvokeModelWithResponseStream
└── s3:PutObject en el bucket de logs
```

**Fargate Spot**: Los containers se ejecutan con estrategia Spot (peso 2) + On-Demand (peso 1). Esto significa que 2 de cada 3 tasks usan Spot (~70% mas barato). Si AWS necesita reclamar el Spot, el On-Demand asegura disponibilidad.

**Variables de entorno del container**:
```
Directas (no sensibles):          Desde Secrets Manager:
├── AWS_DEFAULT_REGION            ├── LANGCHAIN_API_KEY
├── BEDROCK_MODEL_ID              ├── REDIS_HOST
├── CACHE_TTL_DEFAULT             ├── REDIS_PORT
├── CACHE_TTL_BENEFITS            └── REDIS_PASSWORD
├── CACHE_ENABLED
├── S3_BUCKET_UNHANDLED
├── LANGCHAIN_TRACING_V2
├── LANGCHAIN_ENDPOINT
└── LANGCHAIN_PROJECT
```

**Auto Scaling**:
- Escala por **CPU** > 70% de uso
- Escala por **Requests** > 500 req/target
- Minimo: 1 task, Maximo: 3 tasks
- Cooldown: 5 min para bajar, 1 min para subir

**Que expone a otros stacks**: `self.alb_arn`

**Depende de**: Network Stack (VPC), Data Stack (secret, logs_bucket)

---

### 4. WAF Stack (`waf_stack.py`)

**Responsabilidad**: Firewall de aplicacion web. Protege el ALB.

**Que crea**:
| Recurso | Detalle |
|---------|---------|
| **WebACL** | Conjunto de reglas WAF asociadas al ALB |
| **WebACL Association** | Vincula el WebACL al ALB |

**Reglas de seguridad (en orden de prioridad)**:

| # | Regla | Tipo | Que hace |
|---|-------|------|----------|
| 1 | **RateLimitPerIP** | Custom | Bloquea IPs que hagan +2000 requests en 5 minutos |
| 2 | **AWSCommonRuleSet** | Managed | Protege contra XSS, path traversal, file inclusion, inyeccion de comandos |
| 3 | **AWSKnownBadInputs** | Managed | Bloquea payloads conocidos (Log4j/Log4Shell, deserializacion Java) |
| 4 | **AWSIPReputationList** | Managed | Bloquea IPs de botnets, scanners y fuentes maliciosas conocidas |

**Como funciona el WAF**:
```
Request entrante
      |
      v
[Regla 1] Rate limit OK?  --NO--> BLOCK 403
      |YES
      v
[Regla 2] Pasa Common Rules?  --NO--> BLOCK 403
      |YES
      v
[Regla 3] No es Bad Input?  --NO--> BLOCK 403
      |YES
      v
[Regla 4] IP con buena rep?  --NO--> BLOCK 403
      |YES
      v
   ALLOW --> ALB --> Fargate
```

**Depende de**: Compute Stack (ALB ARN)

---

## Flujo de Dependencias entre Stacks

```
NetworkStack
     |
     | vpc
     v
DataStack --------+
     |             |
     | secret      | logs_bucket
     v             v
ComputeStack -----+
     |
     | alb_arn
     v
WafStack
```

CDK despliega los stacks en el orden correcto automaticamente basandose en estas dependencias. No es necesario especificar el orden manualmente.

---

## CI/CD Pipeline (`.github/workflows/deploy.yml`)

El pipeline se activa con cada **push a `master`** o manualmente desde GitHub Actions.

### Flujo del Pipeline

```
Push a master
      |
      v
+---------------------+
| Job 1: BUILD        |
|                     |
| 1. Checkout codigo  |
| 2. Auth AWS (OIDC)  |
| 3. Login a ECR      |
| 4. Docker build     |
| 5. Push imagen con  |
|    tag: abc1234     |
|    tag: latest      |
+----------+----------+
           |
           | image_tag
           v
+---------------------+
| Job 2: DEPLOY       |
|                     |
| 1. Checkout codigo  |
| 2. Auth AWS (OIDC)  |
| 3. Install CDK      |
| 4. cdk diff (log)   |
| 5. cdk deploy --all |
|    con image_tag     |
+----------+----------+
           |
           v
+---------------------+
| Job 3: HEALTH CHECK |
|                     |
| 1. Auth AWS (OIDC)  |
| 2. Leer URL del ALB |
|    desde CloudForm.  |
| 3. Esperar 30s      |
| 4. curl GET /       |
| 5. Verificar 200 OK |
+---------------------+
```

### Autenticacion: OIDC (sin access keys)

El pipeline NO usa access keys estaticas. Usa **OpenID Connect (OIDC)** para asumir un rol IAM temporalmente. Esto es la practica recomendada por AWS porque:

- No hay credenciales de larga duracion almacenadas en GitHub
- Los tokens son temporales (15 min)
- Se puede restringir por repositorio y branch

---

## Flujo de Trabajo Correcto

### Setup Inicial (una sola vez)

```bash
# 1. Instalar herramientas
npm install -g aws-cdk
cd infra && pip install -r requirements.txt

# 2. Bootstrap CDK en tu cuenta AWS
#    Esto crea el bucket S3 y los roles que CDK necesita
cdk bootstrap aws://TU_ACCOUNT_ID/us-east-1

# 3. Primer deploy (crea toda la infra)
cdk deploy --all

# 4. Cargar los secretos en Secrets Manager
aws secretsmanager put-secret-value \
  --secret-id ia-comafi/app-secrets \
  --secret-string '{
    "LANGCHAIN_API_KEY": "lsv2_pt_...",
    "REDIS_HOST": "tu-redis.ejemplo.com",
    "REDIS_PORT": "6379",
    "REDIS_PASSWORD": "password123"
  }'

# 5. Push de la primera imagen Docker a ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  TU_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com

docker build -t TU_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/ia-comafi-api:latest .
docker push TU_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/ia-comafi-api:latest

# 6. Re-deploy para que ECS use la imagen
cdk deploy --all -c image_tag=latest

# 7. Configurar OIDC en AWS para GitHub Actions
#    (ver seccion "Configurar OIDC" mas abajo)
```

### Desarrollo Diario

```
1. Escribis codigo en tu rama
2. Merge a master
3. GitHub Actions se activa automaticamente:
   Build imagen --> CDK Deploy --> Health Check
4. En ~5 min tu cambio esta en produccion
```

### Si Necesitas Cambiar Infra

```bash
cd infra

# Ver que va a cambiar (sin aplicar nada)
cdk diff --all

# Aplicar cambios
cdk deploy --all
```

### Si Necesitas Cambiar Secrets

```bash
aws secretsmanager put-secret-value \
  --secret-id ia-comafi/app-secrets \
  --secret-string '{"LANGCHAIN_API_KEY":"nueva-key", ...}'

# Forzar que ECS tome los nuevos valores
# (reinicia el container con los secrets actualizados)
aws ecs update-service \
  --cluster ia-comafi-cluster \
  --service NOMBRE_DEL_SERVICIO \
  --force-new-deployment
```

### Rollback

```bash
# CDK tiene circuit breaker activado.
# Si un deploy falla, automaticamente vuelve a la version anterior.

# Rollback manual a una imagen especifica:
cdk deploy --all -c image_tag=COMMIT_SHA_ANTERIOR
```

---

## Configurar OIDC para GitHub Actions

### Paso 1: Crear Identity Provider en AWS

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

### Paso 2: Crear el IAM Role

Crear el archivo `trust-policy.json`:
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
          "token.actions.githubusercontent.com:sub": "repo:TU_ORG/ia-comafi-v1:*"
        }
      }
    }
  ]
}
```

```bash
aws iam create-role \
  --role-name github-actions-deploy \
  --assume-role-policy-document file://trust-policy.json

aws iam attach-role-policy \
  --role-name github-actions-deploy \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```

### Paso 3: Configurar el Secret en GitHub

En GitHub > Settings > Secrets and variables > Actions:

| Secret | Valor |
|--------|-------|
| `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::ACCOUNT_ID:role/github-actions-deploy` |

---

## Configuracion (`cdk.json`)

Todos los parametros configurables estan en `cdk.json`:

| Parametro | Default | Descripcion |
|-----------|---------|-------------|
| `project_name` | `ia-comafi` | Prefijo para todos los recursos |
| `environment` | `prod` | Tag de ambiente |
| `image_tag` | `latest` | Tag de imagen Docker en ECR |
| `nat_gateways` | `1` | Cantidad de NAT Gateways (0 = sin NAT, mas barato) |
| `fargate_cpu` | `512` | CPU del task (256, 512, 1024, 2048, 4096) |
| `fargate_memory` | `1024` | Memoria en MB del task |
| `min_capacity` | `1` | Minimo de tasks Fargate |
| `max_capacity` | `3` | Maximo de tasks Fargate |
| `waf_rate_limit` | `2000` | Max requests por IP cada 5 minutos |

Para sobreescribir temporalmente:
```bash
cdk deploy --all -c fargate_cpu=1024 -c fargate_memory=2048
```

---

## Costos Estimados (mensual)

| Servicio | Costo |
|----------|-------|
| ECS Fargate Spot (0.5 vCPU, 1GB) | ~$5-8 |
| ALB | ~$16 |
| NAT Gateway (1x) | ~$32 |
| WAF (WebACL + 4 reglas) | ~$10 |
| Secrets Manager (4 secrets) | ~$1.60 |
| ECR (10 imagenes) | ~$1 |
| CloudWatch Logs | ~$1-2 |
| S3 (logs) | ~$0.10 |
| VPC Flow Logs | ~$0.50 |
| Bedrock Claude 3 Haiku | ~$0.25-5 (uso) |
| **Total** | **~$68-77** |

### Optimizar costos

- Cambiar `nat_gateways: 0` y usar subnets publicas: **-$32/mes**
- Desactivar VPC Flow Logs si no son necesarios: **-$0.50/mes**
- Reducir retention de logs: **-$0.50/mes**

---

## Comandos Utiles

```bash
# Ver todos los stacks
cdk list

# Preview de cambios
cdk diff --all

# Deploy de un stack especifico
cdk deploy ia-comafi-network

# Destruir todo (CUIDADO)
cdk destroy --all

# Ver logs del container en tiempo real
aws logs tail /ecs/ia-comafi/api --follow

# Ver estado del servicio ECS
aws ecs describe-services \
  --cluster ia-comafi-cluster \
  --services NOMBRE_SERVICIO

# Forzar nuevo deployment
aws ecs update-service \
  --cluster ia-comafi-cluster \
  --service NOMBRE_SERVICIO \
  --force-new-deployment
```
