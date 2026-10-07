# Written for GNU Make 3.81, the version macOS ships.
# Every target is a plain command line, so nothing here depends on make itself: the readme prints the
# commands beside each target. All of them are .PHONY, which matters for `docs`, also the name of a folder.

.PHONY: setup api web dev test check docs examples zip up down smoke

setup:
	cd backend && uv sync --locked
	cd frontend && pnpm install --frozen-lockfile

api:
	cd backend && uv run uvicorn ctviz.api.app:create_app --factory --host 127.0.0.1 --port 8000

web:
	cd frontend && pnpm dev

# Both in one terminal: Ctrl-C (or a TERM) ends the whole process group, so neither server is left running.
dev:
	trap 'kill 0' INT TERM; $(MAKE) api & $(MAKE) web & wait

test:
	cd backend && uv run pytest --block-network --record-mode=none
	cd frontend && pnpm test

check:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest --block-network --record-mode=none && uv run python scripts/gen_docs.py --check
	cd frontend && node scripts/gen-types.mjs --check && pnpm typecheck && pnpm lint && pnpm test && pnpm build

# Rewrites every document that is generated from the code: docs/SCHEMA.md, docs/schema/, docs/examples/README.md,
# the generated blocks of the readme, and the frontend's types. `make check` fails while any is stale.
docs:
	cd backend && uv run python scripts/gen_docs.py
	cd frontend && pnpm gen:types

# Records the ten example runs again with the real model and the live registry: needs the key and the network.
examples:
	cd backend && uv run python scripts/run_examples.py
	$(MAKE) docs

# The archive holds what is committed (git archive packs HEAD, so a .env, node_modules or .venv cannot get in),
# under one top-level folder. A dirty working tree is refused, because its changes would be left out silently;
# `make zip ALLOW_DIRTY=1` overrides that. The refusal comes first, so that it costs no time.
zip:
	@[ -n "$(ALLOW_DIRTY)" ] || test -z "$$(git status --porcelain)" || { \
		echo "make zip: the working tree has uncommitted changes, and the archive holds only what is committed."; \
		echo "Commit them first, or run: make zip ALLOW_DIRTY=1"; exit 1; }
	$(MAKE) check
	mkdir -p dist
	rm -f dist/submission.zip
	git archive --format=zip --prefix=query-to-visualization-agent/ -o dist/submission.zip HEAD
	cd backend && uv run python scripts/check_submission.py ../dist/submission.zip

# Containers: the whole stack with nothing but Docker installed (see docker-compose.yml).
up:
	docker compose up --build --wait

down:
	docker compose down

smoke:
	bash docker-smoke.sh
