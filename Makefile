.PHONY: up down build logs restart shell test send

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f kidbot

restart:
	docker compose restart kidbot

shell:
	docker compose exec kidbot bash

test:
	python -m pytest tests/ -v --tb=short

send:
	python scripts/send_text.py $(TEXT)
