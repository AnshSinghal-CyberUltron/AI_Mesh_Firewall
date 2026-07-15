FROM python:3.12-alpine
WORKDIR /app
RUN pip install --no-cache-dir redis==5.0.8
COPY worker.py /app/worker.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/worker.py"]
