FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir asyncua fastapi "uvicorn[standard]" pydantic

COPY src /app/src
COPY web /app/web

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/health',timeout=2).getcode()==200 else 1)" || exit 1

CMD ["python", "-m", "src.server"]
