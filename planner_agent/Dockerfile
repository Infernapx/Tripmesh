FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
EXPOSE 8001
ENV SERVICE_NAME=planner_agent
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
