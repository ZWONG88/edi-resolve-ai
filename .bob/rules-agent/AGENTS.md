# Project Coding Rules (Non-Obvious Only)

- All price/quantity fields in JSON fixtures are `str` — cast to `decimal.Decimal` in `backend/parser.py`; never use `float` anywhere in the codebase
- `ROUND_HALF_UP` is applied at line extension only — never round unit prices before computing extension
- `backend/db.py` `init_db()` must be called at the top of `app.py` before any Streamlit page renders; all audit event writes must be transaction-wrapped — if write fails, raise `AuditPersistenceError`
- `AI_STUB_MODE=true` default; `THREE_WAY_ENABLED=false` default — both read from `.env`; keep stub and disabled-receipt paths intact always
- Tests in `tests/`; acceptance tests in `tests/test_acceptance.py` (AT01–AT24, P0 gate); integration tests marked `@pytest.mark.integration`; fixture files in `tests/fixtures/`
- `backend/reference.py` is the sole file I/O owner for `data/reference/` — comparator and comparator tests must not open those files directly
- `INSUFFICIENT_EVIDENCE` is a valid result state, not an error — do not raise an exception; dependent checks become `NOT_EVALUATED`
- `backend/parser.py` retains original source string values in `raw` fields alongside `Decimal` casts; computes SHA-256 content hash at parse time
- `backend/exceptions.py` defines `ParseError`, `ReconciliationError`, `AuditPersistenceError` — use these, do not raise plain `ValueError` or `Exception`
- Required dependencies: `streamlit`, `python-dotenv`, `pytest`, `ruff` (AI SDK TBD — add when provider confirmed)
