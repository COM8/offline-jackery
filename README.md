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
used for an immediate refresh.

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
