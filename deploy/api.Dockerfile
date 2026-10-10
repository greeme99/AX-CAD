# AX-CAD API (FastAPI + ezdxf + OCCT). Build context: repository root.
# Base images are build args so an internal registry / mirror can be used (closed networks).
ARG PYTHON_IMAGE=python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE}

# OCP (OpenCASCADE) links libGL/X11 even headless
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
# optional build secret "ca": a corporate TLS-inspecting proxy's CA bundle (never stored in a layer)
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca PIP_CERT=/run/secrets/ca; fi \
    && printf '%s\n' 'uv==0.11.32 \' \
        '--hash=sha256:3da76cd4e2697de30928b8a8524bd39183ac1e08cb7e72833807c022b7cba6c4 \' \
        '--hash=sha256:125c142363d0842c8506a057da56bae182e2aa3957344f57dd9ef20ea10f06b0 \' \
        '--hash=sha256:be0799f1ad70c755d10de5aaf46af94199d4f16a992f90278f2662350cd3f4fe' > /tmp/uv.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/uv.txt \
    && uv sync --locked --no-dev --no-install-project \
    && pip uninstall -y uv

COPY alembic.ini ./
COPY core core
COPY backend backend

RUN useradd --system --uid 10001 --no-create-home axcad && mkdir -p /data && chown axcad /data
USER axcad
ENV PATH=/app/.venv/bin:$PATH AXCAD_VAR_DIR=/data XDG_CACHE_HOME=/data/.cache
EXPOSE 8000
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
