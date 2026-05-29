# Editable PPTX Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a post-generation "download editable PPTX" flow that creates a separate editable export from finished slide images without changing the main full-slide image pipeline.

**Architecture:** The backend adds an `editable_pptx` ProjectRun and Celery task that reads completed slide images, asks MiniMax VLM for text boxes, rebuilds a conservative editable PPTX, and writes `editable_presentation.pptx`. The frontend adds a secondary export button that starts the task, reuses workflow polling for progress, and downloads the editable file once ready.

**Tech Stack:** FastAPI, SQLAlchemy ProjectRun, Celery, MiniMax Token Plan VLM via existing provider credentials, python-pptx, Pillow, React/Vite.

---

### Task 1: Backend Service

**Files:**
- Create: `backend/app/services/editable_pptx_export.py`
- Test: `backend/tests/test_editable_pptx_export.py`

- [ ] Write tests for MiniMax OCR parsing, conservative filtering, same-level font normalization, and editable PPTX creation from image paths.
- [ ] Implement MiniMax OCR request helper that returns normalized top-left bbox text regions.
- [ ] Implement visual block detection, background text cleanup, and text insertion rules based on the V8 prototype.
- [ ] Export one PPTX with full-slide background, cropped replaceable visual assets, and native text boxes.

### Task 2: Backend API And Task

**Files:**
- Modify: `backend/app/tasks.py`
- Modify: `backend/app/api/slides.py`
- Modify: `backend/app/services/run_state.py`
- Modify: `backend/app/celery_app.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_run_state.py`

- [ ] Add `editable_pptx` progress labels and route the task to the text queue.
- [ ] Add `generate_editable_pptx_task` to run the service and finish the ProjectRun.
- [ ] Add `POST /projects/{project_id}/editable-pptx` to start the task.
- [ ] Add `GET /projects/{project_id}/download-editable` to serve the result.
- [ ] Add workflow status fields `has_editable_pptx` and `editable_pptx_path`.

### Task 3: Frontend Flow

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/hooks/useProjectWorkflow.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/src/project-isolation.test.mjs`

- [ ] Add API helpers for starting and downloading editable PPTX.
- [ ] Extend workflow types with editable export state.
- [ ] Add a secondary export button: disabled before full PPT exists, starts processing if missing, downloads when ready.
- [ ] Show active `editable_pptx` progress with the existing workflow progress UI.

### Task 4: Verification

**Files:**
- Existing test files only.

- [ ] Run focused backend tests for editable export and run state.
- [ ] Run focused frontend source guard tests.
- [ ] Run frontend build.
- [ ] Confirm the normal `导出 PPTX` path still points to the original file and the editable flow uses a separate file.
