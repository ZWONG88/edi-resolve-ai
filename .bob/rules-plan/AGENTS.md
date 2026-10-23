# Project Architecture Rules (Non-Obvious Only)

- Project is in pure scaffolding state — `backend/` has only an empty `__init__.py`; no architectural patterns are established yet, so new patterns you introduce become the convention
- UI is Streamlit only — do not architect around React, REST APIs, or microservices unless explicitly requested; Streamlit handles both frontend and backend in a single process
- Secrets are split across two systems: `.env` (python-dotenv for backend logic) and `.streamlit/secrets.toml` (Streamlit's native secrets for UI layer) — keep them consistent
- SQLite is the implied DB (gitignored `*.db`) — design schema migrations to be forward-only and recreatable since there is no migration tooling
- The `data/` directory structure (raw → processed → reference) implies an ETL pipeline: ingest raw EDI → parse to processed → enrich with reference data; design modules accordingly
