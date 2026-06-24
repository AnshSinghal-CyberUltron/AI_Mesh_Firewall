FROM python:3.12-slim-bookworm
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY shared /app/shared
COPY control/pyproject.toml /app/control/pyproject.toml
COPY control/manage.py /app/control/manage.py
COPY control/ai_mesh_control /app/control/ai_mesh_control
COPY workers /app/workers
ENV PYTHONPATH=/app/shared:/app/control/ai_mesh_control:/app/workers
ENV DJANGO_SETTINGS_MODULE=main_app.settings
RUN pip install --no-cache-dir \
    django djangorestframework psycopg[binary] redis "celery[sqs]" \
    django-cors-headers django-redis djangorestframework-simplejwt \
    channels channels-redis daphne drf-spectacular whitenoise httpx \
    boto3 cryptography pyyaml jsonschema msal python-dotenv regex dataclasses-json
COPY workers/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["celery", "-A", "ai_mesh_workers.celery_app", "worker", "-Q", "policy.compile,platform.batch,compute.heavy,scan.tier2,vector.index,mcp.audit", "--loglevel=info", "--concurrency=4"]
