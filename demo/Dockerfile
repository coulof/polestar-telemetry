# Public demo: the real dashboard on synthetic data with the clock frozen (libfaketime), so "now" never goes stale.
#   podman build -f demo/Dockerfile -t polestar-demo .
# Holds no real data and no secrets; ADMIN_PASSWORD and DASHBOARD_PASSWORD stay unset (editing off).
FROM docker.io/library/python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends faketime && rm -rf /var/lib/apt/lists/*

ARG DEMO_NOW="2026-11-17 12:00:00"
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    DB_PATH=/tmp/demo.db CONFIG_PATH=/app/config.toml

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY polestar ./polestar
COPY app.py ./
COPY .streamlit ./.streamlit
COPY static ./static
COPY demo/config.toml ./config.toml
COPY demo/make_demo_db.py ./demo/make_demo_db.py

# Build the synthetic database at the frozen instant (make_demo_db uses the clock for "now").
RUN useradd -r -u 10001 app && mkdir /seed && chown app /seed
USER app
RUN faketime -f "$DEMO_NOW" python demo/make_demo_db.py /seed/demo.db

# Runtime clock: frozen wall time, real monotonic time so asyncio and websocket timeouts keep working.
ENV DEMO_NOW=$DEMO_NOW FAKETIME_DONT_FAKE_MONOTONIC=1 POLESTAR_DEMO=1
EXPOSE 8501
# SQLite (WAL) needs a writable directory: copy the seed DB to /tmp (a tmpfs when the root fs is read-only).
CMD ["sh", "-c", "cp /seed/demo.db /tmp/demo.db && FAKETIME=\"$DEMO_NOW\" LD_PRELOAD=/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1 exec streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false"]
