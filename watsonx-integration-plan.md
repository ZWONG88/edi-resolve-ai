# EDIResolve AI — watsonx Integration & First Run Plan

## Overview

The watsonx.ai integration code is already written in `backend/ai_resolver.py`. The goal of
this plan is to:
1. Get the app running locally so you can see the UI.
2. Wire in your IBM watsonx.ai credentials and verify the live AI call passes.
3. Confirm the `pytest -m integration` test passes end-to-end.

Everything stays P0-scoped. No new features; no P2 work.

**Source of truth:** `docs/PRD.md` (FR-05 AI Explanation; Section 8 Task)
**Release gate:** AT01–AT24 must still pass after wiring real keys.

---

## Sub-Tasks

---

### Task A — Run the App Locally (No Keys Needed)

**Status:** `[ ] pending`

**Intent:** Confirm the app boots, the three pages are reachable, and a dataset can be
imported and reviewed — all offline with the mock AI provider. This is your baseline
before touching credentials.

**Expected Outcomes:**
- `streamlit run app.py` opens without error.
- Upload & Process page loads; you can pick "Dataset 1" from the import picker and click
  Import & reconcile.
- Exception Review page shows the reconciled case with its result state and a mock AI
  explanation card.
- Audit History page shows the ingestion and rules-execution events.

**Todo List:**
1. Activate the existing virtual environment and install all dependencies (the venv exists
   but `pip install` has not been run yet — streamlit, pydantic, ibm-watsonx-ai, pytest are
   all missing):
   ```bash
   cd /Users/darrensan/edi-resolve-ai
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy the example env file so the app has defaults:
   ```bash
   cp .env.example .env
   ```
   Leave `AI_PROVIDER=mock` for now.
3. Start the app:
   ```bash
   streamlit run app.py
   ```
   Streamlit will print a local URL (usually `http://localhost:8501`). Open it in your browser.
4. Walk the demo flow:
   - **Upload & Process** → Import dataset folder → select **Dataset 1** → Import & reconcile.
   - **Exception Review** → open the case → look at the findings table and the AI card
     (will say "mock" in the model label).
   - **Audit History** → select the case to see the append-only event log.
5. Run the full test suite to confirm baseline:
   ```bash
   pytest
   ```
   Expected: **143 passed, 0 failed**.

**Relevant Context:**
- The `.venv/` directory exists at Python 3.12 but only has `ruff`, `tabulate`, and a few
  stdlib extras installed. Running `pip install -r requirements.txt` will install streamlit,
  pydantic, ibm-watsonx-ai, pytest, and everything else in one shot.
- `AI_PROVIDER=mock` is the default in `.env.example` so no keys are needed.
- The app calls `init_db()` on startup; the SQLite file is created automatically at
  `data/edi_resolve.db` (gitignored).

---

### Task B — Obtain IBM watsonx.ai Credentials

**Status:** `[ ] pending`

**Intent:** Gather the three required credentials before touching any code. All values go
into `.env`; nothing is hardcoded.

**Expected Outcomes:**
- You have an `AI_API_KEY`, a `WATSONX_PROJECT_ID`, and a confirmed `WATSONX_URL` in hand.

**Todo List:**
1. **API key** — go to [cloud.ibm.com](https://cloud.ibm.com) → **Manage → Access (IAM) →
   API keys → Create** → copy the key immediately (it is only shown once).
2. **Project ID** — go to [dataplatform.cloud.ibm.com/wx](https://dataplatform.cloud.ibm.com/wx)
   → open your watsonx.ai project → **Manage → General** → copy the Project ID GUID.
3. **URL** — use the region your account was created in:
   - US South: `https://us-south.ml.cloud.ibm.com`
   - EU Frankfurt: `https://eu-de.ml.cloud.ibm.com`
   - London: `https://eu-gb.ml.cloud.ibm.com`
   - Tokyo: `https://jp-tok.ml.cloud.ibm.com`
4. Confirm the **Granite model** is available in your project — in watsonx.ai Studio open
   the **Foundation models** catalog and verify `ibm/granite-3-8b-instruct` is listed.
   If it is not, note the exact available model ID to use instead.

**Relevant Context:**
- The model ID defaults to `ibm/granite-3-8b-instruct` in `backend/config.py` and
  `.env.example`; it can be overridden with `AI_MODEL_ID=<other-id>` in `.env`.
- The Lite plan allows ~300k tokens/month; a full demo run uses only a few dozen calls.
- Services on the Lite plan are deleted after 30 days of inactivity (README known limitation).

---

### Task C — Wire Credentials into .env and Verify Live Call

**Status:** `[ ] pending`

**Intent:** Add real credentials to `.env`, switch `AI_PROVIDER=watsonx`, and confirm the
live watsonx call produces a validated `AIExplanation` for a PRICE_VARIANCE case.

**Expected Outcomes:**
- `pytest -m integration` passes (the single `test_watsonx_live` test in
  `tests/test_ai_resolver.py`).
- The app's Exception Review page shows a real Granite explanation card (not "stub-rules-v1").
- The explanation cites real evidence IDs from the bundle and the `model_version` field
  matches the configured `AI_MODEL_ID`.
- All 143 existing unit/acceptance tests still pass (`pytest` without `-m integration`).

**Todo List:**
1. Open `.env` and fill in the three values from Task B:
   ```
   AI_PROVIDER=watsonx
   AI_API_KEY=<your-key>
   WATSONX_URL=https://us-south.ml.cloud.ibm.com   # adjust to your region
   WATSONX_PROJECT_ID=<your-project-id>
   AI_MODEL_ID=ibm/granite-3-8b-instruct            # or your verified model
   ```
2. Run only the integration test first (it makes one live API call):
   ```bash
   pytest -m integration -v
   ```
   Expected: `tests/test_ai_resolver.py::test_watsonx_live` PASSED.
3. If the test fails, read the error:
   - `RuntimeError: AI_API_KEY and WATSONX_PROJECT_ID must be set` → the `.env` values
     are not being picked up; verify the file is at the project root and has no stray spaces.
   - HTTP 401 / authentication error → the API key is wrong or has been revoked.
   - HTTP 404 / model not found → try `AI_MODEL_ID=ibm/granite-3-3-8b-instruct` (alternate
     Granite variant) or confirm in the watsonx Studio catalog.
   - `AI_UNAVAILABLE` with timeout → increase `AI_TIMEOUT_SECONDS=60` in `.env` and retry.
   - `AI_ABSTAIN` with "cited evidence not in bundle" → the validation layer is rejecting the
     model output; note the exact message and it will need a code fix (see context below).
4. Once the integration test is green, run the full suite to confirm nothing regressed:
   ```bash
   pytest
   ```
5. Restart the Streamlit app (`Ctrl+C` then `streamlit run app.py`) so it picks up the new
   `.env` values. Open Exception Review on any PRICE_VARIANCE case and click
   **Get AI suggestion** — the card should now show a Granite model name, not "stub-rules-v1".
6. Update `docs/test_results.md` to record the integration test result, the model ID used,
   and the date verified.

**Relevant Context:**
- `backend/ai_resolver.py` → `watsonx_generate()` makes the actual API call using
  `ibm_watsonx_ai.foundation_models.ModelInference`. The call uses `model.chat()` with
  `temperature=0` and `max_tokens=500`.
- `validate_explanation()` in the same file enforces all PRD Section 19 safety rules: unknown
  citation IDs, numbers that don't match the rules engine, invented identifiers, and
  disallowed actions all cause the narrative to be discarded and `AI_ABSTAIN` returned.
  Deterministic findings are always preserved regardless of AI output.
- The `AI_TIMEOUT_SECONDS` setting (default 20) controls how long the thread-pool waits
  before returning `AI_UNAVAILABLE`. If the Lite-plan endpoint is slow, bump this to 30–60.
- `ibm-watsonx-ai` is already in `requirements.txt` — no new dependency needed.

---

## Key Constraints (unchanged from build plan)

- AI module never performs arithmetic and never receives raw EDI text.
- Every AI claim must cite an evidence ID in the bundle; uncited claims are discarded.
- `AI_ABSTAIN` and `AI_UNAVAILABLE` are safe: deterministic findings and reviewer actions
  remain fully available.
- No secrets hardcoded anywhere — all credentials live in `.env` (gitignored).
- AT01–AT24 must still pass after the live provider is enabled.
