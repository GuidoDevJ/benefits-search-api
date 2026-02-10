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

# LangChain Configuration
LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "true")
LANGCHAIN_ENDPOINT = os.getenv(
    "LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"
)
LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY")
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "multiagent-project")

# Redis Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# Cache TTL Configuration (en segundos)
CACHE_TTL_DEFAULT = int(os.getenv("CACHE_TTL_DEFAULT", "86400"))  # 24 horas
CACHE_TTL_BENEFITS = int(os.getenv("CACHE_TTL_BENEFITS", "86400"))  # 24 horas
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"

# AWS S3 Configuration
S3_BUCKET_UNHANDLED = os.getenv("S3_BUCKET_UNHANDLED", "comafi-ai-logs")

# Validaciones
if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise ValueError(
        "AWS_ACCESS_KEY_ID y AWS_SECRET_ACCESS_KEY deben estar configuradas en .env"
    )

if not LANGCHAIN_API_KEY:
    print(
        "WARNING: LANGCHAIN_API_KEY no está configurada. "
        "LangSmith tracing estará deshabilitado."
    )
