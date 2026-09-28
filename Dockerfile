FROM python:3.11-slim

ARG PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
ARG DEBIAN_MIRROR_URL=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG PLAYWRIGHT_DOWNLOAD_HOST=
ARG PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANNAS_API_DATA_DIR=/data \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY pyproject.toml README.md ./
COPY annas_api ./annas_api
# Keep Debian's official security source intact; replace only the main package mirror.
RUN sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources \
    && pip install --no-cache-dir . \
    && if [ -n "${PLAYWRIGHT_DOWNLOAD_HOST}" ]; then \
         PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST}" \
         PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT="${PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT}" \
         python -m playwright install --with-deps chromium; \
       else \
         PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT="${PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT}" \
         python -m playwright install --with-deps chromium; \
       fi

RUN mkdir -p /data /ms-playwright
USER root
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "annas_api.api:app", "--host", "0.0.0.0", "--port", "8000"]
