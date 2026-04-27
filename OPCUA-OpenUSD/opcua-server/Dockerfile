FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
RUN pip install --no-cache-dir \
        "asyncua>=1.1.5" \
        "cryptography>=43.0" \
        "pydantic>=2.7"

COPY src /app/src
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /app/certs/trusted /app/certs/issued

ENV PYTHONUNBUFFERED=1
EXPOSE 4840

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import socket,sys; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',4840)); s.close()" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
