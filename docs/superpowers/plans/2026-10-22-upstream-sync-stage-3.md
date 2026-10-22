# Upstream Sync Stage 3 Plan

## Goal

Merge upstream checkpoint `fae3bded` into `sync/upstream-20261021-staged`, absorbing the GPU, Windows desktop, GlazeWM, multi-monitor auto-hide, dependency, and CI fixes while preserving every fork workspace-preview, Pieces, focus, animation, restart, and frozen-runtime behavior.

## Boundary

This checkpoint intentionally stops before upstream's Window Switcher refactor and Windows virtual-desktop COM-thread feature. Those larger UI/runtime changes belong to the next stage and will receive their own live gate.

## Conflict ownership

- `src/core/bar_helper.py` is the only file changed both upstream and by the fork since the Stage 2 merge base.
- Retain the fork's `opacity_tick`, `animation_tick`, and `animation_finished` signal boundaries.
- Adopt upstream's native `QWindow.setPosition` finalization so auto-hidden bars do not lose their monitor HWND on multi-monitor systems.

## Execution

1. Merge `fae3bded` with `--no-ff --no-commit` and inspect the automatically combined `bar_helper.py` semantics.
2. Add or retain regression coverage for fork animation signals and upstream monitor-HWND preservation.
3. Run focused bar, GPU, GlazeWM, Windows-desktop, dependency, and import tests.
4. Run Ruff formatting/lint, strict Pyright, and the full pytest suite.
5. Build the frozen app and verify runtime DLLs.
6. Restart only the isolated candidate through the guarded `yasb-dev` config. Verify one process, clean logs, Komorebi reconnection, two monitors, spatial previews, Pieces overlays, workspace/focus/move behavior, and reload.
7. Wait for user approval; then commit and push immediately before starting Stage 4.

No config, theme, shortcut, autostart, or Scoop installation file may be changed.
