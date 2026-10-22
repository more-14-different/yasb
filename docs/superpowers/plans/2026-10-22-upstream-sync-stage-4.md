# Upstream Sync Stage 4 Plan

## Goal

Merge upstream checkpoint `5fd4161b` into `sync/upstream-20261021-staged`, adopting the Window Switcher preview/key-release/hover behavior and Windows virtual-desktop COM-thread refactor while preserving all fork-specific Komorebi workspace previews, Pieces widgets, focus guards, restart behavior, and frozen-runtime support.

## Boundary

This checkpoint intentionally stops before `04153be6` and therefore excludes the validation-schema constant refactor, Pydantic upgrade, and server-monitor TLS/certificate changes. Those form the final stage.

## Conflict ownership

- `src/core/utils/utilities.py` is the only path changed both upstream and by the fork since Stage 3.
- Retain the fork's `LabelPart` and `parse_label_template` additions.
- Adopt upstream's configurable `PopupWidget` animation duration used by the Window Switcher.
- Do not alter the fork development config, themes, launch shortcuts, autostart, or Scoop installation.

## Execution

1. Merge `5fd4161b` with `--no-ff --no-commit` and review all overlapping semantics.
2. Add focused regression coverage for Window Switcher configuration, modifier-release selection, thumbnail lifecycle/hover behavior, and Windows desktop service event/thread boundaries where practical.
3. Run focused tests, Ruff formatting/lint, strict Pyright, and the complete pytest suite.
4. Build the frozen application and verify its runtime DLLs and bundled helpers.
5. Restart only the isolated candidate using the guarded `yasb-dev` config. Verify one process, clean logs, Komorebi reconnection, two monitors, spatial previews, Pieces overlays, workspace/focus/window-move behavior, and configuration reload.
6. Exercise the new widgets through a separate controlled smoke harness because they are not mounted by the active fork config.
7. Wait for user approval; then commit and push immediately before starting Stage 5.
