# Image ramping untuk MCP server Invezgo.
# Sengaja TIDAK install pandas/numpy: jalur import MCP (server -> config -> ingest
# .client -> budget) cuma butuh requests + yaml + dotenv + mcp. Build cepat, image kecil.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "mcp>=1.9" "requests>=2.31" "PyYAML>=6.0" "python-dotenv>=1.0"

# Build context = root repo. Butuh src (paket) + config/settings.yaml (load_settings).
COPY src ./src
COPY config ./config

ENV PYTHONPATH=/app/src \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8848 \
    MCP_QUOTA_FILE=/data/mcp_quota.json

EXPOSE 8848
CMD ["python", "-m", "markup_radar.mcp_server.server"]
