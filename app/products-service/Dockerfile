# syntax=docker/dockerfile:1.6
# Multi-stage build for the products-service.
# Build context must be the `app/` directory (not this service's own dir)
# so that the shared library at `shared/` is visible:
#   docker build -t products-service -f app/products-service/Dockerfile app/
#
# Hardening: see app/users-service/Dockerfile header.

ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE} AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY shared/requirements.txt ./shared-requirements.txt
RUN pip install --user -r shared-requirements.txt
COPY shared/ ./shared/
RUN pip install --user ./shared

FROM ${BASE_IMAGE}
WORKDIR /app/products-service
ENV PYTHONUNBUFFERED=1 \
    PATH=/root/.local/bin:$PATH \
    LOG_FORMAT=json
COPY --from=builder /root/.local /root/.local
COPY --chown=appuser:appuser products-service/main.py ./
RUN groupadd -r appuser && useradd -r -g appuser appuser --home-dir /app --no-create-home
EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez',timeout=2).status==200 else 1)" || exit 1
LABEL org.opencontainers.image.source="https://github.com/abdelkaderbenhmida/stage" \
      org.opencontainers.image.title="devops-platform products-service" \
      org.opencontainers.image.licenses="MIT"
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
