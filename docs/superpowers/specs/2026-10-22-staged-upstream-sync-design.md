# Staged Upstream Sync Design

## Objective

Bring `yasb-fork` from its current upstream merge base, `a237644a`, to upstream `ff3d431e` in four independently testable stages while preserving every fork-specific behavior. Each stage must build, run, and pass automated and live multi-monitor validation before it is committed and pushed.

The work will happen on branch `sync/upstream-20261021-staged` in the isolated worktree `.worktrees/sync-upstream-20261021`. The original checkout and the Scoop/original YASB installation remain untouched.

## Upstream Checkpoints

Each checkpoint is merged with `--no-ff --no-commit` so the resulting stage commit records the upstream checkpoint as a parent and the final branch contains `ff3d431e` in its ancestry.

1. `29b5b57b` — GlazeWM query reduction, GPU utilization correction, non-blocking window actions, initial IconService/taskbar/launchpad work where required by ancestry.
2. `a4201949` — complete IconService consolidation, Win32 bindings migration, `pywin32` removal, CLI pipe cleanup, and associated tests.
3. `57cd65a7` — Windows Desktops COM-thread work, Explorer recovery, Window Switcher live previews and keyboard behavior, auto-hide monitor fix, and CI improvements.
4. `ff3d431e` — dependency upgrades, validation changes, Server Monitor TLS fixes and tests, and all remaining upstream changes.

The exact commits in a checkpoint are determined by Git ancestry, not by selectively recreating upstream patches. If a checkpoint proves inseparable from a later prerequisite, the checkpoint may be advanced, but it may not skip the validation and approval gate.

## Fork Behaviors That Must Survive

### Komorebi workspace UI

- `app_icons.display_mode: "layout_preview"` remains the defining steady-state mode.
- Workspace application logos remain a compact spatial preview beside the workspace digit; row icons remain fallback-only.
- `hide_label: false` keeps workspace digits visible when icons exist.
- `WorkspaceButtonWithIcons`, `WorkspaceLayoutPreview`, the custom layout engine, owned overlay geometry, stack/deck hit testing, focus borders, hover state, pending state, immediate repaint, and monitor rebinding remain functional.
- Focus, window moves, workspace switches, config reloads, display changes, empty workspaces, floating windows, and title/icon changes refresh without misplaced or stale overlays.

### Fork widgets and integrations

- `pieces_density` and `pieces_toggle` retain Screenpipe, OBS, truth-ledger, session persistence, per-monitor control, tooltip, timing, overlay geometry, and z-order behavior.
- Screenpipe graph/effective-frame-count behavior and delayed bucket refresh remain intact.
- Fork-only custom widget, YouTube scraper, label parsing, bar animation, and monitor-binding behavior remain intact.

### Reliability and lifecycle

- Hung windows are rejected before synchronous focus/window work through `IsHungAppWindow`-equivalent checks.
- Fast workspace focus, pending feedback, mouse-follows-focus correction, and Komorebi-specific cursor behavior remain intact.
- Reload retains `--restart-parent-pid`, previous-process waiting, CLI-server ordering, and forced process-exit behavior that prevents overlapping or zombie instances.
- The dev config role guard and separation from the Scoop/original configuration remain intact.

### Frozen build

- `src/build.py` continues to include `python3.dll` and the versioned Python runtime DLL from `sys.base_prefix` for local uv Python builds.
- Upstream architecture-aware Everything DLL selection is added without removing the fork runtime-DLL fix.
- The resulting executable is launched from the isolated worktree, not installed over the Scoop/original executable and not made the permanent autostart target during validation.

## Integration Rules

### IconService

Adopt upstream's unified asynchronous `IconService`, cache and source model. Preserve the fork workspace architecture and replace only its icon acquisition/cache boundary. Workspace previews must subscribe to `icons_changed`, preserve per-window identity, and repaint spatial tiles without degrading to row mode.

### Win32 bindings and `pywin32`

Adopt upstream bindings and constants. Before removing `pywin32`, migrate every fork-only import, including Pieces, workspace preview, focus helpers, taskbar guards and scripts. A repository-wide scan for `win32api`, `win32con`, `win32gui`, `win32process`, `win32security`, `win32com`, and removed icon modules must be clean except for deliberately documented compatibility code.

### Conflict ownership

- `src/core/widgets/komorebi/workspaces.py`: fork architecture owns control flow and UI behavior; upstream supplies IconService and compatible API migrations.
- `src/core/utils/win32/window_actions.py`: upstream bindings own the low-level calls; fork hung-window guards and Komorebi focus helpers remain.
- `src/core/widgets/yasb/taskbar.py`: upstream IconService and non-blocking actions are adopted; fork hung-window immunity remains.
- `src/main.py` and single-instance support: upstream module extraction is adopted; fork restart-parent and forced-exit behavior remains.
- `src/build.py`: upstream Everything architecture selection and fork Python-runtime inclusion are combined.

No conflict is resolved wholesale with `ours` or `theirs` when both sides contain required behavior.

## Stage Workflow and Approval Gate

For each checkpoint:

1. Record the clean starting commit and merge the checkpoint with `git merge --no-ff --no-commit`.
2. Resolve conflicts according to this design and migrate semantic dependencies that Git cannot detect.
3. Add or update focused regression tests before changing fork behavior; observe the relevant failure before implementing the adaptation where a new behavior test is needed.
4. Run formatting/linting, Pyright, targeted tests, the complete test suite, stale-import checks, and a frozen build.
5. Stop any isolated-worktree `yasb.exe`, rebuild it, and launch it with `YASB_CONFIG_HOME` obtained through the config-role guard.
6. Confirm the running executable path, process health and logs; confirm Komorebi is online and YASB receives state changes.
7. Exercise the live matrix on both monitors and capture evidence.
8. Leave the candidate running and report the stage results to the user.
9. Wait for explicit user confirmation. Before confirmation, do not create the stage merge commit and do not push.
10. After confirmation, commit the full stage, run `git swap auto`, and push the stage branch. Only then begin the next checkpoint.

The preliminary design and plan commits may be made locally to establish the work record, but they are not pushed until the first user-approved stage push.

## Automated Validation

The exact commands will be pinned in the implementation plan after the isolated baseline is measured. Every stage must include:

- Ruff formatting/lint checks relevant to changed files.
- strict Pyright over the repository.
- targeted tests for the files and behavior changed in that checkpoint.
- complete `pytest` suite using the repository virtual environment.
- Win32 ABI/declaration tests, reporting any SDK-dependent skips explicitly.
- repository-wide stale-import and deleted-module scans.
- successful frozen build from the isolated worktree using the existing repository venv/interpreter.
- confirmation that the frozen artifact contains the required Python runtime and architecture-specific DLLs.

A baseline failure is investigated before upstream integration so it is not confused with a regression.

## Live Validation Matrix

Live validation uses temporary, disposable windows and avoids moving the user's existing work windows where practical.

- Both physical monitors show the intended bar and bind to the correct Komorebi monitor.
- Workspace digits remain visible beside layout previews.
- Empty, single-window, tiled, stacked/decked, floating and full-width layouts render spatially.
- Mouse and keyboard workspace switching update focus and pending states immediately.
- Clicking preview tiles focuses the intended non-hung window without freezing the bar.
- Moving a temporary window within and between workspaces updates both source and destination previews.
- Moving/switching across monitors does not displace overlays or swap monitor state.
- Hover, last-focused and active borders remain correct.
- Config reload produces one healthy YASB process and restores previews.
- Pieces overlay and controls remain monitor-local, positioned correctly and beneath/above the intended windows.
- Window Switcher, taskbar, launchpad and Windows Desktops changes introduced by the relevant stage are exercised.

Automated evidence and screenshots support the check, but the user is the final visual gate before each stage commit and push.

## Failure and Recovery

- A failed stage remains uncommitted and unpushed while it is debugged.
- The next upstream checkpoint is never merged until the current stage has user approval, a commit and a successful push.
- Existing user configuration and theme files are not edited to make a stage pass.
- The original checkout remains available as the recovery build. The isolated candidate can be stopped without changing Start Menu or autostart targets.
- If a live test could materially disturb current windows or recording state, reduce it to disposable windows or ask before performing it.

## Completion Criteria

The sync is complete when:

- `ff3d431e` is an ancestor of `sync/upstream-20261021-staged`.
- All four user-approved stage commits have been pushed.
- full automated validation passes at the final stage, with skips disclosed.
- the final frozen build runs from the fork config and passes the two-monitor Komorebi/Pieces validation matrix.
- no defining fork behavior has been replaced by upstream's row-only or generic behavior.
- the source checkout remains clean and the original Scoop YASB/config boundary remains unchanged.
