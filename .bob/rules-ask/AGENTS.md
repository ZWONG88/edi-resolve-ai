# Project Documentation Rules (Non-Obvious Only)

- `backend/__init__.py` is empty — there is no existing implementation to reference; all modules must be created from scratch
- The only canonical domain description is `README.md`; treat it as the source of truth for EDI terminology and MVP scope
- "data/" subdirectories contain only `.gitkeep` files — no sample EDI data exists in the repo yet
- No API docs, no architecture diagrams, no ADRs exist — document decisions in README or inline comments as you build
