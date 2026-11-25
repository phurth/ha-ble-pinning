"""Adapter picker options."""

from __future__ import annotations

from types import SimpleNamespace

from ha_ble_pinning import AUTOMATIC, AUTOMATIC_LABEL, adapter_options, is_automatic


def _heard(source, name, rssi):
    return SimpleNamespace(scanner=SimpleNamespace(source=source, name=name),
                           advertisement=SimpleNamespace(rssi=rssi))


HCI0 = "11:11:11:11:11:11"
PROXY = "33:33:33:33:33:33"


def test_automatic_first_then_strongest_first() -> None:
    options = adapter_options([
        _heard(HCI0, f"hci0 ({HCI0})", -70),
        _heard(PROXY, f"proxy ({PROXY})", -50),
    ])
    assert list(options) == [AUTOMATIC, PROXY, HCI0]
    assert options[AUTOMATIC] == AUTOMATIC_LABEL
    assert options[PROXY] == f"proxy ({PROXY}), -50 dBm"


def test_duplicates_and_missing_rssi() -> None:
    options = adapter_options([
        _heard(HCI0, "hci0", None),
        _heard(HCI0.lower(), "hci0 again", -40),
    ])
    assert list(options) == [AUTOMATIC, HCI0.lower()]
    assert options[HCI0.lower()] == "hci0 again, -40 dBm"


def test_stored_choice_is_kept_when_not_heard() -> None:
    options = adapter_options([_heard(HCI0, "hci0", -60)], current=PROXY)
    assert options[PROXY] == f"{PROXY} (not available right now)"
    # A choice that is heard is not listed twice, whatever its spelling.
    options = adapter_options([_heard(HCI0, "hci0", -60)], current=HCI0.replace(":", "").lower())
    assert len(options) == 2


def test_automatic_choices() -> None:
    assert is_automatic(None) and is_automatic("") and is_automatic(AUTOMATIC)
    assert not is_automatic(HCI0)
    assert list(adapter_options([], current=AUTOMATIC)) == [AUTOMATIC]


def _scanner(source, name, connectable=True):
    return SimpleNamespace(source=source, name=name, connectable=connectable)


def test_known_scanners_are_listed_when_the_device_is_silent() -> None:
    """A connected device stops advertising; the picker must still list the paths."""
    options = adapter_options(
        [],
        scanners=[_scanner(PROXY, "proxy"), _scanner(HCI0, "hci0"), _scanner("44:44:44:44:44:44", "passive", False)],
    )
    assert list(options) == [AUTOMATIC, HCI0, PROXY]
    assert options[HCI0] == "hci0"


def test_heard_scanners_first_and_not_repeated() -> None:
    options = adapter_options(
        [_heard(PROXY, "proxy", -55)],
        scanners=[_scanner(HCI0, "hci0"), _scanner(PROXY, "proxy")],
    )
    assert list(options) == [AUTOMATIC, PROXY, HCI0]
    assert options[PROXY] == "proxy, -55 dBm"
