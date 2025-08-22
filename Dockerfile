FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY exporter.py .
RUN chmod +x exporter.py

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash exporter && \
    chown -R exporter:exporter /app

USER exporter

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ls /app --summary || exit 1

CMD ["python", "/app/exporter.py", "--interval", "60"]