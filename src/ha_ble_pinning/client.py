"""A Home Assistant BleakClient that only connects through allowed scanners.

Home Assistant's Bluetooth stack (habluetooth) chooses the connection path for
every ``BleakClient`` itself: its client wrapper keeps only the *address* of
the ``BLEDevice`` it is given and scores every scanner that has heard the
device (signal strength, free connection slots, connects in progress, recent
failures).  Nothing an integration passes in selects the adapter or proxy.

:func:`pinned_client_class` returns a subclass of that wrapper which narrows
only the path choice: habluetooth is offered just the scanners in
``pinned_sources`` (strongest signal first) and, only when none of those can
take the connection, the ones in ``fallback_sources``.  Slot accounting,
connect tracking, connection parameters and failure counting all stay with
habluetooth.  When no allowed scanner can take the connection, connecting
raises :class:`PinnedAdapterUnavailable` instead of using any other path.
"""

from __future__ import annotations

import logging
from typing import Any

from bleak.exc import BleakError

_LOGGER = logging.getLogger(__name__)

NO_RSSI = -127

# The two habluetooth methods the pinned client relies on.  Both are private,
# so their presence is checked before the pinned client is offered at all.
SELECT_METHOD = "_async_get_best_available_backend_and_device"
BACKEND_METHOD = "_async_get_backend_for_ble_device"


class PinnedAdapterUnavailable(BleakError):
    """No allowed scanner can take this connection right now."""


def normalize_source(source: Any) -> str:
    """Return a scanner source (adapter MAC or proxy MAC) in comparable form."""
    return str(source).upper().replace(":", "")


def pinned_candidates(candidates: list[Any], allowed: frozenset[str]) -> list[Any]:
    """Return the scanner candidates whose source is in *allowed*, strongest first.

    *candidates* are habluetooth ``BluetoothScannerDevice`` objects (anything
    with ``.scanner.source`` and ``.advertisement.rssi``); *allowed* holds
    normalized sources (see :func:`normalize_source`).
    """
    matching = [c for c in candidates if normalize_source(c.scanner.source) in allowed]
    return sorted(
        matching,
        key=lambda c: getattr(c.advertisement, "rssi", None) or NO_RSSI,
        reverse=True,
    )


def describe_candidate(candidate: Any) -> str:
    """Return a short description of a scanner candidate for logs."""
    scanner = candidate.scanner
    name = getattr(scanner, "name", None) or scanner.source
    return f"{name} (RSSI {getattr(candidate.advertisement, 'rssi', None)})"


def stock_choice(candidates: list[Any]) -> Any | None:
    """Return the candidate Home Assistant's own ranking would try first.

    Mirrors habluetooth's ordering (signal strength, then its connection-path
    score, which penalises busy adapters, recent failures and full slots).
    Only used to log whether the pin changed the outcome; returns None if the
    candidates do not offer that scoring.
    """
    try:
        ranked = sorted(
            candidates,
            key=lambda c: getattr(c.advertisement, "rssi", None) or NO_RSSI,
            reverse=True,
        )
        if len(ranked) > 1:
            rssi_diff = (ranked[0].advertisement.rssi or NO_RSSI) - (
                ranked[1].advertisement.rssi or NO_RSSI
            )
            ranked = sorted(
                ranked, key=lambda c: c.score_connection_path(rssi_diff), reverse=True
            )
        return ranked[0] if ranked else None
    except Exception:  # noqa: BLE001 - diagnostics only
        return None


def make_pinned_client_class(base: type) -> type:
    """Build the pinned client as a subclass of Home Assistant's wrapper *base*.

    Exposed so the behaviour can be tested against stand-in base classes; use
    :func:`pinned_client_class` in an integration.
    """

    class PinnedHaBleakClient(base):  # type: ignore[misc, valid-type]
        """Home Assistant's BleakClient wrapper, restricted to allowed scanners."""

        def __init__(
            self,
            address_or_ble_device: Any,
            *args: Any,
            pinned_sources: Any = (),
            fallback_sources: Any = (),
            **kwargs: Any,
        ) -> None:
            self._pin_sources = frozenset(normalize_source(s) for s in pinned_sources)
            self._pin_fallback = (
                frozenset(normalize_source(s) for s in fallback_sources)
                - self._pin_sources
            )
            self._pin_address = str(
                getattr(address_or_ble_device, "address", address_or_ble_device)
            )
            super().__init__(address_or_ble_device, *args, **kwargs)

        def _async_get_best_available_backend_and_device(
            self, *args: Any, **kwargs: Any
        ) -> Any:
            if not (self._pin_sources or self._pin_fallback):
                return super()._async_get_best_available_backend_and_device(
                    *args, **kwargs
                )
            manager = args[0] if args else kwargs.get("manager")
            if manager is None:  # a signature without the manager argument
                from habluetooth import get_manager  # noqa: PLC0415

                manager = get_manager()
            address = self._pin_address
            candidates = list(manager.async_scanner_devices_by_address(address, True))
            stock = stock_choice(candidates)
            heard = False
            for tier, role in (
                (self._pin_sources, "a pinned path"),
                (self._pin_fallback, "a fallback path"),
            ):
                usable = pinned_candidates(candidates, tier)
                heard = heard or bool(usable)
                for candidate in usable:
                    backend = getattr(self, BACKEND_METHOD)(
                        manager, candidate.scanner, candidate.ble_device
                    )
                    if backend is None:  # e.g. no free connection slot
                        continue
                    others = [c for c in candidates if c is not candidate]
                    _LOGGER.info(
                        "%s: connecting through %s, %s%s%s",
                        address,
                        describe_candidate(candidate),
                        role,
                        (
                            "; not using "
                            + ", ".join(describe_candidate(c) for c in others)
                            if others
                            else ""
                        ),
                        (
                            " (Home Assistant would have used "
                            f"{describe_candidate(stock)})"
                            if stock is not None and stock is not candidate
                            else ""
                        ),
                    )
                    return backend
            allowed = ", ".join(sorted(self._pin_sources | self._pin_fallback))
            if heard:
                msg = f"{address}: no free connection slot on the allowed path(s) ({allowed})"
            else:
                msg = (
                    f"{address}: the allowed path(s) ({allowed}) have not heard "
                    "the device recently"
                )
            raise PinnedAdapterUnavailable(msg)

    return PinnedHaBleakClient


_CLIENT_CLASS_CACHE: dict[str, type | None] = {}


def pinned_client_class() -> type | None:
    """Return the pinned client class, or None if pinning is not possible here.

    None means Home Assistant's Bluetooth wrapper is not importable, or no
    longer has the methods the pin relies on.  Callers should then connect the
    normal way and check the path afterwards (see
    :func:`ha_ble_pinning.connection_path`).
    """
    if "cls" not in _CLIENT_CLASS_CACHE:
        cls: type | None = None
        try:
            from habluetooth.wrappers import HaBleakClientWrapper  # noqa: PLC0415
        except ImportError:
            _LOGGER.debug("habluetooth is not available; pinning is disabled")
        else:
            if hasattr(HaBleakClientWrapper, SELECT_METHOD) and hasattr(
                HaBleakClientWrapper, BACKEND_METHOD
            ):
                cls = make_pinned_client_class(HaBleakClientWrapper)
            else:
                _LOGGER.warning(
                    "This Home Assistant version's Bluetooth client no longer has "
                    "the methods ha-ble-pinning relies on; connections will go "
                    "wherever Home Assistant routes them"
                )
        _CLIENT_CLASS_CACHE["cls"] = cls
    return _CLIENT_CLASS_CACHE["cls"]
