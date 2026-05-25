# Build from AI_Mesh_Firewall repo root
FROM python:3.12-slim-bookworm

WORKDIR /app
COPY shared /app/shared
COPY gateway/pyproject.toml /app/gateway/pyproject.toml
COPY gateway/ai_mesh_gateway /app/gateway/ai_mesh_gateway

WORKDIR /app/gateway
RUN pip install --no-cache-dir \
    boto3 cryptography fastapi "uvicorn[standard]" httpx jsonschema litellm \
    numpy pinecone psutil "psycopg[binary]" pymilvus python-dotenv pyyaml \
    redis requests motor websockets \
    opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp

ENV PYTHONPATH=/app/shared:/app/gateway:/app/gateway/ai_mesh_gateway
EXPOSE 8300

CMD ["python", "-m", "uvicorn", "ai_mesh_gateway.main:app", "--host", "0.0.0.0", "--port", "8300"]
