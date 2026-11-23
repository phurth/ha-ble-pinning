"""Which local adapter holds a BlueZ bond (pairing) for a device."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"


def _unwrap(value: Any) -> Any:
    """Return the payload of a dbus-fast Variant (or the value itself)."""
    return value.value if hasattr(value, "value") else value


def bonded_device_paths_in(objects: dict, address: str) -> dict[str, str]:
    """Map each adapter MAC holding a bond for *address* to its device path.

    *objects* is a BlueZ ``GetManagedObjects`` result.  BlueZ keeps one device
    object, and one bond, per adapter (``/org/bluez/hci0/dev_…``,
    ``/org/bluez/hci1/dev_…``).
    """
    target = address.upper()
    paths: dict[str, str] = {}
    for path_str, interfaces in objects.items():
        path = str(path_str)
        device = interfaces.get(DEVICE_IFACE)
        if device is None or not _unwrap(device.get("Paired")):
            continue
        device_address = _unwrap(device.get("Address"))
        if device_address is not None:
            if str(device_address).upper() != target:
                continue
        elif target.replace(":", "_") not in path.upper():
            continue
        adapter = objects.get(path.rsplit("/", 1)[0], {}).get(ADAPTER_IFACE, {})
        adapter_mac = _unwrap(adapter.get("Address"))
        if adapter_mac is not None:
            paths[str(adapter_mac).upper()] = path
    return dict(sorted(paths.items()))


async def async_get_bonded_device_paths(address: str) -> dict[str, str]:
    """Return ``{adapter MAC: BlueZ device path}`` for each adapter bonded to *address*.

    Queries BlueZ over D-Bus (``dbus-fast``, which Home Assistant ships).
    Returns an empty dict when BlueZ or D-Bus is unavailable or on any error.
    """
    try:
        from dbus_fast import BusType, Message, MessageType  # noqa: PLC0415
        from dbus_fast.aio import MessageBus  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            reply = await bus.call(
                Message(
                    destination="org.bluez",
                    path="/",
                    interface="org.freedesktop.DBus.ObjectManager",
                    member="GetManagedObjects",
                )
            )
        finally:
            bus.disconnect()
        if reply.message_type == MessageType.ERROR or not reply.body:
            return {}
        return bonded_device_paths_in(reply.body[0], address)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Could not read BlueZ bonds for %s: %s", address, exc)
        return {}
