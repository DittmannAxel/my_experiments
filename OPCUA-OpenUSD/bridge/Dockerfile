FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# usd-core is a fat binary wheel; keep it in its own layer for cache reuse.
RUN pip install --no-cache-dir usd-core
RUN pip install --no-cache-dir asyncua pydantic

COPY src /app/src

ENV PYTHONUNBUFFERED=1
ENV USD_STAGE_DIR=/stage

# Non-trivial healthcheck: the bridge keeps a heartbeat file in /stage that
# we touch every loop iteration; if it stops being touched, restart.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, time; \
heartbeat='/stage/.bridge.heartbeat'; \
mtime = os.path.getmtime(heartbeat); \
import sys; \
sys.exit(0 if (time.time() - mtime) < 30 else 1)"

CMD ["python", "-m", "src.main"]
