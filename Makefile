BLACK=black
RUFF=ruff
PYTEST=pytest
DOCKER_COMPOSE=docker compose

.PHONY: test lint format run compose

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
