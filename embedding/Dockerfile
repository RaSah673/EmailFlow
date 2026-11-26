FROM cgr.dev/chainguard/wolfi-base:latest@sha256:c902a861151182e4ce2de29b8632ba3f7d75b68c35880580f16e353819e524d3
RUN apk upgrade --no-cache && apk add --no-cache python-3.12 py3-pip-wheel libstdc++ \
    && python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt && mkdir /models && python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5',cache_dir='/models',threads=2)" && chmod -R a+rX /models
COPY service.py .
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp HF_HUB_OFFLINE=1
USER 10001:10001
CMD ["uvicorn","service:app","--host","0.0.0.0","--port","8000","--no-access-log","--limit-concurrency","12"]
