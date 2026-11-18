# hardware-catalog-pipeline

Work in progress. Turns messy supplier spec sheets for PC parts into a validated catalog with Claude: classify, extract, validate, correct, then route each product to the catalog or to a human review queue.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env  # add your ANTHROPIC_API_KEY
.venv/bin/pytest
```
