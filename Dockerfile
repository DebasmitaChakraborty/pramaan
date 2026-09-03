FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

COPY pyproject.toml .
COPY pramaan/ ./pramaan/
COPY .contracts_store/ ./.contracts_store/

RUN pip install --no-cache-dir . uvicorn

EXPOSE 8080

CMD ["sh", "-c", "uvicorn pramaan.server:app --host 0.0.0.0 --port ${PORT:-8080}"]
