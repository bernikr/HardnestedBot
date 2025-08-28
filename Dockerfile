FROM ghcr.io/astral-sh/uv:python3.13-alpine AS builder
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

FROM python:3.13-alpine
SHELL ["sh", "-exc"]

COPY --from=builder --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app
CMD ["python", "app.py"]
