FROM python:3.12-slim-bookworm
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY shared /app/shared
COPY control/pyproject.toml /app/control/pyproject.toml
COPY control/manage.py /app/control/manage.py
COPY control/ai_mesh_control /app/control/ai_mesh_control
WORKDIR /app/control
RUN pip install --no-cache-dir \
    django djangorestframework psycopg[binary] redis celery \
    django-cors-headers django-redis djangorestframework-simplejwt \
    channels channels-redis daphne drf-spectacular whitenoise httpx \
    boto3 cryptography pyyaml jsonschema msal python-dotenv regex dataclasses-json
ENV PYTHONPATH=/app/shared:/app/control/ai_mesh_control
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
