# syntax=docker/dockerfile:1.6
# Multi-stage build for the users-service.
# Build context must be the `app/` directory (not this service's own dir)
# so that the shared library at `shared/` is visible:
#   docker build -t users-service -f app/users-service/Dockerfile app/
#
# Hardening (devops-analysis-report.md P2):
#   - Multi-stage build with a builder stage isolating pip caches.
#   - Non-root `appuser`, COPY --chown.
#   - Python 3.11-slim + digest pin (override via --build-arg BASE_IMAGE=python:3.11.6-slim@sha256:...).
#   - HEALTHCHECK as defense-in-depth alongside the k8s probes.
#   - .dockerignore at repo root excludes .git, __pycache__, .venv from context.

ARG BASE_IMAGE=python:3.11-slim

FROM ${BASE_IMAGE} AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY shared/requirements.txt ./shared-requirements.txt
COPY users-service/requirements.txt ./service-requirements.txt
RUN pip install -r shared-requirements.txt -r service-requirements.txt
COPY shared/ ./shared/
RUN pip install ./shared

FROM ${BASE_IMAGE}
WORKDIR /app/users-service
ENV PYTHONUNBUFFERED=1 \
    PATH=/usr/local/bin:$PATH \
    LOG_FORMAT=json \
    PYTHONPATH=/app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /build/shared /app/shared
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
 && groupadd -r appuser && useradd -r -g appuser appuser --home-dir /app --no-create-home
COPY --chown=appuser:appuser users-service/main.py ./
EXPOSE 8000
USER appuser
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/livez',timeout=2).status==200 else 1)" || exit 1
LABEL org.opencontainers.image.source="https://github.com/abdelkaderbenhmida/stage" \
      org.opencontainers.image.title="devops-platform users-service" \
      org.opencontainers.image.licenses="MIT"
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
