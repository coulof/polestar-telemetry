FROM docker.io/library/python:3.15-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/data/telemetry.db PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY polestar ./polestar
COPY app.py ./
COPY .streamlit ./.streamlit
COPY static ./static

RUN useradd -r -u 10001 app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8501

# default: dashboard; the collector service overrides the command
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
