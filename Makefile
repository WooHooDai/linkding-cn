.PHONY: init serve tasks test lint format frontend

init:
	uv sync
	uv run manage.py migrate
	npm install

serve:
	@pkill -f "manage.py runserver" 2>/dev/null || true
	@sleep 0.5
	uv run manage.py runserver

tasks:
	@pkill -f "manage.py run_huey" 2>/dev/null || true
	@sleep 0.5
	uv run manage.py run_huey

test:
	uv run pytest -n auto

lint:
	uv run ruff check bookmarks

format:
	uv run ruff format bookmarks
	npx prettier bookmarks/frontend --write
	npx prettier bookmarks/styles --write

frontend:
	npm run dev
