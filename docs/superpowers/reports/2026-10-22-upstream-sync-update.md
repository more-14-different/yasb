# 2026-10-22 Staged Upstream Sync Report

## Summary

This update brought `yasb-fork` from its previous upstream merge base to the
verified official YASB tip in five independently tested merge stages. The work
preserved the fork's Komorebi spatial workspace preview, Pieces integrations,
hung-window protection, reload lifecycle, and frozen Python 3.14 build support.
The merge implementation changed 198 files, with 8,289 insertions and 6,403
deletions relative to the Stage 1 plan baseline.

The completed integration result is commit
`fc8a0cb3d0b8e5ae1beb20d3cc566aa9135dc7f7` on branch
`sync/upstream-20261021-staged`. It has been pushed to the fork, but has not
been merged into the main development branch.

## Commit Boundaries

### Overall ranges

| History | Range | Meaning |
| --- | --- | --- |
| Fork work | `9045dcdc..fc8a0cb3` | Sync design, stage plans, and all five integration merges after the last pre-sync fork commit. |
| Merge implementation | `414f873c..fc8a0cb3` | The five tested upstream merge stages after the Stage 1 plan was established. |
| Official upstream | `a237644a..ff3d431e` | All official upstream changes absorbed by this update. |

Relevant anchors:

- Last fork commit before sync documentation: `9045dcdc` (`chore(lint): format patch helper script`).
- Previous official upstream merge base: `a237644af06f44ee3c1579e4b4449f63b4c4733f`.
- Sync design: `78e5d352` (`docs: design staged upstream sync`).
- Stage 1 plan baseline: `414f873c168a3c4188130624c71eddce8f141162`.
- Official upstream tip: `ff3d431e951a699b180118051f53b9def7ed3c15`.
- Final fork integration commit: `fc8a0cb3d0b8e5ae1beb20d3cc566aa9135dc7f7`.

### Stage mapping

| Stage | Official upstream range | Fork merge commit | Main result |
| --- | --- | --- | --- |
| 1 | `a237644a..29b5b57b` | `e9ef062583c668cc5d3fd571f958bd3b9eca3e07` | Non-blocking window actions without losing the fork's hung-window and Komorebi focus guards. |
| 2 | `29b5b57b..a4201949` | `d5aeab18965bb23578a0991f86ff2e45af6b4da3` | Unified asynchronous IconService, consolidated ctypes Win32 bindings, removal of `pywin32`, and CLI pipe cleanup. |
| 3 | `a4201949..fae3bded` | `80aba03aa9c33883e9d0f03238c539bc648537e0` | GPU, GlazeWM, Explorer recovery, multi-monitor auto-hide, dependencies, and CI reliability fixes. |
| 4 | `fae3bded..5fd4161b` | `910f9f10566ba9fdf2341ef2e46e0af7824df413` | Window Switcher live previews and Alt+Tab behavior plus dedicated-thread Windows desktop COM handling. |
| 5 | `5fd4161b..ff3d431e` | `fc8a0cb3d0b8e5ae1beb20d3cc566aa9135dc7f7` | Pydantic/schema compatibility, dependency updates, and Server Monitor TLS/certificate fixes. |

Each fork merge commit has the preceding fork stage as its first parent and the
listed official checkpoint as its second parent. Consequently,
`ff3d431e951a699b180118051f53b9def7ed3c15` is an ancestor of the final result,
and `HEAD..origin/main` contained zero remaining official upstream commits when
the final stage was completed.

## Framework Improvements

### Non-blocking and resilient window operations

Window restore, minimize, maximize, and foreground operations now use the
upstream asynchronous command path instead of waiting synchronously for the
target application. A hung application is therefore much less likely to stall
the bar. The fork's validity checks, `IsHungAppWindow` guards, bounded close
behavior, cursor correction, and Komorebi focus helpers remain in place.

### Unified asynchronous icon architecture

Taskbar, Launchpad, Quick Launch, Window Switcher, and the fork's Komorebi
spatial preview now share one `IconService`, cache, source model, and
`icons_changed` refresh mechanism. This removes duplicate icon extraction
paths, improves website icon/title discovery, and lets late asynchronous icon
results repaint workspace tiles without degrading the preview to row mode.

### Consolidated native Windows layer

Windows declarations and constants are consolidated under the ctypes Win32
bindings. Uses of `win32api`, `win32con`, `win32gui`, `win32process`,
`win32security`, and `win32com` were migrated, allowing the `pywin32`
dependency to be removed. COM automation that remains necessary uses the
project's current bindings/comtypes architecture.

### Better lifecycle and multi-monitor behavior

- Windows virtual-desktop COM work runs on its own thread.
- Stale virtual-desktop COM objects are reset after Explorer restarts.
- Auto-hide final positioning uses the native `QWindow`, preventing the bar's
  monitor HWND from becoming `None` on multi-monitor systems.
- CLI pipe and single-instance responsibilities are separated while the fork's
  `--restart-parent-pid`, previous-process wait, and forced-exit protections
  remain intact.
- GlazeWM subscription events avoid unrelated queries.
- GPU utilization is calculated consistently with Windows Task Manager.

### Stronger build and verification framework

The build gained architecture-aware Everything DLL selection. The
fork-specific inclusion of `python3.dll` and the versioned Python 3.14 runtime
DLL from `sys.base_prefix` remains intact. Test coverage now includes Win32
declarations and ABI checks, asynchronous window behavior, native widget
behavior, Windows desktop boundaries, and Server Monitor HTTP, HTTPS,
certificate, port, redirect, and TLS 1.2 behavior. CI also reports Pyright
results more clearly and pins its tooling more predictably.

## User-Visible and Configurable Improvements

### Window Switcher

- Optional live window previews with configurable preview width.
- Alt+Tab-style modifier keybindings: cycle while held and select on release.
- More accurate hover-to-focus behavior.
- Configurable popup fade duration and improved thumbnail lifecycle.

### Windows Desktops

- `label_workspace_populated_btn` can distinguish inactive desktops that
  contain windows.
- `.windows-desktops .ws-btn.populated` provides a dedicated CSS state.
- Desktop state survives Explorer and COM lifecycle transitions more reliably.

### Server Monitor

- `ssl_verify` now correctly controls whether untrusted, expired, mismatched,
  or self-signed certificates make a server appear offline.
- Certificate expiry can still be reported when verification is disabled.
- Hostnames, explicit ports, full HTTP/HTTPS URLs, redirects, and custom user
  agents are handled more consistently.
- HTTPS regression coverage requires TLS 1.2.

### Schema and dependencies

- Pydantic was updated from `2.13.5` to `2.14.1`.
- certifi was updated from `2026.7.22` to `2026.10.19`.
- Widget type schema constants now use
  `Field(json_schema_extra={"const": ...})`, compatible with the updated
  Pydantic schema exporter.
- Other adopted dependency updates include Pillow `13.0.0`, tzdata `2026.5`,
  and holidays `0.106`.
- Although the upstream range contains a cx_Freeze `8.7.2` bump, the final fork
  configuration deliberately retains its existing cx_Freeze `8.6.4` pin.

## Configuration Compatibility

No user `config.yaml`, theme, shortcut, autostart entry, or Scoop installation
was modified during the sync. Existing fork configuration remains valid; the
new options are capabilities rather than mandatory migrations.

The defining fork configuration and behavior were preserved:

- `app_icons.display_mode: "layout_preview"` remains the intended steady-state
  Komorebi workspace presentation.
- `hide_label: false` continues to show workspace digits beside spatial icons.
- Row icons remain fallback-only rather than an alignment workaround.
- `pieces_density` and `pieces_toggle` retain Screenpipe, OBS, truth-ledger,
  session, per-monitor, tooltip, timing, geometry, and z-order behavior.
- The guarded `yasb-dev` configuration remains separate from the original
  Scoop YASB configuration.

## Preserved Fork-Specific Runtime Behavior

- Spatial workspace tiles, focus borders, hover and pending states, stack/deck
  hit testing, immediate repaint, and monitor rebinding.
- Focus, workspace switching, title/icon updates, and same-monitor or
  cross-monitor window moves.
- Hung-window rejection before focus or window actions.
- Mouse-follows-focus and Komorebi-specific cursor correction.
- Fork-only label parsing, bar animation signals, custom widgets, YouTube
  behavior, and Pieces data-source discovery.
- One-instance reload behavior without overlapping or zombie dev processes.
- Frozen Python 3.14 runtime DLL support.

## Verification Evidence

The final Stage 5 candidate passed:

- `2189` pytest tests plus `2` subtests.
- Ruff formatting and lint checks.
- Pyright with `0` errors and `0` warnings.
- Export of all `61` registered widget schemas.
- Frozen application build with `yasb.exe`, `yasbc.exe`, `python3.dll`,
  `python314.dll`, the Everything DLL, and `YASBTrayHook.dll`.
- Guarded startup and `yasbc reload`, replacing the old process with exactly
  one new isolated dev process.
- Two-monitor Komorebi connection with nine workspaces per monitor.
- Focus, workspace switching, same-monitor moves, cross-monitor moves, and
  restoration using disposable test windows.
- Visual inspection of both bars, spatial workspace previews, visible digits,
  and Pieces widgets after startup and reload.
- Clean startup/reload logs apart from existing configuration deprecation
  warnings.

Server Monitor was not mounted in the active fork config, so its runtime logic
was covered by the upstream local HTTP/HTTPS/TLS test suite rather than by
changing the user's configuration.

## Related Documents

- Design constraints: `docs/superpowers/specs/2026-10-22-staged-upstream-sync-design.md`
- Execution plans: `docs/superpowers/plans/2026-10-22-upstream-sync-stage-1.md`
  through `docs/superpowers/plans/2026-10-22-upstream-sync-stage-5.md`
- Window Switcher options: `docs/widgets/(Widget)-Window-Switcher.md`
- Windows Desktops options: `docs/widgets/(Widget)-Windows-Desktops.md`
- Server Monitor options: `docs/widgets/(Widget)-Server-Monitor.md`
