FROM cgr.dev/chainguard/wolfi-base:latest@sha256:c902a861151182e4ce2de29b8632ba3f7d75b68c35880580f16e353819e524d3
RUN apk upgrade --no-cache && apk add --no-cache python-3.12 py3-pip-wheel \
    && python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
USER 10001:10001
CMD ["uvicorn","app.api:app","--host","0.0.0.0","--port","8000"]
