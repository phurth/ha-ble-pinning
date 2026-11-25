"""Pin a Home Assistant Bluetooth connection to chosen adapters or proxies."""

from .bluez import async_get_bonded_device_paths, bonded_device_paths_in
from .choice import (
    AUTOMATIC,
    AUTOMATIC_LABEL,
    adapter_options,
    establish_connection_via,
    is_automatic,
)
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

__version__ = "0.2.0"

__all__ = [
    "AUTOMATIC",
    "AUTOMATIC_LABEL",
    "ConnectionPath",
    "PinnedAdapterUnavailable",
    "adapter_options",
    "async_get_bonded_device_paths",
    "bonded_device_paths_in",
    "connection_path",
    "establish_connection_via",
    "establish_pinned_connection",
    "is_automatic",
    "make_pinned_client_class",
    "normalize_source",
    "path_matches_pin",
    "pinned_candidates",
    "pinned_client_class",
    "stock_choice",
]
