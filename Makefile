# =============================================================================
# TripMesh — Unified Developer Workflow Makefile
# =============================================================================

.PHONY: help setup dev test build docker-up docker-down clean

help:
	@echo "TripMesh commands:"
	@echo "  make setup      - Install Python dependencies and Node modules"
	@echo "  make dev        - Run the multi-agent mesh and frontend locally"
	@echo "  make test       - Run Python test suite across all agents"
	@echo "  make build      - Build static frontend assets and compile Python"
	@echo "  make docker-up  - Start all services with Docker Compose"
	@echo "  make docker-down- Stop all Docker Compose services"
	@echo "  make clean      - Clean build artifacts and temporary files"

setup:
	@echo "==> Setting up environment..."
	python -m pip install --upgrade pip
	python -m pip install -r requirements.txt
	cd frontend && npm install

dev:
	@echo "==> Starting TripMesh multi-agent system..."
	python run_mesh.py

test:
	@echo "==> Running test suite..."
	python -m pytest tests/ -v

build:
	@echo "==> Compiling Python source..."
	python -m compileall shared planner_agent gateway transport_agent hotel_agent_crewai local_guide_agent_rag budget_agent
	@echo "==> Building frontend assets..."
	cd frontend && npm run build

docker-up:
	@echo "==> Starting TripMesh via Docker Compose..."
	docker compose up --build

docker-down:
	@echo "==> Stopping Docker Compose services..."
	docker compose down

clean:
	@echo "==> Cleaning temporary build files..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	rm -rf frontend/dist frontend/.astro
