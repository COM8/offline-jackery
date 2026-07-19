# Offline Jackery

Offline Jackery is a Home Assistant custom integration for controlling supported
Jackery devices directly over Bluetooth Low Energy. Jackery cloud access is used
only during setup to obtain the device-specific Bluetooth key; normal operation
is local.

The initial supported device is the **Jackery SolarVault 3 Pro** (`HOME_011`,
model code `3001`). This project is based on reverse engineering and is not
affiliated with or supported by Jackery.

> [!WARNING]
> This integration is experimental. Commands affecting EPS output or grid export
> can affect attached equipment and regulatory compliance. Test cautiously.

## Installation with HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/COM8/offline-jackery` as an **Integration** repository.
3. Download **Offline Jackery** and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and select
   **Offline Jackery**.

## Local Shelly Pro 3EM bridge

You can add any number of Shelly Pro 3EM bridges from the same integration:

1. Add **Offline Jackery** again and choose **Shelly Pro 3EM local P1 bridge**.
2. Enter the Shelly's local address and the Home Assistant host's LAN IPv4
   address. The latter must be reachable by the SolarVault.
3. Keep the generated virtual meter serial, and choose a different HTTP port
   for each additional bridge (for example 21001, 21002, ...).
4. Open the SolarVault device in Home Assistant and use its **Smart-meter
   source** selector. Choose any loaded local bridge; the integration binds it
   over Bluetooth and enables smart-meter following automatically.

Choose **Current meter configuration (for example online Shelly)** to remove
the local bridge selected through this integration and resume following the
SolarVault's remaining meter configuration. Other meter bindings are not
removed. The `offline_jackery.bind_shelly_bridge` action remains available for
automations and performs the same selection.

Each entry polls only the Shelly's local Gen2 RPC API, serves HomeWizard API v1
over HTTP, and advertises `_hwenergy._tcp.local.` with mDNS. No Shelly or
Jackery cloud is involved after initial SolarVault setup. If the Shelly uses
authentication, its password is stored in Home Assistant's config-entry
storage. Keep Home Assistant, the Shelly, and SolarVault on the same trusted
LAN/VLAN; this emulated HomeWizard endpoint is intentionally unauthenticated.

## Setup

The configuration wizard asks for the Jackery account login mode, account,
password, and (for email accounts) two-letter region code. The password and
session token are used only in memory and are not stored. After login:

1. Select a SolarVault system from the account.
2. Let Home Assistant scan for its Bluetooth advertisement.
3. Select the matching serial-number result.
4. The wizard validates the key by connecting and reading initial telemetry.

The Bluetooth key and selected Bluetooth address are stored in Home Assistant's
config-entry storage. Protect Home Assistant backups and its `.storage`
directory, because Home Assistant does not provide a general encrypted secret
store for config-entry values.

## Operation

Home Assistant refreshes the device every five seconds while it is reachable.
If Bluetooth disconnects, the integration reconnects using exponential backoff,
capped at 64 seconds. The standard Home Assistant entity update action can be
used for an immediate refresh, and the **Refresh status** button bypasses
reconnect backoff.

Every scalar property returned in the main device and combined-system BLE
snapshots is exposed as a read-only sensor or binary sensor. Nested properties
and array members are preserved as individual entities. Known power,
temperature, and state-of-charge fields receive native Home Assistant units and
device classes. Each property also includes its protocol field and a concise
description in its attributes.

The currently verified writable entities are:

- **Off-grid / EPS output** switch
- **Smart-meter power following** switch
- **Maximum grid feed-in power** number (0 to the reported device maximum, in
  10 W increments)

The grid feed-in setting is a ceiling, not an instantaneous power target. Actual
export still depends on PV production, battery state, local load, metering, and
firmware safety rules.

Enable debug logging temporarily with:

```yaml
logger:
  logs:
    custom_components.offline_jackery: debug
```

## Development

Open this repository in the supplied dev container, then run:

```bash
scripts/setup
scripts/develop
```

Lint and test with:

```bash
scripts/lint
pytest
```

Protocol behavior was recovered from the Jackery Android app and is documented
in the parent research repository used to develop this integration.

## License

MIT
