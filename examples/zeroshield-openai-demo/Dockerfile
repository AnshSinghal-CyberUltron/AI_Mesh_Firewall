# ZeroShield OpenAI-SDK reference demo — FastAPI backend that also serves the
# static frontend. Build context = this directory (examples/zeroshield-openai-demo).
#
#   docker build -f examples/zeroshield-openai-demo/Dockerfile \
#     -t ai-mesh-demo:dev examples/zeroshield-openai-demo
#
# Runtime config (env):
#   CONTROL_BASE_URL     — control plane for login + org simulator key (http://control:8000)
#   ZEROSHIELD_BASE_URL  — gateway /v1 base (internal: http://gateway:8300/v1)
#   ZEROSHIELD_API_KEY   — optional legacy fallback (unused when users log in)
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

EXPOSE 8770
WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8770"]
