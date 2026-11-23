# ha-ble-pinning

Make a Home Assistant custom integration connect to a Bluetooth device **only through the adapters or proxies you choose**.

> **AI-written code.** Every line of code, test and documentation in this repository was written by an AI model (Anthropic's Claude), working under the direction of the maintainer, who reviewed, tested and deployed it. It has been exercised against real hardware (see [Status](#status)), but judge it as you would any third-party code before depending on it.

> **Not affiliated** with Home Assistant, the Open Home Foundation or the habluetooth project. It relies on private internals of Home Assistant's Bluetooth library; see [Caveats](#caveats).

## The problem

In Home Assistant, an integration does not choose which Bluetooth adapter or ESPHome proxy a connection goes through. Home Assistant's Bluetooth library ([habluetooth](https://github.com/Bluetooth-Devices/habluetooth)) wraps every `BleakClient`. The wrapper keeps only the **address** of the device the integration passes in, and when it connects it ranks every scanner that has heard that device by signal strength, free connection slots, connects in progress and recent failures, then uses the winner. Passing a `BLEDevice` from a particular adapter, or `adapter="hci1"`, makes no difference.

That is the right default for most devices, but it breaks two kinds of setup:

- **Devices that need a bond (pairing).** BlueZ stores a bond, the long-term key, per adapter, and only the adapter that made the bond can open an encrypted link. On a host with a second adapter, or with an ESPHome proxy nearby, Home Assistant can route the connection somewhere without the bond. The device then refuses with *insufficient authentication*. On a second local adapter, a GATT read can even hang once the device drops the unencrypted link, which leaves the integration stuck until Home Assistant restarts. Which path wins depends on signal strength and load at that moment, so the failure comes and goes.
- **Dedicated proxies.** Two devices, each with its own proxy nearby, where both proxies hear both devices. Each proxy keeps taking over the other's device as signal strengths shift ([habluetooth #602](https://github.com/Bluetooth-Devices/habluetooth/issues/602)).

Home Assistant has no per-device pin. Both cases are open upstream ([#602](https://github.com/Bluetooth-Devices/habluetooth/issues/602), [#628](https://github.com/Bluetooth-Devices/habluetooth/issues/628)), and the maintainer has said a pin would defeat the purpose of the library. So this lives outside it, for the integrations that need it.

## How it works

`pinned_client_class()` returns a subclass of Home Assistant's own client wrapper (`HaBleakClientWrapper`) that overrides **one** method: the one that picks the connection path. Instead of every scanner that heard the device, habluetooth is offered:

1. the scanners in `pinned_sources`, strongest signal first;
2. only if none of those can take the connection, the scanners in `fallback_sources`.

Everything else stays with Home Assistant: connection-slot accounting, connect tracking, connection parameters, failure counting, and disconnecting clients when a scanner goes away. If no allowed scanner can take the connection, because none has heard the device recently or none has a free slot, connecting raises `PinnedAdapterUnavailable`, a `BleakError`. It never quietly falls through to a path you didn't allow; your integration's normal retry logic tries again later.

The pin goes through `bleak_retry_connector.establish_connection`, which passes extra keyword arguments to the client class. `establish_pinned_connection()` does that for you and then **checks where the link really landed**. It reads habluetooth's record of the scanner used and bleak's BlueZ device path (`/org/bluez/hciN/dev_…`). If the link is on a path that isn't allowed, it disconnects quietly, without calling your disconnect callback, and raises `PinnedAdapterUnavailable`. That check also covers a future habluetooth version where the override no longer takes effect.

For bonded devices, `async_get_bonded_device_paths(address)` asks BlueZ (over D-Bus) which local adapters hold a bond for the device. That is the natural set to pin to.

## Usage

```python
from ha_ble_pinning import (
    PinnedAdapterUnavailable,
    async_get_bonded_device_paths,
    establish_pinned_connection,
)

bonded = await async_get_bonded_device_paths(address)  # {adapter MAC: BlueZ device path}

if bonded:
    client = await establish_pinned_connection(
        ble_device,
        address,
        pinned_sources=bonded.keys(),             # only the adapter(s) holding the bond
        local_device_paths=bonded.values(),       # makes the post-connect check exact
        disconnected_callback=self._on_disconnect,
    )
else:
    # No bond: let Home Assistant route as usual.
    client = await establish_connection(BleakClient, ble_device, address, ...)
```

Pin to a dedicated proxy by its source (the proxy's MAC as Home Assistant reports it):

```python
client = await establish_pinned_connection(ble_device, address, pinned_sources=[PROXY_MAC])
```

Or keep your own `establish_connection` call and use the client class directly:

```python
from ha_ble_pinning import pinned_client_class

cls = pinned_client_class()  # None if this Home Assistant version lacks the hooks
client = await establish_connection(
    cls or BleakClient, ble_device, address, pinned_sources={ADAPTER_MAC}
)
```

Each pinned connect logs which path it used and which it skipped, and the path Home Assistant would have picked when the pin overrode it:

```
AA:BB:CC:00:00:01: connecting through hci1 (11:11:11:11:11:11) (RSSI -60), a pinned path;
not using hci0 (22:22:22:22:22:22) (RSSI -40) (Home Assistant would have used hci0 …)
```

### `fallback_sources`

Use it for a path that has worked before but should only be the second choice, such as a proxy that holds its own bond. It is tried only when no pinned scanner can take the connection, so a louder but stale fallback can never win over a working pinned adapter.

## When not to use it

- **Devices that don't need a particular path.** Home Assistant's own routing (best signal, free slots, failover) is better.
- **Home Assistant core integrations.** Core does not accept code that relies on private internals. This is for custom integrations.
- **A broken pinned adapter.** If the pinned adapter itself has stopped working (it scans but can't connect, which happens with some USB dongles), pinning means waiting instead of failing some other way. The fix is to replug or replace the adapter, or to pair through a different one.

## Caveats

- **Private internals.** It overrides two private habluetooth methods, `_async_get_best_available_backend_and_device` and `_async_get_backend_for_ble_device`. `pinned_client_class()` checks that both exist and returns `None` when they don't, with a logged warning. `establish_pinned_connection()` then connects through Home Assistant's own client and still refuses a link on a disallowed path.
- **Weekly breakage check.** A [weekly CI job](.github/workflows/ci.yml) runs the tests against the versions pinned by the latest Home Assistant release, by Home Assistant's `dev` branch, and by the newest PyPI releases. It opens an issue in this repository when one fails. The integration tests drive the real `HaBleakClientWrapper.connect` and `establish_connection`, with stand-in scanners and backends.
- **Linux/BlueZ for bond lookups.** `async_get_bonded_device_paths()` needs BlueZ on D-Bus, as on Home Assistant OS. Pinning by source works with any scanner type.

## Status

Used by two custom integrations, [ha-onecontrol](https://github.com/phurth/ha-onecontrol) (Lippert OneControl RV gateways) and [ha-gopower](https://github.com/phurth/ha-gopower) (GoPower solar controllers). Tested on Home Assistant 2026.11 (habluetooth 7.1.x, bleak 3.x):

- a host with one local adapter and an ESPHome proxy, where Home Assistant had routed a bonded gateway through the proxy;
- a host with two local adapters and a proxy, where it now consistently uses the bonded adapter even when another is louder;
- a host with two local adapters, where Home Assistant had preferred the adapter without the bond: the pin consistently chose the bonded one. A working connection there is still awaiting confirmation, because that adapter turned out to be faulty.

Version 0.1.0. Not published on PyPI. To use it, copy `src/ha_ble_pinning/` into your integration as a subpackage. It has no Home Assistant imports and no dependencies beyond what Home Assistant already ships.

## License

MIT
