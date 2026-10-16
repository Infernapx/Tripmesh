FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY app.py .

# Expose port (overridden per-service via ENV if needed)
EXPOSE 8000

ENV SERVICE_NAME=gateway

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
