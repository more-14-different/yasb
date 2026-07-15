# Repository Notes

## Runtime Layout
- Original/Scoop YASB config: `C:\Users\Xcz\.config\yasb\config.yaml`; `C:\Users\Xcz\.config\yasb` is a junction to `D:\C2D\dotfiles\yasb`.
- Fork/dev YASB config: `D:\C2D\dotfiles\yasb-dev\config.yaml`; this is the config that mounts `pieces_density` and `pieces_toggle`.
- Fork launch scripts validate that `D:\C2D\dotfiles\yasb-dev\.yasb-config-role` contains `yasb-fork`, then set `YASB_CONFIG_HOME`. When auditing the fork, do not infer its active widgets from the original config under `C:\Users\Xcz\.config\yasb`.
- Do not overwrite config or theme files when working in this repo unless explicitly asked.
- Original Scoop YASB remains available at `E:\Scoop\apps\yasb\current\yasb.exe`.
- Dev build entrypoint is `E:\MCP\Projects\yasb-fork\src\dist\yasb.exe`.

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
