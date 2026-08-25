BLACK=black
RUFF=ruff
PYTEST=pytest
DOCKER_COMPOSE=docker compose

.PHONY: test lint format run compose backup

test:
	$(PYTEST) -q

lint:
	$(RUFF) check .

format:
	$(BLACK) .

run:
	streamlit run meetup.py --server.address 0.0.0.0 --server.port $${PORT:-8501}

compose:
	$(DOCKER_COMPOSE) up --build

backup:
	python backup_runtime_data.py create
