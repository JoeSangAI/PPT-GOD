# Repository Data Cleanup Design

**Goal:** Separate PPT God runtime/user data from program code and remove historical temporary/test artifacts without losing useful regression coverage.

**Approved data root:** `.pptgod-data/`

## Current State

- Runtime data is configured as `./uploads` and `./outputs` from the backend working directory, and Docker mounts host `backend/uploads` and `backend/outputs`.
- Those directories are already gitignored, but the local workspace contains large old data sets in `backend/uploads`, `backend/outputs`, root `outputs`, and root `output`.
- Formal regression tests live in `backend/tests/`, `frontend/src/*.test.mjs`, and explicit frontend e2e files.
- Root-level `backend/test_*.py` files are historical experiment scripts. Many depend on local absolute paths, generated image files, external API calls, or embedded API key strings.
- `backend/test_outputs/`, `.logs/`, `.playwright-cli/`, `.pytest_cache/`, `__pycache__/`, `.DS_Store`, local SQLite DBs, and root output folders are generated artifacts.

## Target Structure

```text
.pptgod-data/
  uploads/
  outputs/
  db/
  logs/
```

The application should default to `.pptgod-data/uploads` and `.pptgod-data/outputs` for local development. Docker should mount the same host directories into `/app/uploads` and `/app/outputs` so container URLs remain `/uploads/...` and `/outputs/...`.

## Cleanup Policy

Keep:

- Product source under `backend/app`, `frontend/src`, scripts, templates, docs, requirements, Docker files, and design assets.
- Formal tests in `backend/tests/`, `frontend/src/*.test.mjs`, and explicit frontend e2e files.
- Any valuable assertions from root `backend/test_*.py` after moving them into `backend/tests/`.

Remove:

- Root-level `backend/test_*.py` experiment scripts after preserving useful coverage.
- Tracked generated files under `backend/test_outputs/`.
- Local generated directories: `.logs/`, `.playwright-cli/`, `.pytest_cache/`, `__pycache__/`, root `output/`, root `outputs/`, `frontend/.playwright-cli/`, and `frontend/.pytest_cache/`.
- Local SQLite DB files from the code tree after moving/redirecting the default database into `.pptgod-data/db`.

## Code Changes

- Add a runtime data root setting, defaulting to `../.pptgod-data` from the backend package root.
- Default `UPLOAD_DIR`, `OUTPUT_DIR`, and local SQLite database path to that runtime data root unless explicitly overridden.
- Update Docker volumes to mount `.pptgod-data/uploads` and `.pptgod-data/outputs`.
- Keep static routes unchanged: `/uploads` and `/outputs`.
- Update `.gitignore` to include `.pptgod-data/` and keep legacy generated paths ignored.
- Add focused regression tests for the data-root defaults and any root experiment assertions that are still useful.

## Verification

- Run focused backend tests for configuration, path resolution, overlay layer behavior, and runtime schema health.
- Run frontend workflow tests if package scripts are available.
- Re-run `git status --short` and a generated-artifact scan to confirm the code tree no longer contains tracked temporary outputs or root experiment scripts.

## Non-Goals

- Do not delete formal regression tests.
- Do not rewrite user-facing copy or workflow text.
- Do not migrate production database contents.
- Do not change public URLs for uploaded files or generated outputs.
