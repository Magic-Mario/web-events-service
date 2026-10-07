# Microservicio `events` — T049.
#
# Multi-stage: el builder lleva el toolchain (`uv`, `grpcio-tools`) y el
# runtime sólo el venv + el código. **No va ninguna credencial en la imagen**:
# las credenciales llegan en vuelo desde IRSA (principio III).
#
# ⚠️ `server/_generated/` está en `.gitignore` y `server/servicer.py` los
# importa en runtime: sin el `gen_proto.sh` de este build la imagen arranca y
# revienta al importar. El stub que se genera aquí es el de SERVIDOR.

# --- builder: dependencias + stubs gRPC ------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencias primero: capa cacheable mientras no cambie el lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Código + contrato. Sólo lo necesario: ni tests, ni workflows, ni .git.
COPY config.py repository.py seed_catalog.py seed_events.json ./
COPY proto ./proto
COPY tools ./tools
COPY server ./server
COPY sources ./sources

# ⚠️ MUST: genera los stubs, que no están en el repositorio.
RUN bash tools/gen_proto.sh proto/events.proto

# --- runtime: venv + código, sin toolchain ---------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

WORKDIR /app

# Misma base que el builder a propósito: el `python` del venv construido en la
# etapa anterior sigue apuntando a la misma ruta del sistema. Ninguna variable
# de credenciales en esta capa (principio III).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY config.py repository.py seed_catalog.py seed_events.json ./
COPY server ./server
COPY sources ./sources

# El gRPC vive en un ClusterIP dentro del clúster: sólo lo abre k8s.
EXPOSE 50051

CMD ["python", "-m", "server"]
