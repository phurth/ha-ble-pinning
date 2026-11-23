"""Telling which path a connected client's link actually uses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import normalize_source


@dataclass(frozen=True)
class ConnectionPath:
    """Where a connected client's link actually goes."""

    source: str | None
    """Home Assistant scanner source: local adapter MAC, or a proxy's MAC."""

    device_path: str | None
    """BlueZ device object path (``/org/bluez/hciN/dev_…``) for a local link."""

    def describe(self) -> str:
        """Return a short human-readable form for logs."""
        if self.device_path and self.source:
            return f"{self.source} ({self.device_path.rsplit('/', 1)[0]})"
        return self.source or self.device_path or "an unknown path"


def connection_path(client: Any) -> ConnectionPath:
    """Return the path a connected client actually uses (best effort).

    Reads habluetooth's record of the scanner it connected through and, for a
    local link, bleak's BlueZ device object path.  Either may be missing on
    other library versions; the fields are then None.
    """
    scanner = getattr(client, "_connected_scanner", None)
    source = getattr(scanner, "source", None) if scanner is not None else None
    backend = getattr(client, "_backend", None)
    device_path = getattr(backend, "_device_path", None)
    return ConnectionPath(
        source if isinstance(source, str) else None,
        device_path if isinstance(device_path, str) else None,
    )


def path_matches_pin(
    path: ConnectionPath,
    allowed_sources: frozenset[str],
    local_device_paths: frozenset[str] = frozenset(),
) -> bool | None:
    """Return whether *path* is an allowed one, or None when it cannot be told.

    *allowed_sources* are normalized scanner sources.  When the link is local
    and *local_device_paths* (the BlueZ device paths of the allowed adapters)
    are known, the device path decides.  Otherwise the scanner source does.
    """
    if path.device_path is not None and local_device_paths:
        return path.device_path in local_device_paths
    if path.source is not None:
        return normalize_source(path.source) in allowed_sources
    return None
