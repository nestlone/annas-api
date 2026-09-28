FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FERRY_API_DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY pyproject.toml README.md ./
COPY annas_api ./annas_api
RUN pip install --no-cache-dir . && python -m playwright install --with-deps chromium

RUN mkdir -p /data /ms-playwright
USER root
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "annas_api.api:app", "--host", "0.0.0.0", "--port", "8000"]
