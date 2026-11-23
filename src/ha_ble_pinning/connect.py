"""One call that connects through the allowed paths and checks the result."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import logging
from typing import Any

from .client import PinnedAdapterUnavailable, normalize_source, pinned_client_class
from .path import ConnectionPath, connection_path, path_matches_pin

_LOGGER = logging.getLogger(__name__)

DISCONNECT_TIMEOUT = 10.0


async def establish_pinned_connection(
    device: Any,
    name: str,
    *,
    pinned_sources: Iterable[str],
    fallback_sources: Iterable[str] = (),
    local_device_paths: Iterable[str] = (),
    disconnected_callback: Any = None,
    **kwargs: Any,
) -> Any:
    """Connect to *device* only through the allowed scanners, then verify it.

    Wraps ``bleak_retry_connector.establish_connection`` with the pinned client
    (:func:`ha_ble_pinning.pinned_client_class`).  When the pinned client is
    unavailable (habluetooth changed), it falls back to Home Assistant's own
    client, and the check below still refuses a link on a disallowed path.

    *pinned_sources* and *fallback_sources* are scanner sources (adapter or
    proxy MACs); fallback sources are only used when no pinned one can take the
    connection.  *local_device_paths* (BlueZ device paths of the allowed local
    adapters, e.g. from :func:`async_get_bonded_device_paths`) make the check
    exact for local links.  Extra keyword arguments go to
    ``establish_connection``.

    Raises :class:`PinnedAdapterUnavailable` (a ``BleakError``) when no allowed
    path can take the connection, or when the link landed elsewhere — in which
    case it has already been disconnected without calling
    *disconnected_callback*.
    """
    from bleak import BleakClient  # noqa: PLC0415  (Home Assistant's wrapper at runtime)
    from bleak_retry_connector import establish_connection  # noqa: PLC0415

    pinned = frozenset(normalize_source(s) for s in pinned_sources)
    fallback = frozenset(normalize_source(s) for s in fallback_sources)
    paths = frozenset(local_device_paths)
    pinned_cls = pinned_client_class() if (pinned or fallback) else None
    pin_kwargs: dict[str, Any] = (
        {"pinned_sources": pinned, "fallback_sources": fallback} if pinned_cls else {}
    )
    client = await establish_connection(
        pinned_cls or BleakClient,
        device,
        name,
        disconnected_callback=disconnected_callback,
        **pin_kwargs,
        **kwargs,
    )
    if not (pinned or fallback):
        return client
    path = connection_path(client)
    if path_matches_pin(path, pinned | fallback, paths) is False:
        await _drop(client, path, name)
    return client


async def _drop(client: Any, path: ConnectionPath, name: str) -> None:
    """Disconnect a link on a disallowed path, quietly, and refuse it."""
    _LOGGER.warning(
        "%s: connected through %s, which is not an allowed path — disconnecting",
        name,
        path.describe(),
    )
    try:
        client.set_disconnected_callback(None)
    except Exception:  # noqa: BLE001
        pass
    try:
        await asyncio.wait_for(client.disconnect(), DISCONNECT_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass
    raise PinnedAdapterUnavailable(
        f"{name}: Home Assistant connected through {path.describe()}, "
        "not an allowed path"
    )
