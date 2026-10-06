# Written for GNU Make 3.81, the version macOS ships.
# Every target is a plain command line, so nothing here depends on make itself.

.PHONY: setup api test check

setup:
	cd backend && uv sync --locked

api:
	cd backend && uv run uvicorn ctviz.api.app:create_app --factory --host 127.0.0.1 --port 8000

test:
	cd backend && uv run pytest --block-network --record-mode=none

check:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest --block-network --record-mode=none
