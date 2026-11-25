"""Letting the user choose the adapter or proxy for one device.

For devices that don't need a particular path (no bond), Home Assistant's own
routing is usually right. But it balances only by signal strength and its own
slot count, and it can't know that a cheap adapter drops or refuses links well
before the slot count it assumes. A per-device choice lets the user spread
held connections across adapters and proxies by hand.

:func:`adapter_options` builds the choices for an options form;
:func:`establish_connection_via` connects through the stored choice.
``AUTOMATIC`` (the default) leaves routing to Home Assistant, unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .client import NO_RSSI, normalize_source
from .connect import establish_pinned_connection

AUTOMATIC = "automatic"
AUTOMATIC_LABEL = "Automatic (Home Assistant chooses)"


def is_automatic(choice: str | None) -> bool:
    """Return True when *choice* leaves routing to Home Assistant."""
    return not choice or choice == AUTOMATIC


def _rssi(scanner_device: Any) -> int:
    rssi = getattr(getattr(scanner_device, "advertisement", None), "rssi", None)
    return rssi if isinstance(rssi, int) else NO_RSSI


def adapter_options(
    scanner_devices: Iterable[Any],
    current: str | None = None,
    scanners: Iterable[Any] = (),
) -> dict[str, str]:
    """Return ``{value: label}`` choices for an adapter picker, Automatic first.

    *scanner_devices* is what Home Assistant's
    ``bluetooth.async_scanner_devices_by_address(hass, address, connectable=True)``
    returns: one entry per adapter or proxy that has recently heard the device,
    with ``.scanner.source``, ``.scanner.name`` and ``.advertisement.rssi``.
    They come first, strongest first, labelled with the signal they hear.

    *scanners* is every scanner Home Assistant knows
    (``bluetooth.async_current_scanners(hass)``). Pass it for devices that hold
    a connection: a connected device usually stops advertising, so after a few
    minutes no scanner "hears" it and *scanner_devices* is empty. Connectable
    scanners not already listed follow, by name. The value is the scanner
    source (adapter or proxy MAC).

    *current*, the stored choice, stays in the list even when no scanner with
    that source is known right now, so saving the form never silently drops it.
    """
    options = {AUTOMATIC: AUTOMATIC_LABEL}
    seen: set[str] = set()
    for device in sorted(scanner_devices, key=_rssi, reverse=True):
        scanner = getattr(device, "scanner", None)
        source = getattr(scanner, "source", None)
        if not isinstance(source, str) or normalize_source(source) in seen:
            continue
        seen.add(normalize_source(source))
        name = getattr(scanner, "name", None) or source
        rssi = _rssi(device)
        options[source] = f"{name}, {rssi} dBm" if rssi != NO_RSSI else name
    others = []
    for scanner in scanners:
        source = getattr(scanner, "source", None)
        if (
            not isinstance(source, str)
            or getattr(scanner, "connectable", True) is False
            or normalize_source(source) in seen
        ):
            continue
        seen.add(normalize_source(source))
        others.append((getattr(scanner, "name", None) or source, source))
    for name, source in sorted(others):
        options[source] = name
    if not is_automatic(current) and normalize_source(current) not in seen:
        options[current] = f"{current} (not available right now)"
    return options


async def establish_connection_via(
    device: Any,
    name: str,
    *,
    adapter: str | None,
    allow_fallback: bool = True,
    scanner_devices: Iterable[Any] = (),
    disconnected_callback: Any = None,
    **kwargs: Any,
) -> Any:
    """Connect through the user's chosen *adapter* (a scanner source).

    With *adapter* empty or ``AUTOMATIC``, this is a plain
    ``bleak_retry_connector.establish_connection`` with Home Assistant's
    client, which routes as usual.

    Otherwise the connection is pinned to *adapter*.  With *allow_fallback*,
    the other scanners in *scanner_devices* (pass
    ``async_scanner_devices_by_address(hass, address, connectable=True)``) are
    the fallback tier: used only when the chosen adapter has not heard the
    device recently or has no free slot.  A chosen adapter that accepts the
    connection and then fails is not a reason to fall back; the caller's
    normal retry applies.  Without a usable path this raises
    :class:`ha_ble_pinning.PinnedAdapterUnavailable` (a ``BleakError``).

    Extra keyword arguments go to ``establish_connection``.
    """
    if is_automatic(adapter):
        from bleak import BleakClient  # noqa: PLC0415  (Home Assistant's wrapper at runtime)
        from bleak_retry_connector import establish_connection  # noqa: PLC0415

        return await establish_connection(
            BleakClient, device, name, disconnected_callback=disconnected_callback, **kwargs
        )

    chosen = normalize_source(adapter)
    fallback: list[str] = []
    if allow_fallback:
        for scanner_device in scanner_devices:
            source = getattr(getattr(scanner_device, "scanner", None), "source", None)
            if isinstance(source, str) and normalize_source(source) != chosen:
                fallback.append(source)
    return await establish_pinned_connection(
        device,
        name,
        pinned_sources=[adapter],
        fallback_sources=fallback,
        disconnected_callback=disconnected_callback,
        **kwargs,
    )
