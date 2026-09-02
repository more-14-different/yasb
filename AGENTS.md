# Repository Notes

## Runtime Layout
- Original/Scoop YASB config: `C:\Users\Xcz\.config\yasb\config.yaml`; `C:\Users\Xcz\.config\yasb` is a junction to `D:\C2D\dotfiles\yasb`.
- Fork/dev YASB config: `D:\C2D\dotfiles\yasb-dev\config.yaml`; this is the config that mounts `pieces_density` and `pieces_toggle`.
- Fork launch scripts validate that `D:\C2D\dotfiles\yasb-dev\.yasb-config-role` contains `yasb-fork`, then set `YASB_CONFIG_HOME`. When auditing the fork, do not infer its active widgets from the original config under `C:\Users\Xcz\.config\yasb`.
- Do not overwrite config or theme files when working in this repo unless explicitly asked.
- Original Scoop YASB remains available at `E:\Scoop\apps\yasb\current\yasb.exe`.
- Dev build entrypoint is `E:\MCP\Projects\yasb-fork\src\dist\yasb.exe`.
- `pieces_density.options.truth_time_db_path: "auto"` resolves `EVENT_LOGGER_DB_PATH` first, then discovers a sibling `event-logger` repo, then checks `%LOCALAPPDATA%\event-logger`; use an explicit path only when discovery is inappropriate.

## Local Build Workflow
- Preferred local Python is the repo venv at `.venv\Scripts\python.exe`.
- Build a self-contained dev bundle with `.\build-dev.ps1`.
- Run from source with `.\run-dev.ps1`.
- Rebuild and relaunch the dev frozen app with `.\restart-dev-dist.ps1`.
- `src\build.py` explicitly includes `python3.dll` and `python314.dll` from `sys.base_prefix` because local `uv` Python installs do not place those DLLs beside the venv executable.

## Start Menu / Startup
- Start menu has two entries:
  - `YASB` -> Scoop/original build
  - `YASB Dev` -> `src\dist\yasb.exe`
- Autostart currently points at the dev build entrypoint above; do not switch it back to Scoop unless explicitly asked.

## Editing Guidance
- Changes to workspace icon click behavior live in `src\core\widgets\komorebi\workspaces.py`.
- When validating frozen builds, stop any running dev `yasb.exe` from `src\dist` before cleaning or rebuilding `src\dist`.

## Workspace Preview UI Invariants
- The fork's defining workspace UI is the non-row `app_icons.display_mode: "layout_preview"`: application logos form a compact spatial preview of the Komorebi workspace beside its digit button. Do not switch it to `row` as a workaround for alignment, missing-logo, or refresh bugs.
- The fork config intentionally uses `app_icons.hide_label: false`; workspace digits must remain visible even when that workspace has logos. `hide_label: true` deterministically removes the digit for every workspace whose preview contains an icon.
- `WorkspaceButtonWithIcons` owns the digit label and a preview anchor, while `WorkspaceLayoutPreview` paints the spatial tiles in an owned overlay positioned from that anchor. Diagnose dual-monitor displacement or missing logos in monitor-to-widget binding, Komorebi window rectangles, preview compaction, overlay geometry/z-order, and event refresh timing.
- Row icons are only the implementation's fallback when a layout preview cannot be constructed. Seeing row icons is evidence that preview construction failed; it is not the intended steady-state layout.
- When validating workspace UI changes, inspect both monitors and test window focus, window moves between workspaces, workspace switches, and config reloads. Preserve the fork/original config boundary described above.
