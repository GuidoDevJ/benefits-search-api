import os

from dotenv import load_dotenv

load_dotenv()

# AWS Bedrock Configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)

# Redis Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Cache
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"

# Serialization Format for LLM communication ("json" or "toon")
SERIALIZATION_FORMAT = os.getenv("SERIALIZATION_FORMAT", "json")

# Audit Configuration
AUDIT_ENABLED = os.getenv("AUDIT_ENABLED", "true").lower() == "true"

# CloudWatch Logs — log groups y retention (audit + unhandled queries)
CW_LOG_GROUP_RECORDS = os.getenv("CW_LOG_GROUP_RECORDS", "/comafi/audit/records")
CW_LOG_GROUP_SESSIONS = os.getenv("CW_LOG_GROUP_SESSIONS", "/comafi/audit/sessions")
CW_LOG_GROUP_UNHANDLED = os.getenv(
    "CW_LOG_GROUP_UNHANDLED", "/comafi/unhandled-queries"
)
CW_RETENTION_DAYS = int(os.getenv("CW_RETENTION_DAYS", "90"))

# Validaciones
# En ECS con task role, boto3 obtiene credenciales vía metadata endpoint;
# no se necesitan claves estáticas.
_ecs_task_role = os.getenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI") or \
                 os.getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI")

if not _ecs_task_role and (not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY):
    raise ValueError(
        "AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY deben estar configuradas en .env "
        "(o ejecutar en ECS con un IAM task role asignado)"
    )

if not os.getenv("LANGCHAIN_API_KEY"):
    print(
        "WARNING: LANGCHAIN_API_KEY no está configurada. "
        "LangSmith tracing estará deshabilitado."
    )
