FROM python:3.12-slim

WORKDIR /opt/gateway_v2

COPY gateway_v2/pyproject.toml /opt/gateway_v2/pyproject.toml
COPY gateway_v2/gateway_v2 /opt/gateway_v2/gateway_v2
COPY gateway_v2/lint /opt/gateway_v2/lint
COPY shared /opt/shared

ENV PYTHONPATH=/opt/gateway_v2:/opt/shared

RUN pip install --no-cache-dir -e /opt/gateway_v2

CMD ["python", "-c", "from gateway_v2.edge import app; raise SystemExit(0 if app is None else 1)"]
