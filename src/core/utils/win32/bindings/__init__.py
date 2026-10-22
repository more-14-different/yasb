"""Typed ctypes Win32 bindings.

Keep this package boundary explicit so linked worktrees cannot be shadowed by
an older editable checkout that still exposes the former aggregate bindings.
Import APIs from their concrete DLL modules.
"""
