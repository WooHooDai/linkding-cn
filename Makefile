.PHONY: serve

init:
	uv sync
	uv run manage.py migrate
	npm install

serve:
	uv run manage.py runserver

tasks:
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
