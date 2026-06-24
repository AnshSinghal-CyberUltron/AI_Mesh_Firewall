FROM python:3.12-slim

WORKDIR /app

# Dependencies first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code + static UI.
COPY app ./app
COPY web ./web

ENV DEMO_HOST=0.0.0.0 \
    DEMO_PORT=8770 \
    PYTHONUNBUFFERED=1

EXPOSE 8770

CMD ["python", "-m", "app.server"]
