# syntax=docker/dockerfile:1.6
# ONE generic Dockerfile for ALL discovered services.
#
#   docker build -t users-service -f app/Dockerfile --build-arg SERVICE_NAME=users-service app/
#
# Build contract (what a service dir under app/ must provide):
#   - app/<name>/main.py          (FastAPI app; required for discovery)
#   - app/<name>/requirements.txt (optional; merged with shared/reqs)
#   - any extra files needed at runtime are copied with the whole dir.
#
# Runtime contract every service must implement (used by k8s probes,
# HEALTHCHECK and Prometheus):
#   - GET /livez  -> 200
#   - GET /readyz -> 200 when ready (Vault reachable), 503 otherwise
#   - GET /metrics -> prometheus metrics
#
# Hardening (multi-stage, non-root, digest-pinned base via --build-arg
# BASE_IMAGE=python:3.11.6-slim@sha256:..., HEALTHCHECK, no wheel/jaraco
# cleanup that strips vendored deps).
ARG BASE_IMAGE=python:3.11-slim
ARG SERVICE_NAME

FROM ${BASE_IMAGE} AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
ARG SERVICE_NAME
COPY shared/requirements.txt ./shared-requirements.txt
COPY ${SERVICE_NAME}/ ./service-src/
RUN if [ -f service-src/requirements.txt ]; then \
        pip install -r shared-requirements.txt -r service-src/requirements.txt; \
    else \
        pip install -r shared-requirements.txt; \
    fi
COPY shared/ ./shared/
RUN pip install ./shared
# Hardening: the base image vendors vulnerable wheel 0.45.1 and
# jaraco.context 5.3.0 INSIDE setuptools/_vendor (Trivy flags them even
# after the standalone upgrade). setuptools >= 84 vendors neither.
RUN pip install --upgrade "wheel>=0.46.2" "jaraco.context>=6.1.0" "setuptools>=84"

FROM ${BASE_IMAGE}
WORKDIR /app/${SERVICE_NAME}
ENV PYTHONUNBUFFERED=1 \
    PATH=/usr/local/bin:$PATH \
    LOG_FORMAT=json \
    PYTHONPATH=/app
ARG SERVICE_NAME
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/shared /app/shared
# Runtime is a pure uvicorn process: strip the build-tooling the base image
# and pip's isolated build env leak into site-packages (setuptools vendors
# vulnerable wheel 0.45.1 + jaraco.context 5.3.0 — Trivy fails the gate).
RUN rm -rf /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/wheel* \
           /usr/local/lib/python3.11/site-packages/jaraco* \
           /usr/local/lib/python3.11/site-packages/_distutils_hack \
           /usr/local/lib/python3.11/site-packages/distutils-precedence.pth
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && groupadd -r appuser && useradd -r -g appuser appuser --home-dir /app --no-create-home
COPY --chown=appuser:appuser ${SERVICE_NAME}/ ./
EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez',timeout=2).status==200 else 1)" || exit 1
LABEL org.opencontainers.image.source="https://github.com/<owner>/<repo>" \
      org.opencontainers.image.title="devops-platform ${SERVICE_NAME}" \
      org.opencontainers.image.licenses="MIT"
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]