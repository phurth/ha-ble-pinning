"""Integration tests against the real habluetooth and bleak-retry-connector.

These drive Home Assistant's actual ``HaBleakClientWrapper.connect`` and
``bleak_retry_connector.establish_connection``; only the Bluetooth manager,
scanners and the bleak backend are stand-ins.  The scheduled CI job runs them
against the versions current Home Assistant ships and against the latest
releases, so a habluetooth change that breaks the pin is caught early.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest

habluetooth = pytest.importorskip("habluetooth")

import bleak_retry_connector as brc  # noqa: E402
import habluetooth.wrappers as wrappers  # noqa: E402
from bleak.backends.device import BLEDevice  # noqa: E402
from habluetooth.scanner_device import BluetoothScannerDevice  # noqa: E402

import ha_ble_pinning  # noqa: E402
from ha_ble_pinning import (  # noqa: E402
    PinnedAdapterUnavailable,
    connection_path,
    establish_pinned_connection,
)
from ha_ble_pinning.client import BACKEND_METHOD, SELECT_METHOD  # noqa: E402

DEVICE = "AA:BB:CC:00:00:01"
DEV = DEVICE.replace(":", "_")
HCI0 = "11:11:11:11:11:11"
HCI1 = "22:22:22:22:22:22"
PROXY = "33:33:33:33:33:33"


class FakeScanner:
    def __init__(self, source, name, connector=None):
        self.source, self.name, self.connector = source, name, connector
        self.adapter_idx = None
        self.connectable = True
        self._clients = set()

    def _add_connecting(self, address):
        pass

    def _finished_connecting(self, address, connected):
        pass

    def _score_connection_paths(self, rssi_diff, scanner_device):
        return scanner_device.advertisement.rssi

    def connection_failures(self, address):
        return 0

    def connections_in_progress(self):
        return 0

    def get_allocations(self):
        return None


class FakeBackend:
    """Stands in for bleak's BlueZ client and the ESPHome proxy client."""

    made: list[str] = []

    def __init__(self, device, **kwargs):
        details = device.details if isinstance(device.details, dict) else {}
        self._device_path = details.get("path")
        self._connected = False
        self.callback = kwargs.get("disconnected_callback")
        FakeBackend.made.append(self._device_path or f"proxy:{details.get('source')}")

    async def connect(self, pair=False, **kwargs):
        self._connected = True

    @property
    def is_connected(self):
        return self._connected

    async def disconnect(self):
        self._connected = False
        return True

    def set_disconnected_callback(self, callback, **kwargs):
        self.callback = callback


class FakeManager:
    shutdown = False

    def __init__(self, devices, scanners, slots=True):
        self.devices, self.scanners, self.slots = devices, scanners, slots

    def async_scanner_devices_by_address(self, address, connectable):
        return list(self.devices)

    def async_allocate_connection_slot(self, device):
        return self.slots

    def async_release_connection_slot(self, device):
        pass

    def async_scanner_by_source(self, source):
        return {s.source: s for s in self.scanners}.get(source)

    def get_bluez_mgmt_ctl(self):
        return None

    def async_current_scanners(self):
        return self.scanners

    def async_address_reachability_diagnostics(self, address, intent):
        return "diagnostics"


def _local(scanner, hci, rssi):
    device = BLEDevice(DEVICE, "dev", {"path": f"/org/bluez/{hci}/dev_{DEV}", "props": {}})
    return BluetoothScannerDevice(scanner, device, SimpleNamespace(rssi=rssi))


def _proxy(scanner, rssi):
    device = BLEDevice(DEVICE, "dev", {"source": scanner.source})
    return BluetoothScannerDevice(scanner, device, SimpleNamespace(rssi=rssi))


@pytest.fixture
def scanners():
    hci0 = FakeScanner(HCI0, f"hci0 ({HCI0})")
    hci1 = FakeScanner(HCI1, f"hci1 ({HCI1})")
    proxy = FakeScanner(
        PROXY,
        f"proxy ({PROXY})",
        connector=SimpleNamespace(client=FakeBackend, can_connect=lambda: True),
    )
    return hci0, hci1, proxy


def _connect(manager, client_class, **kwargs):
    FakeBackend.made = []

    async def none(device):
        return []

    async def no_cache(device):
        return False

    async def run():
        with mock.patch.object(wrappers, "get_manager", return_value=manager), \
             mock.patch.object(wrappers, "get_platform_client_backend_type",
                               return_value=(FakeBackend, "BlueZ")), \
             mock.patch.object(brc, "get_connected_devices", none), \
             mock.patch.object(brc, "_has_valid_services_in_cache", no_cache):
            return await brc.establish_connection(
                client_class, manager.devices[0].ble_device, DEVICE, max_attempts=2, **kwargs
            )

    return asyncio.run(run())


def test_the_private_hooks_still_exist() -> None:
    """The early warning: fails as soon as habluetooth renames what we override."""
    assert hasattr(wrappers.HaBleakClientWrapper, SELECT_METHOD)
    assert hasattr(wrappers.HaBleakClientWrapper, BACKEND_METHOD)
    assert ha_ble_pinning.pinned_client_class() is not None


def test_stock_wrapper_prefers_the_stronger_proxy(scanners) -> None:
    hci0, _, proxy = scanners
    manager = FakeManager([_local(hci0, "hci0", -70), _proxy(proxy, -50)], [hci0, proxy])

    _connect(manager, wrappers.HaBleakClientWrapper)

    assert FakeBackend.made == [f"proxy:{PROXY}"]


def test_pin_overrides_a_stronger_proxy(scanners) -> None:
    hci0, _, proxy = scanners
    manager = FakeManager([_local(hci0, "hci0", -70), _proxy(proxy, -50)], [hci0, proxy])

    client = _connect(manager, ha_ble_pinning.pinned_client_class(), pinned_sources={HCI0})

    assert FakeBackend.made == [f"/org/bluez/hci0/dev_{DEV}"]
    assert connection_path(client).source == HCI0


def test_pin_overrides_a_stronger_second_adapter(scanners) -> None:
    hci0, hci1, _ = scanners
    manager = FakeManager([_local(hci0, "hci0", -40), _local(hci1, "hci1", -60)], [hci0, hci1])

    _connect(manager, ha_ble_pinning.pinned_client_class(), pinned_sources={HCI1})

    assert FakeBackend.made == [f"/org/bluez/hci1/dev_{DEV}"]


def test_fallback_proxy_when_the_pinned_adapter_is_out_of_range(scanners) -> None:
    hci0, _, proxy = scanners
    manager = FakeManager([_proxy(proxy, -55)], [hci0, proxy])

    _connect(manager, ha_ble_pinning.pinned_client_class(),
             pinned_sources={HCI0}, fallback_sources={PROXY})

    assert FakeBackend.made == [f"proxy:{PROXY}"]


def test_refused_when_the_pinned_adapter_has_not_heard_the_device(scanners) -> None:
    hci0, _, proxy = scanners
    manager = FakeManager([_local(hci0, "hci0", -70), _proxy(proxy, -50)], [hci0, proxy])

    with pytest.raises(Exception) as caught:
        _connect(manager, ha_ble_pinning.pinned_client_class(), pinned_sources={HCI1})

    chain, exc = [], caught.value
    while exc is not None:
        chain.append(exc)
        exc = exc.__cause__
    assert any(isinstance(e, PinnedAdapterUnavailable) for e in chain)
    assert FakeBackend.made == []


def test_establish_pinned_connection_end_to_end(scanners) -> None:
    hci0, hci1, proxy = scanners
    manager = FakeManager(
        [_local(hci0, "hci0", -40), _local(hci1, "hci1", -60), _proxy(proxy, -30)],
        [hci0, hci1, proxy],
    )

    async def none(device):
        return []

    async def no_cache(device):
        return False

    async def run():
        with mock.patch.object(wrappers, "get_manager", return_value=manager), \
             mock.patch.object(wrappers, "get_platform_client_backend_type",
                               return_value=(FakeBackend, "BlueZ")), \
             mock.patch.object(brc, "get_connected_devices", none), \
             mock.patch.object(brc, "_has_valid_services_in_cache", no_cache):
            return await establish_pinned_connection(
                manager.devices[0].ble_device,
                DEVICE,
                pinned_sources=[HCI1],
                local_device_paths=[f"/org/bluez/hci1/dev_{DEV}"],
                max_attempts=2,
            )

    FakeBackend.made = []
    client = asyncio.run(run())

    assert FakeBackend.made == [f"/org/bluez/hci1/dev_{DEV}"]
    assert client.is_connected
