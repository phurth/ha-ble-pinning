"""Unit tests against a stand-in for habluetooth's client wrapper."""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest
from bleak.exc import BleakError

import ha_ble_pinning
from ha_ble_pinning import (
    ConnectionPath,
    PinnedAdapterUnavailable,
    bonded_device_paths_in,
    client as client_module,
    connection_path,
    make_pinned_client_class,
    path_matches_pin,
    pinned_candidates,
    stock_choice,
)

DEVICE = "AA:BB:CC:00:00:01"
BONDED = "11:11:11:11:11:11"
OTHER = "22:22:22:22:22:22"
PROXY = "33:33:33:33:33:33"


def _candidate(source: str, rssi: int | None, score: float | None = None):
    candidate = SimpleNamespace(
        scanner=SimpleNamespace(source=source, name=source),
        ble_device=SimpleNamespace(address=DEVICE),
        advertisement=SimpleNamespace(rssi=rssi),
    )
    if score is not None:
        candidate.score_connection_path = lambda rssi_diff: score
    return candidate


class FakeWrapper:
    """Has the same two hooks as habluetooth's HaBleakClientWrapper."""

    def __init__(self, address_or_ble_device, *args, **kwargs):
        self.init_kwargs = kwargs
        self.no_slot: set[str] = set()

    def _async_get_best_available_backend_and_device(self, manager):
        return "stock choice"

    def _async_get_backend_for_ble_device(self, manager, scanner, ble_device):
        return None if scanner.source in self.no_slot else f"via {scanner.source}"


class Manager:
    def __init__(self, *candidates):
        self.candidates = list(candidates)

    def async_scanner_devices_by_address(self, address, connectable):
        assert address == DEVICE and connectable is True
        return self.candidates


Pinned = make_pinned_client_class(FakeWrapper)


def test_candidates_are_filtered_and_sorted() -> None:
    weak, strong = _candidate(BONDED, -70), _candidate(OTHER, -40)
    allowed = frozenset({"111111111111", "222222222222"})

    assert pinned_candidates([_candidate(PROXY, -30), weak, strong], allowed) == [strong, weak]


def test_without_a_pin_home_assistant_decides() -> None:
    assert Pinned(DEVICE)._async_get_best_available_backend_and_device(Manager()) == "stock choice"


def test_pin_beats_stronger_paths() -> None:
    manager = Manager(_candidate(PROXY, -40), _candidate(OTHER, -45), _candidate(BONDED, -75))

    client = Pinned(DEVICE, pinned_sources={BONDED})

    assert client._async_get_best_available_backend_and_device(manager) == f"via {BONDED}"


def test_manager_by_keyword() -> None:
    client = Pinned(DEVICE, pinned_sources={BONDED})

    assert client._async_get_best_available_backend_and_device(
        manager=Manager(_candidate(BONDED, -60))
    ) == f"via {BONDED}"


def test_next_pinned_path_when_the_first_has_no_slot() -> None:
    manager = Manager(_candidate(BONDED, -40), _candidate(OTHER, -70))
    client = Pinned(DEVICE, pinned_sources={BONDED, OTHER})
    client.no_slot = {BONDED}

    assert client._async_get_best_available_backend_and_device(manager) == f"via {OTHER}"


def test_fallback_only_when_no_pinned_path_can_take_it() -> None:
    client = Pinned(DEVICE, pinned_sources={BONDED}, fallback_sources={PROXY})

    assert client._async_get_best_available_backend_and_device(
        Manager(_candidate(PROXY, -40), _candidate(OTHER, -45))
    ) == f"via {PROXY}"
    assert client._async_get_best_available_backend_and_device(
        Manager(_candidate(PROXY, -40), _candidate(BONDED, -80))
    ) == f"via {BONDED}", "a louder fallback must not win over a pinned path"


def test_refused_when_no_allowed_path_heard_it() -> None:
    client = Pinned(DEVICE, pinned_sources={BONDED})

    with pytest.raises(PinnedAdapterUnavailable, match="not heard"):
        client._async_get_best_available_backend_and_device(Manager(_candidate(OTHER, -40)))


def test_refused_when_allowed_paths_have_no_slot() -> None:
    client = Pinned(DEVICE, pinned_sources={BONDED})
    client.no_slot = {BONDED}

    with pytest.raises(PinnedAdapterUnavailable, match="no free connection slot"):
        client._async_get_best_available_backend_and_device(Manager(_candidate(BONDED, -40)))


def test_refusal_is_a_bleak_error() -> None:
    assert issubclass(PinnedAdapterUnavailable, BleakError)


def test_pin_arguments_are_consumed() -> None:
    client = Pinned(DEVICE, pinned_sources={BONDED}, _is_retry_client=True)

    assert client.init_kwargs == {"_is_retry_client": True}


def test_stock_choice_follows_the_score() -> None:
    busy_local, proxy = _candidate(BONDED, -43, score=-66.2), _candidate(PROXY, -66, score=-66.0)

    assert stock_choice([busy_local, proxy]) is proxy
    assert stock_choice([_candidate(BONDED, -40), _candidate(PROXY, -60)]) is None


def test_log_names_what_home_assistant_would_have_used(caplog) -> None:
    caplog.set_level("INFO")
    manager = Manager(_candidate(BONDED, -43, score=-66.2), _candidate(PROXY, -66, score=-66.0))

    Pinned(DEVICE, pinned_sources={BONDED})._async_get_best_available_backend_and_device(manager)

    assert f"Home Assistant would have used {PROXY}" in caplog.text


# ── Availability of the hooks ─────────────────────────────────────────


def _fake_habluetooth(monkeypatch, wrapper: type) -> None:
    package = types.ModuleType("habluetooth")
    wrappers = types.ModuleType("habluetooth.wrappers")
    wrappers.HaBleakClientWrapper = wrapper
    package.wrappers = wrappers
    monkeypatch.setitem(sys.modules, "habluetooth", package)
    monkeypatch.setitem(sys.modules, "habluetooth.wrappers", wrappers)
    monkeypatch.setattr(client_module, "_CLIENT_CLASS_CACHE", {})


def test_client_class_subclasses_the_wrapper(monkeypatch) -> None:
    _fake_habluetooth(monkeypatch, FakeWrapper)

    assert issubclass(ha_ble_pinning.pinned_client_class(), FakeWrapper)


def test_no_client_class_when_the_hooks_are_gone(monkeypatch) -> None:
    _fake_habluetooth(monkeypatch, type("Wrapper", (), {}))

    assert ha_ble_pinning.pinned_client_class() is None


# ── Paths ─────────────────────────────────────────────────────────────

BONDED_PATH = "/org/bluez/hci1/dev_AA_BB_CC_00_00_01"
OTHER_PATH = "/org/bluez/hci0/dev_AA_BB_CC_00_00_01"
ALLOWED = frozenset({"111111111111"})


def _client(source, device_path):
    return SimpleNamespace(
        _connected_scanner=SimpleNamespace(source=source) if source else None,
        _backend=SimpleNamespace(_device_path=device_path),
    )


def test_paths() -> None:
    assert path_matches_pin(connection_path(_client(BONDED, BONDED_PATH)), ALLOWED, frozenset({BONDED_PATH}))
    assert path_matches_pin(connection_path(_client(OTHER, OTHER_PATH)), ALLOWED, frozenset({BONDED_PATH})) is False
    assert path_matches_pin(connection_path(_client(PROXY, None)), ALLOWED) is False
    assert path_matches_pin(connection_path(_client(BONDED, None)), ALLOWED) is True
    assert connection_path(SimpleNamespace()) == ConnectionPath(None, None)
    assert path_matches_pin(ConnectionPath(None, None), ALLOWED) is None


def test_bonded_device_paths() -> None:
    v = lambda value: SimpleNamespace(value=value)  # noqa: E731 - dbus Variant
    objects = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": v(OTHER)}},
        "/org/bluez/hci1": {"org.bluez.Adapter1": {"Address": v(BONDED)}},
        OTHER_PATH: {"org.bluez.Device1": {"Address": v(DEVICE), "Paired": v(False)}},
        BONDED_PATH: {"org.bluez.Device1": {"Address": v(DEVICE), "Paired": v(True)}},
    }

    assert bonded_device_paths_in(objects, DEVICE) == {BONDED: BONDED_PATH}


def test_no_bluez_means_no_bonds() -> None:
    assert isinstance(asyncio.run(ha_ble_pinning.async_get_bonded_device_paths(DEVICE)), dict)
