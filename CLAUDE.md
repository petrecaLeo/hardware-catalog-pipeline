# hardware-catalog-pipeline

Messy supplier spec sheets for PC parts go in; a validated catalog comes out. Each sheet is classified, extracted with a strict tool, validated (Pydantic + per-category rules) and sent back with the error when it fails. Then it goes to the catalog or to a human review queue. A store assistant answers only from the approved catalog.

## Stack

Python 3.12, `anthropic`, FastAPI + uvicorn, SQLite, a no-build page (HTML/CSS/JS), pytest.

## Commands

- Setup: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`, then `cp .env.example .env` and add the key.
- Tests: `.venv/bin/pytest`
- Always use `.venv/bin/python` and `.venv/bin/pytest`.

## Model

- `claude-haiku-5-5`. It is a real, current model: don't change or "fix" the name. Settings live in `pipeline/config.py`.
- Always send `thinking={"type": "disabled"}`. `temperature`, `top_p`, `top_k` and prefill return a 400.
- Read response blocks by `type`, never `content[0]`.
- Strict tools: every object needs `additionalProperties: false`, every field is required and nullable, at most 16 union-typed parameters per request.
- Keep `max_tokens` low. `stop_reason == "max_tokens"` means the answer was cut off.

## Cost

- Print the cost of every call from `message.usage`, with 6 decimals: `tokens: 16 in / 41 out | cost: $0.000221`.
- Per million tokens: $0.10 input and $0.50 output. Batch costs half. Cache write is 1.25× the input price, cache read 0.1×.
- Real API calls only when the task needs them, with the cost reported. Budget for the whole project: US$ 0.40.
- Tests never use the real key: `tests/conftest.py` sets a fake one before any project import, and tests use the fake client.
- Never print or commit `.env`.

## Repo rules

- Code, file names and commit messages in English. Data fields, prompts and the UI in pt-BR.
- Keep the repo self-contained: no references to files, folders or projects outside it.
- Commits: conventional commits, author `petrecaLeo` only, no `Co-Authored-By` or other attribution lines.
