# Implementation Plan - Context/Cancellation/CVE Hardening

## 1. Scope

This pass focused on three stabilization goals:

1. Context-per-box correctness
2. Single authoritative cancellation and CVE JIT ownership
3. Focused async/manager tests to catch race and cleanup regressions early

## 2. Completed Work

### Phase 1: Context-Per-Box Correctness

- [x] Reworked Redis semantics in `src/cereal_killer/context_per_box.py` to use sync Redis client access for sync call paths.
- [x] Removed task-returning Redis helper behavior that could return unresolved `Task` objects in sync flows.
- [x] Added explicit replacement helpers:
    - `set_active_history(commands)`
    - `set_active_transcript(entries)`
- [x] Added app-level machine/context routing helpers in `src/cereal_killer/ui/app.py`:
    - `set_active_machine(machine)`
    - `set_active_history(commands)`
    - `set_active_transcript(entries)`
- [x] Wired target switches through app-level machine switching in:
    - `src/cereal_killer/ui/commands/command_handler.py`
    - `src/cereal_killer/ui/observers/terminal_observer.py`
- [x] Fixed transcript pruning replacement bug in `src/cereal_killer/ui/context/context_state.py` by replacing via app API instead of assigning to read-only active transcript property.

### Phase 2: Cancellation + CVE JIT Ownership

- [x] Centralized worker cancellation semantics through `WorkerLifecycleManager.with_worker_cancellation()`.
- [x] Updated lifecycle cancellation to skip cancelling the currently running task.
- [x] Updated all worker managers to delegate cancellation to app lifecycle path:
    - `chat_workers.py`
    - `search_workers.py`
    - `vision_workers.py`
    - `ingest_workers.py`
    - `misc_workers.py`
- [x] Routed CVE JIT through the authoritative app worker entrypoint (`_run_cve_jit_worker`) from command and observer flows.
- [x] Fixed async delegation in `src/cereal_killer/ui/app.py` to `await` manager worker methods (eliminates dropped coroutine calls).

### Phase 3: Async Cleanup Hardening

- [x] Added tracked background task set in `src/cereal_killer/ui/app.py` and cancellation on unmount.
- [x] Replaced nested untracked clipboard task creation with direct awaited observer clipboard watch.
- [x] Hardened readiness status update path in `src/cereal_killer/ui/observers/terminal_observer.py` against transient widget lookup errors.

### Phase 4: Focused Regression Tests

- [x] Added `tests/test_context_per_box.py`:
    - Redis-backed context load behavior
    - Active history replacement behavior
    - Active transcript replacement behavior
- [x] Added `tests/test_worker_lifecycle.py`:
    - Cancellation skips current task
    - Busy-state update safe with missing dashboard
- [x] Added `tests/test_session_manager.py`:
    - Persist scheduling uses active machine history context
- [x] Updated `tests/test_ui_layout.py` dummy app/engine stubs for new lifecycle hooks and deterministic test cleanup.

## 3. Verification Results

- [x] Targeted tests:
    - `PYTHONPATH=src python3 -m unittest -q tests.test_context_per_box tests.test_worker_lifecycle tests.test_session_manager tests.test_ui_layout`
    - Result: PASS

- [x] Full suite:
    - `PYTHONPATH=src python3 -m unittest discover -s tests -q`
    - Result: PASS (173 tests)

## 4. Files Touched (Plan Scope)

- `src/cereal_killer/context_per_box.py`
- `src/cereal_killer/ui/app.py`
- `src/cereal_killer/ui/context/context_state.py`
- `src/cereal_killer/ui/commands/command_handler.py`
- `src/cereal_killer/ui/observers/terminal_observer.py`
- `src/cereal_killer/ui/sessions/session_manager.py`
- `src/cereal_killer/ui/workers/worker_lifecycle.py`
- `src/cereal_killer/ui/workers/chat_workers.py`
- `src/cereal_killer/ui/workers/search_workers.py`
- `src/cereal_killer/ui/workers/vision_workers.py`
- `src/cereal_killer/ui/workers/ingest_workers.py`
- `src/cereal_killer/ui/workers/misc_workers.py`
- `tests/test_context_per_box.py`
- `tests/test_worker_lifecycle.py`
- `tests/test_session_manager.py`
- `tests/test_ui_layout.py`
