"""Self-install helpers (Hamlib download, dependency checks)."""

from . import hamlib_installer
from .dependencies import (
    DependencyKind,
    DependencyStatus,
    VARA_OFFICIAL_URL,
    inspect_dependencies,
)

__all__ = [
    "DependencyKind",
    "DependencyStatus",
    "VARA_OFFICIAL_URL",
    "hamlib_installer",
    "inspect_dependencies",
]
