# gArchi workspace container (Cloud Run). Build: gcloud builds submit --tag <AR_IMAGE> .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080
WORKDIR /app

COPY pyproject.toml README.md ./
COPY gcp_architect ./gcp_architect
COPY scripts ./scripts

# Editable install keeps PROJECT_DIR=/app so assets/ and catalog paths resolve.
RUN pip install -e . \
 && python scripts/setup_icons.py \
 && rm -rf /root/.cache

# Non-root runtime user
RUN useradd -m -u 10001 garchi && chown -R garchi:garchi /app
USER garchi

EXPOSE 8080
CMD exec python -m uvicorn gcp_architect.ui.server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips '*'
