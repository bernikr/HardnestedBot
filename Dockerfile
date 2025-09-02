FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
SHELL ["sh", "-exc"]

ENV UV_COMPILE_BYTECODE=1 \ 
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv venv

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm AS hardnested-builder
SHELL ["sh", "-exc"]
WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    <<EOT
apt-get update -q
apt-get install -qqy \
    -o APT::Install-Recommends=false \
    -o APT::Install-Suggests=false \
    build-essential liblzma-dev git

git clone https://github.com/noproto/HardnestedRecovery.git
cd HardnestedRecovery
make
cd ..
EOT

FROM python:3.13-slim-bookworm
SHELL ["sh", "-exc"]

COPY --from=builder --chown=app:app /app /app
COPY --from=hardnested-builder --chown=app:app /app/HardnestedRecovery /app/HardnestedRecovery
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
ARG VERSION
ENV VERSION=${VERSION:-"unspecified"}
EXPOSE 8080
CMD ["python", "app.py"]
