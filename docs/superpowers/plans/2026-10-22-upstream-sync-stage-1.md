# Upstream Sync Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge upstream `29b5b57b` into the fork while retaining hung-window immunity and Komorebi focus behavior, then produce a frozen candidate for user-approved two-monitor validation.

**Architecture:** Merge the upstream branch tip with `--no-ff --no-commit`, resolve the sole textual conflict in the shared window-actions layer, and retain the automatically merged taskbar and switcher consumers. The fork owns hung-window guards and cursor/focus policy; upstream supplies non-blocking `PostMessage`, `ShowWindowAsync`, and asynchronous `SetWindowPos` behavior.

**Tech Stack:** Python 3.14, PyQt6, pywin32 (still present in Stage 1), ctypes Win32 bindings, pytest, Ruff, Pyright, cx_Freeze, Komorebi.

**Spec:** `docs/superpowers/specs/2026-10-22-staged-upstream-sync-design.md`

## Global Constraints

- Work only in `E:\MCP\Projects\yasb-fork\.worktrees\sync-upstream-20261021` on `sync/upstream-20261021-staged`.
- Do not edit `D:\C2D\dotfiles\yasb-dev\config.yaml`, its theme, the Scoop/original YASB, Start Menu targets, or autostart.
- Preserve `layout_preview`, visible workspace digits, Pieces widgets, fast/pending focus behavior, hung-window guards, reload handling, and Python runtime DLL packaging.
- Keep the Stage 1 merge uncommitted and unpushed until the user visually approves the running candidate.
- After approval, create one merge commit for the complete stage, run `git swap auto`, and push `sync/upstream-20261021-staged`.
- Use `E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe` for Python tooling and frozen builds; do not create or modify a second environment.
- Any plan deviation must be recorded in the execution ledger before continuing.

## Review Focus

- A hung foreground target must return before `GetAncestor`, thread attachment, `SetWindowPos`, or focus calls.
- Restore/minimize/maximize must post `WM_SYSCOMMAND` without synchronously waiting on the target; failed posts must use `ShowWindowAsync`.
- `set_foreground` must retain the fork guard while using upstream asynchronous top-window positioning.
- Taskbar, popup Window Switcher, and Quick Launch Window Switcher must focus/restore through the shared helpers and retain safe fallbacks.
- Komorebi workspace icon clicks and mouse-follows-focus correction must retain their fork-specific cursor and pending-state behavior even though the shared helper changes.

---

### Task 1: Establish the isolated baseline and pin Stage 1 behavior

**Files:**
- Modify: `tests/win32/test_window_actions.py`
- Reference: `src/core/utils/win32/window_actions.py`

**Interfaces:**
- Consumes: existing `resolve_base_and_focus(hwnd)`, `restore_window(hwnd)`, `minimize_window(hwnd)`, `maximize_window(hwnd)`, `set_foreground(hwnd)`.
- Produces: regression tests defining asynchronous window-command behavior and preservation tests defining hung-window short-circuit behavior.

- [ ] **Step 1: Verify the worktree and toolchain baseline**

Run:

```powershell
git status --short --branch
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest tests/win32/test_window_actions.py tests/test_komorebi_workspaces.py tests/widgets/test_native_windows.py -q
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m ruff check .
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pyright --pythonpath 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe'
```

Expected: clean `sync/upstream-20261021-staged` worktree except the committed plan/spec history; all commands exit 0. If an existing baseline check fails, diagnose it before merging.

- [ ] **Step 2: Add the failing asynchronous-command tests**

Add focused monkeypatch tests:

- `test_restore_posts_syscommand_without_waiting`
- `test_failed_post_falls_back_to_show_window_async`
- `test_set_foreground_uses_async_set_window_pos`

The assertions pin `PostMessage(hwnd, WM_SYSCOMMAND, command, 0)`, the correct async fallback command, and `SWP_ASYNCWINDOWPOS | SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE`.

- [ ] **Step 3: Run the new tests and observe RED**

Run:

```powershell
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest tests/win32/test_window_actions.py -k 'posts_syscommand or failed_post or async_set_window_pos' -q
```

Expected: FAIL because the fork still uses `SendMessageTimeoutW` and thread attachment rather than the upstream non-blocking implementation.

- [ ] **Step 4: Add preservation coverage for hung-window guards**

Add `test_hung_window_short_circuits_resolution_and_foreground`, monkeypatching the validity/hung checks and making downstream native calls raise if reached.

- [ ] **Step 5: Run the preservation test on the pre-merge fork**

Run:

```powershell
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest tests/win32/test_window_actions.py -k hung_window_short_circuits -q
```

Expected: PASS, proving the fork behavior that the merge must preserve. This is a characterization gate, not a newly implemented behavior.

### Task 2: Merge `29b5b57b` and resolve window-actions semantics

**Files:**
- Modify: `src/core/utils/win32/window_actions.py`
- Merge: `src/core/utils/win32/aumid.py`
- Merge: `src/core/widgets/services/quick_launch/providers/window_switcher.py`
- Merge: `src/core/widgets/yasb/taskbar.py`
- Merge: `src/core/widgets/yasb/window_switcher.py`
- Test: `tests/win32/test_window_actions.py`

**Interfaces:**
- Consumes: Task 1's tests and the fork's `move_cursor_to_window_center(hwnd)`/hung-window policy.
- Produces: non-blocking shared window helpers used by Taskbar, Quick Launch, Window Switcher, and Komorebi workspaces.

- [ ] **Step 1: Start the uncommitted upstream merge**

Run:

```powershell
git merge --no-ff --no-commit 29b5b57b
```

Expected: one content conflict in `src/core/utils/win32/window_actions.py`; taskbar and the three other consumers auto-merge.

- [ ] **Step 2: Resolve `window_actions.py` according to the spec**

Implement upstream `_post_sys_command(hwnd: int, cmd: int, fallback_show_cmd: int) -> None` and asynchronous `set_foreground(hwnd: int) -> None`. Retain:

- validity and `IsHungAppWindow` checks in `resolve_base_and_focus`, `set_foreground`, and `force_foreground_focus`;
- `move_cursor_to_window_center(hwnd)`;
- safe close behavior and its bounded `SendMessageTimeoutW` usage;
- existing exported function names consumed by fork code.

- [ ] **Step 3: Verify consumer merges did not bypass shared helpers**

Inspect the staged diff and confirm Taskbar, Quick Launch Window Switcher, and Window Switcher use `restore_window`, `show_window`, and `set_foreground` instead of reintroducing synchronous focus fallbacks. Confirm the Taskbar entry guard still rejects hung HWNDs.

Run:

```powershell
git diff --check
git diff --cc -- src/core/utils/win32/window_actions.py
rg -n 'SendMessage\(|SetActiveWindow|force_foreground_focus' src/core/widgets/yasb/taskbar.py src/core/widgets/yasb/window_switcher.py src/core/widgets/services/quick_launch/providers/window_switcher.py
```

Expected: no whitespace errors or unresolved markers; no newly introduced synchronous fallback in these consumers. Existing unrelated definitions are documented if found.

- [ ] **Step 4: Run the Stage 1 RED tests and preservation tests**

Run:

```powershell
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest tests/win32/test_window_actions.py -q
```

Expected: all tests PASS, including the previously failing asynchronous tests and the fork preservation test.

- [ ] **Step 5: Run focused consumer regression tests**

Run:

```powershell
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest tests/test_komorebi_workspaces.py tests/widgets/test_native_windows.py tests/smoke/test_imports.py -q
```

Expected: PASS with only previously documented environment skips.

### Task 3: Complete automated verification and build the isolated frozen candidate

**Files:**
- Verify: repository source and tests
- Generate (ignored): `src/build/`, `src/dist/`

**Interfaces:**
- Consumes: the resolved uncommitted Stage 1 merge.
- Produces: a verified frozen executable at the isolated worktree's `src/dist/yasb.exe`.

- [ ] **Step 1: Run formatting, lint, typing and stale-marker checks**

Run:

```powershell
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m ruff format --check .
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m ruff check .
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pyright --pythonpath 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe'
$markers = rg -n '^(<<<<<<<|=======|>>>>>>>)' . --glob '!src/build/**' --glob '!src/dist/**'
if ($LASTEXITCODE -eq 0) { $markers; throw 'Unresolved conflict markers found' }
if ($LASTEXITCODE -ne 1) { throw 'Conflict-marker scan failed' }
```

Expected: all tool commands exit 0 and no conflict markers are found.

- [ ] **Step 2: Run the complete test suite**

Run:

```powershell
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest -q
```

Expected: PASS; report the exact passed/skipped counts and whether SDK-backed Win32 checks ran or skipped.

- [ ] **Step 3: Build without changing shortcuts or autostart**

From `src`, run the root environment's interpreter directly rather than `build-dev.ps1`:

```powershell
Push-Location src
try {
    & 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' build.py build
} finally {
    Pop-Location
}
```

Expected: exit 0 and `src/dist/yasb.exe` exists in the isolated worktree.

- [ ] **Step 4: Verify frozen runtime files**

Check `src/dist` for `yasb.exe`, `python3.dll`, and `python314.dll`. Check `src/dist/lib` for `YASBTrayHook.dll` and `Everything64.dll` (or the correct architecture equivalents); cx_Freeze places the extension DLLs under `lib` in this build layout.

Expected: every required runtime file exists and the executable path resolves inside the isolated worktree.

### Task 4: Restart the isolated candidate and run the Stage 1 live gate

**Files:**
- Read only: `D:\C2D\dotfiles\yasb-dev\.yasb-config-role`, config and logs
- Runtime only: isolated `src/dist/yasb.exe`

**Interfaces:**
- Consumes: Task 3's frozen executable.
- Produces: process, Komorebi-state, screenshot and user-observation evidence for Stage 1.

- [ ] **Step 1: Resolve the fork config through the guard and replace only the running dev process**

Resolve `YASB_CONFIG_HOME` with `assert-dev-config.ps1`. Record the current `yasb.exe` path, stop only the fork/dev process, and start the isolated executable hidden with the guarded environment.

Expected: exactly one fork `yasb.exe` runs from the isolated worktree; Scoop/original remains untouched.

- [ ] **Step 2: Verify process and Komorebi health**

Confirm the process remains responsive, inspect the newest YASB log for startup exceptions, and query Komorebi state for both monitors/workspaces.

Expected: no startup traceback or repeated disconnect loop; both physical monitors appear and YASB receives Komorebi state.

- [ ] **Step 3: Exercise Stage 1 focus behavior with disposable windows**

Use temporary test windows to exercise restore, minimize, taskbar focus, Window Switcher focus, workspace-preview focus, workspace switching, and a cross-workspace move. Do not move existing user work windows.

Expected: actions return promptly, the bar remains responsive, focus/pending indicators converge, and previews update on source and destination workspaces.

- [ ] **Step 4: Check both monitors and fork-only UI invariants**

Capture both monitors after the operations and verify layout previews remain spatial, digits remain visible, overlays stay attached to the correct bar, and Pieces controls/overlay retain their expected z-order.

Expected: no row-mode regression, missing digits, displaced overlays, stale focus border, or monitor swap.

- [ ] **Step 5: Report evidence and wait for the user**

Leave the candidate running. Report automated counts, build artifact path, executable PID/path, log status, Komorebi monitor state, live operations exercised, and screenshots. Do not commit or push.

Expected: explicit user confirmation that the visible result is correct.

### Task 5: Commit and push Stage 1 after explicit approval

**Files:**
- Commit: all resolved Stage 1 merge changes, tests, corrected spec and this plan

**Interfaces:**
- Consumes: explicit user approval from Task 4.
- Produces: one pushed Stage 1 merge commit on `sync/upstream-20261021-staged`.

- [ ] **Step 1: Reconfirm the approval and staged scope**

Run `git status`, `git diff --check`, and inspect the staged/unstaged summary. Confirm `29b5b57b` will be the second parent and no generated build files are tracked.

Expected: only intended source, tests and documentation are included.

- [ ] **Step 2: Create the Stage 1 merge commit**

Commit message:

```text
merge: absorb upstream non-blocking window actions
```

Expected: a two-parent merge commit whose second parent is `29b5b57b`.

- [ ] **Step 3: Select the GitHub identity and push**

Run:

```powershell
git swap auto
git push -u origin sync/upstream-20261021-staged
```

Expected: push succeeds to `more-14-different/yasb`; fetch remains configured for `amnweb/yasb`.

- [ ] **Step 4: Record the pushed commit and stop before Stage 2**

Record the remote branch tip and test evidence in the execution ledger. Do not merge the next checkpoint until Stage 2's topology and plan are reviewed.
