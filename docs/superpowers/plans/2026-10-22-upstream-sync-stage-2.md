# Upstream Sync Stage 2 Plan

## Goal

Merge upstream checkpoint `a4201949` into `sync/upstream-20261021-staged`, adopting the unified asynchronous IconService and ctypes Win32 bindings while retaining every fork-specific workspace-preview, Pieces, focus, restart, and frozen-runtime behavior.

## Known conflict ownership

- `src/core/widgets/komorebi/workspaces.py`: keep the fork layout-preview architecture and control flow; replace its icon-loading boundary with upstream `IconService`, `WindowIcon`, and `icons_changed` refreshes.
- `src/core/utils/win32/window_actions.py`: use upstream bindings/constants; retain the fork hung-window guards, cursor correction, and bounded close behavior.
- `src/core/widgets/yasb/taskbar.py`: take upstream IconService integration and non-blocking actions; retain fork hung-window immunity.
- `src/main.py`: take upstream single-instance module extraction; retain `--restart-parent-pid`, previous-process waiting, and forced-exit semantics.
- `tests/win32/test_window_actions.py`: preserve the Stage 1 regressions, port them to the new bindings, and combine them with upstream ABI/declaration coverage.

## Execution

1. Merge `a4201949` with `--no-ff --no-commit` and resolve the five predicted conflicts semantically.
2. Scan the entire repository for removed `pywin32` imports and old icon modules. Migrate fork-only uses, especially Pieces, workspace cursor correction, launchpad, and tests.
3. Add focused tests proving the fork spatial preview requests icons through `IconService` and refreshes when asynchronous icons arrive without hiding workspace digits.
4. Run focused IconService/workspace/Win32/native/import tests.
5. Run `ruff format --check .`, `ruff check .`, and strict Pyright with the root venv interpreter.
6. Run the complete pytest suite and report SDK-backed Win32 coverage.
7. Build the frozen app and verify Python runtime DLLs plus architecture-specific Everything and tray-hook DLLs.
8. Restart only the isolated candidate through the guarded `yasb-dev` config, verify one healthy process, Komorebi reconnection, dual-monitor spatial previews, taskbar/launchpad/window-switcher icons, Pieces overlays, window moves, and reload.
9. Wait for user approval; then commit and push immediately before starting Stage 3.

## Required commands

```powershell
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest tests/test_komorebi_workspaces.py tests/widgets/test_native_windows.py tests/smoke/test_imports.py tests/win32 -q
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m ruff format --check .
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m ruff check .
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pyright --pythonpath 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe'
& 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' -m pytest -q
Push-Location src
try { & 'E:\MCP\Projects\yasb-fork\.venv\Scripts\python.exe' build.py build } finally { Pop-Location }
```

No config, theme, shortcut, autostart, or Scoop installation file may be changed.
