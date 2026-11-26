FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir /models && python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5',cache_dir='/models',threads=2)" && chmod -R a+rX /models
COPY service.py .
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp HF_HUB_OFFLINE=1
USER 10001:10001
CMD ["uvicorn","service:app","--host","0.0.0.0","--port","8000","--no-access-log","--limit-concurrency","12"]
