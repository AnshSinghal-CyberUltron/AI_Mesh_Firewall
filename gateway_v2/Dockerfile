# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ARG SOURCE_DATE_EPOCH=1704067200
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TZ=UTC \
    LC_ALL=C.UTF-8 \
    PYTHONPATH=/opt/gateway_v2:/opt/shared

# Bind-mount sources into a single RUN so COPY directory mtimes (build clock)
# never enter a layer. Touch to SOURCE_DATE_EPOCH before the layer is committed.
RUN --mount=type=bind,source=gateway_v2/pyproject.toml,target=/tmp/src/pyproject.toml,ro \
    --mount=type=bind,source=gateway_v2/gateway_v2,target=/tmp/src/pkg,ro \
    --mount=type=bind,source=shared/ai_mesh_shared,target=/tmp/src/shared,ro \
    mkdir -p /opt/gateway_v2/gateway_v2 /opt/shared/ai_mesh_shared \
    && cp /tmp/src/pyproject.toml /opt/gateway_v2/pyproject.toml \
    && cp -a /tmp/src/pkg/. /opt/gateway_v2/gateway_v2/ \
    && cp -a /tmp/src/shared/. /opt/shared/ai_mesh_shared/ \
    && find /opt -xdev -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} + \
    && pip install --no-cache-dir --no-compile \
        setuptools==75.8.2 \
        wheel==0.45.1 \
    && pip install --no-cache-dir --no-compile --no-build-isolation /opt/gateway_v2 \
    && find /usr/local /opt -xdev -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +

CMD ["python", "-c", "from gateway_v2.edge import app; raise SystemExit(0 if app is None else 1)"]
