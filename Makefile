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
	python meetup.py

compose:
	$(DOCKER_COMPOSE) up --build

backup:
	python backup_runtime_data.py create
