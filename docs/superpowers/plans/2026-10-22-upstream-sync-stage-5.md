# Upstream Sync Stage 5 Plan

## Goal

Merge the verified official upstream tip `ff3d431e` into `sync/upstream-20261021-staged`, completing the staged catch-up while preserving every fork-specific Komorebi workspace preview, Pieces widget, focus guard, restart path, and frozen-runtime behavior.

## Scope

- Adopt Pydantic `2.14.1` and the schema-export `Field(json_schema_extra={"const": ...})` compatibility refactor.
- Adopt certifi `2026.10.19`.
- Adopt Server Monitor URL, SSL verification, certificate parsing, custom-port, redirect, user-agent, and TLS 1.2 fixes with their upstream regression suite.
- Preserve all unrelated fork dependency pins and build/test tooling.

## Conflict ownership

`pyproject.toml` is the only conflicting path. The fork changed only its repository line-ending normalization since the common upstream checkpoint. Resolve from the fork copy, update only Pydantic and certifi to the upstream versions, then re-normalize according to `.gitattributes`.

## Verification

1. Run focused schema, dependency, Server Monitor, and fork-feature tests.
2. Run Ruff formatting/lint, strict Pyright, and the full pytest suite.
3. Build the frozen app and verify all required executables, Python DLLs, Everything, and tray-hook artifacts.
4. Restart only the isolated candidate through the guarded `yasb-dev` config.
5. Verify clean startup/reload logs, two unpaused Komorebi monitors, spatial workspace previews with digits, Pieces widgets, focus and cross-monitor window moves, and config reload.
6. Confirm `HEAD` contains upstream `ff3d431e`, the tree is conflict-free, and no upstream commits remain.
7. Wait for user approval; then commit and push.

No config, theme, shortcut, autostart, or Scoop installation file may be changed.
