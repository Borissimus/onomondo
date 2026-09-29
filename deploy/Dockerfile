FROM ghcr.io/astral-sh/uv:0.12.20 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend ./backend
COPY shared ./shared
COPY device-agent ./device-agent
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev && useradd --uid 10001 --create-home sgp32
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "sgp32.application:application", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
