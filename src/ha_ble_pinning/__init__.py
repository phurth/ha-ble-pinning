"""Pin a Home Assistant Bluetooth connection to chosen adapters or proxies."""

from .bluez import async_get_bonded_device_paths, bonded_device_paths_in
from .client import (
    PinnedAdapterUnavailable,
    make_pinned_client_class,
    normalize_source,
    pinned_candidates,
    pinned_client_class,
    stock_choice,
)
from .connect import establish_pinned_connection
from .path import ConnectionPath, connection_path, path_matches_pin

__version__ = "0.1.0"

__all__ = [
    "ConnectionPath",
    "PinnedAdapterUnavailable",
    "async_get_bonded_device_paths",
    "bonded_device_paths_in",
    "connection_path",
    "establish_pinned_connection",
    "make_pinned_client_class",
    "normalize_source",
    "path_matches_pin",
    "pinned_candidates",
    "pinned_client_class",
    "stock_choice",
]
