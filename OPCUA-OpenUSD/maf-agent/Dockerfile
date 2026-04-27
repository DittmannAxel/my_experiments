FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
        asyncua \
        httpx \
        pydantic \
        agent-framework

COPY src /app/src
COPY prompts /app/prompts

ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import os, time, sys; \
hb='/tmp/agent.heartbeat'; \
sys.exit(0 if (time.time() - os.path.getmtime(hb)) < 30 else 1)"

CMD ["python", "-m", "src.main"]
