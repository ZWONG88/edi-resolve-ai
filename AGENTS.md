# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

**EDI Resolve AI** — AI-powered EDI transaction discrepancy detection and resolution (SpartHill Hacks 2026 IBM AI Challenge). Python backend with a Streamlit dashboard.

## Stack

- **Language**: Python (virtual env at `.venv/`)
- **UI**: Streamlit (`streamlit run <app_file>`)
- **Database**: SQLite (`.db` files, gitignored)
- **Config/Secrets**: `.env` file (gitignored); Streamlit secrets at `.streamlit/secrets.toml` (also gitignored — never commit)
- No `pyproject.toml`, `setup.py`, or `Makefile` yet — project is in early scaffolding stage

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run Streamlit app (once entry point is created)
streamlit run app.py

# Run tests (pytest assumed; no config exists yet)
pytest
pytest path/to/test_file.py::test_function_name   # single test
```

> `requirements.txt` is currently empty — populate it as dependencies are added.

## Data Layout

```
data/
  raw/        # raw EDI files (850 POs, 810 invoices) — gitkeep placeholder
  processed/  # parsed/structured output — gitkeep placeholder
  reference/  # reference/lookup data — gitkeep placeholder
```

## Key Domain Concepts

- **EDI 850**: Electronic purchase orders from retailers
- **EDI 810**: Electronic invoices from suppliers
- Discrepancy types to detect: price, quantity, UOM, product identifier mismatches
- AI layer explains root causes and recommends resolutions

## Architecture Notes

- `backend/` package is scaffolded but empty — add modules here (parser, comparator, ai_resolver, etc.)
- No frontend framework other than Streamlit — avoid introducing React/Node unless explicitly requested
- Secrets must never be hardcoded; use `.env` + `python-dotenv` or Streamlit secrets

## Code Style

- Follow PEP 8; use type hints for all function signatures
- No linter config exists yet — add `ruff` or `flake8` to `requirements.txt` when introducing linting
