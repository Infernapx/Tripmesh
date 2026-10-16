FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
EXPOSE 8004
ENV SERVICE_NAME=local_guide_agent
# Chroma persistent dir (mounted as volume in docker-compose)
ENV CHROMA_PERSIST_DIR=/app/data/chroma_db
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
