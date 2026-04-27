FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Pull the OPC UA spec source. Public, shallow, pinned by ref via build-arg.
ARG UA_SPEC_REF=main
RUN git clone --depth=1 --branch ${UA_SPEC_REF} \
        https://github.com/OPCFoundation/UA-for-AI-Prototype.git \
        /app/ua-spec-source \
 && rm -rf /app/ua-spec-source/.git

# Heavy deps in their own layer for cache reuse.
RUN pip install --no-cache-dir \
        torch==2.4.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir \
        sentence-transformers==3.1.1 \
        asyncpg \
        fastapi \
        "uvicorn[standard]" \
        openai \
        "mcp>=1.0" \
        pydantic \
        numpy

COPY src /app/src

# Pre-download the embedding model into the image so first-run isn't 5 min
# of model download on top of 5-15 min of embedding.
ENV HF_HOME=/app/.cache/hf
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('mixedbread-ai/mxbai-embed-large-v1')"

ENV PYTHONUNBUFFERED=1
EXPOSE 49322

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=20 \
  CMD curl -fsS http://localhost:49322/health -o /dev/null || exit 1

CMD ["python", "-m", "src.server"]
