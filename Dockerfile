# SPDX-FileCopyrightText: 2024 bernikr <hi@berni.kr>
#
# SPDX-License-Identifier: MIT

FROM ghcr.io/astral-sh/uv:trixie-slim AS app-builder
SHELL ["sh", "-exc"]

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PREVIEW_FEATURES=frozen-lockfile

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

FROM debian:trixie-slim AS hardnested-builder
SHELL ["sh", "-exc"]
WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    <<EOT
apt-get update -q
apt-get install -qqy \
    -o APT::Install-Recommends=false \
    -o APT::Install-Suggests=false \
    ca-certificates build-essential liblzma-dev git

git clone https://github.com/noproto/HardnestedRecovery.git
cd HardnestedRecovery
make
cd ..
EOT

FROM gcr.io/distroless/cc-debian13:latest AS runtime

COPY --from=app-builder /python /python
COPY --from=app-builder --chown=65532:65532 /app /app
COPY --from=hardnested-builder --chown=65532:65532 /app/HardnestedRecovery /app/HardnestedRecovery
ENV PATH="/app/.venv/bin:$PATH"

USER 65532:65532
WORKDIR /app
ARG VERSION
ENV VERSION=${VERSION:-"unspecified"}
EXPOSE 8080
CMD ["python", "app.py"]
