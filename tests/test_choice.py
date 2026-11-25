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
    assert options[PROXY] == f"{PROXY} (not hearing this device right now)"
    # A choice that is heard is not listed twice, whatever its spelling.
    options = adapter_options([_heard(HCI0, "hci0", -60)], current=HCI0.replace(":", "").lower())
    assert len(options) == 2


def test_automatic_choices() -> None:
    assert is_automatic(None) and is_automatic("") and is_automatic(AUTOMATIC)
    assert not is_automatic(HCI0)
    assert list(adapter_options([], current=AUTOMATIC)) == [AUTOMATIC]
