# Project Coding Rules (Non-Obvious Only)

- `requirements.txt` is empty — always add new dependencies to it; nothing is pre-installed
- SQLite `.db` files are gitignored; any DB initialization must be idempotent (recreatable from scratch)
- `.streamlit/secrets.toml` is gitignored — document required secret keys in a `.env.example` or README section when adding integrations
- `data/raw/`, `data/processed/`, `data/reference/` exist only as gitkeep placeholders — create subdirectories or files here as needed; do not move or rename the top-level data dirs
- `backend/` is the sole Python package root; add all new modules as submodules of `backend/` (e.g., `backend/parser.py`, `backend/ai_resolver.py`)
- No test framework is configured yet — when adding tests, add `pytest` to `requirements.txt` and co-locate test files or use a `tests/` directory at the project root
- Type hints are expected on all function signatures (no enforcing linter yet, but follow PEP 8)
