FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements/server_requirements.txt ./requirements/server_requirements.txt
RUN pip install --no-cache-dir -r requirements/server_requirements.txt

COPY . .

RUN mkdir -p server/temp server/models

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

CMD ["python", "-m", "server.main"]
