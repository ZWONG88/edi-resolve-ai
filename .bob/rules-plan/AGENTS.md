# Project Architecture Rules (Non-Obvious Only)

- UI is Streamlit only — single-process; all backend calls are direct Python function calls, not HTTP
- Secrets: `.env` (python-dotenv for backend) + `.streamlit/secrets.toml` (Streamlit UI) — keep consistent; `AI_STUB_MODE` and `THREE_WAY_ENABLED` are read from `.env`
- SQLite schema is forward-only and must be recreatable; no migration tooling; all audit writes are atomic (transaction-wrapped)
- `data/raw/` holds JSON-format EDI fixtures; `data/reference/` holds `uom_conversions.json` + `price_contracts.json`; `tests/fixtures/` holds AT01–AT24 labeled scenario files
- AI provider abstracted behind `backend/ai_resolver.py`; `AI_STUB_MODE=true` by default — never hardwire provider calls into UI or comparator
- Financial calculations live exclusively in `backend/comparator.py` — AI must not perform or verify arithmetic; AI must not receive raw EDI text (prompt injection risk per PRD Section 19)
- Result states are a closed vocabulary (13 states defined in `edi-resolve-plan.md`) — do not invent new states; `NOT_EVALUATED` ≠ `CHECK_FAILED`
- P2 three-way 861 receipt matching is gated behind `THREE_WAY_ENABLED=false` — do not implement 861 semantic parsing unless this flag is explicitly enabled and documented
- P0 release gate: AT01–AT24 in `tests/test_acceptance.py` must pass; AT25–AT36 are P2 only
- Build plan: `edi-resolve-plan.md` at project root — read before proposing architectural changes
