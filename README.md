# Bosch Compress Local

[![Validate](https://github.com/jarkkosyrjala/bosch-compress-local/actions/workflows/validate.yml/badge.svg)](https://github.com/jarkkosyrjala/bosch-compress-local/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant custom integration for direct, local communication with Bosch
Compress heat pumps through their Bosch IP module. It follows current Home
Assistant entity semantics, discovers the connected installation's capabilities,
and does not require the Bosch cloud or supplemental MQTT hardware.

> [!IMPORTANT]
> This independent project is not affiliated with, endorsed by, or supported by
> Bosch, IVT, or their affiliates. Product names and trademarks belong to their
> respective owners. This software can control heating equipment; use it at your
> own risk and verify safety-critical behavior at the appliance.

Version 0.4.0 requires **Home Assistant Core 2026.11.0 or newer**. Upgrade Core
before installing this integration version. For Core 2026.8.3–2026.10.x, keep
[version 0.3.0](https://github.com/jarkkosyrjala/bosch-compress-local/releases/tag/v0.3.0),
which remains available. Older Core versions are unsupported.

Climate and water-heater entities use Home Assistant's native temperature API.
Bosch readings and writes stay in Celsius; Home Assistant converts displayed
values and climate service inputs to the user's configured units. Entity IDs,
heating controls and the read-only DHW target are preserved.

## Hardware compatibility

Development and live testing use this installation:

- Bosch Compress 7000i AW heat pump;
- AWM 5-9 indoor unit with an RC300-family controller;
- `iCom_Low_NSC_v1` IP module running firmware `04.08.02`.

Other Bosch and IVT systems using the same encrypted local IP-module protocol may
work, but have not yet been verified. Compatibility reports are welcome in the
[issue tracker](https://github.com/jarkkosyrjala/bosch-compress-local/issues). Include
the equipment and IP-module models and firmware versions, but never include
credentials or unredacted diagnostic data.

The integration supports installations with zero or more heating circuits and zero
or more domestic-hot-water (DHW) circuits. Circuit IDs are discovered when the
config entry is set up or reloaded, instead of assuming HC1/HC2 or DHW1. Previously
discovered circuit IDs are remembered so their entities can recover after a
temporary incomplete gateway response. Reload the integration after adding or
reconfiguring physical circuits.

## Features

- AES-256-encrypted local Bosch IP-module communication.
- Bounded, recursive capability discovery instead of a fixed model-specific
  endpoint list.
- Independent climate entities for every discovered heating circuit.
- A water-heater entity for every discovered DHW circuit.
- Native sensor, binary sensor, number, select, switch, text, and date-time entities.
- Mode-aware room-temperature targets, Extra Warm Water, and DHW program controls
  when the gateway confirms that their endpoints are writable.
- Current-day compressor and auxiliary-heater energy totals from Bosch recordings.
- Config-entry diagnostics that exclude credentials, addresses, and live values.

The integration always checks the gateway's stable identity and otherwise polls
only resources used by enabled entities. Discovered nonessential or unverified
scalar resources remain available in Home Assistant but are disabled by default.

Bosch notification records are historical diagnostics. They are not treated as a
live alarm, and the integration does not infer a current alarm from notification
history alone.

## Installation

### HACS

Until Bosch Compress Local is included in the HACS default catalog, install it as a
custom repository:

1. Open HACS in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/jarkkosyrjala/bosch-compress-local` with category
   **Integration**.
4. Install **Bosch Compress Local** and restart Home Assistant.

[![Open your Home Assistant instance and add this repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jarkkosyrjala&repository=bosch-compress-local&category=integration)

### Manual installation

Copy `custom_components/bosch_compress` into the matching directory below your
Home Assistant configuration directory, then restart Home Assistant.

## Configuration and credentials

In Home Assistant, open **Settings → Devices & services → Add integration**, select
**Bosch Compress Local**, and enter:

- **Bosch IP module address:** the module's local hostname or IP address.
- **Access token:** the gateway access key printed on the IP-module label, commonly
  shown as `Password` or `Passwort` and formatted as four hyphen-separated groups.
  This is not the label's numeric `Loginname` value.
- **Password:** the password chosen when the IP module was paired or configured in
  the Bosch/IVT application.

The label and application wording varies between gateway generations. If another
local Bosch client already works, use its access-key and password pair. Do not use
Home Assistant credentials. The connection address can later be validated and
changed through the integration's options flow.

## Control behavior

- Heating-circuit mode uses the Bosch `auto` and `manual` controls and presents
  them as Home Assistant `auto` and `heat` respectively.
- A circuit advertises target-temperature control only when the IP module exposes a
  verified writable room-setpoint endpoint for its current operating mode.
- Extra Warm Water is available for a DHW circuit only when the IP module advertises
  its writable charge resource.
- The DHW program is available only when the IP module advertises a writable
  operation-mode resource.

Bosch complex schedule and holiday-assignment payloads are discoverable as disabled
diagnostic entities, but are not offered as writable controls until they can be
represented by a safe native Home Assistant type.

## Energy dashboard

The compressor and auxiliary-heater energy sensors report the current local day's
recorded consumption in kWh. They use Home Assistant's `total_increasing` state
class and reset at the local day boundary, so they can be selected directly as
individual-consumption sources in **Settings → Dashboards → Energy**.

These are daily recording totals rather than lifetime device counters. The Bosch
recording for the current day is queried again on each update, which repairs gaps
within that day. If Home Assistant remains offline across midnight, consumption
after its final poll of the previous day cannot be recovered automatically.

## Troubleshooting

### Unknown temperature readings

A reachable gateway can report a missing measurement using an EMS error marker
such as −3276.8°C. These values are exposed as unknown rather than recorded as
temperatures. Invalid numeric readings, including non-finite numbers, are handled
the same way. Valid climate modes and target controls remain available, and the
next valid poll restores the measurement automatically. A failed endpoint request
instead makes the affected entity unavailable.

This does not remove invalid readings already stored in Home Assistant history.

### Cannot connect or authenticate

- Confirm that Home Assistant can reach the IP module on the local network.
- Check that the label access key is entered as **Access token** and that **Password**
  is the user-selected application password.
- Ensure another client has not changed or invalidated the gateway password.

### Missing circuits, entities, or local controls

The Bosch IP module can temporarily expose an incomplete capability tree. On the
initial test system, Extra Warm Water, the DHW program, and both heating-circuit
target setters returned HTTP 404 until the complete heat-pump appliance was
power-cycled. After the controller setup was restored, the IP module advertised the
writable local resources.

A complete power loss can also reset room-controller assignments. In the observed
case, the indoor unit reported `A11/3081` for duplicate or unconfigured
room-temperature controllers and omitted a heating circuit until the controller
assignments were entered again. Clearing that configuration fault restored the
circuit without changing firmware or credentials.

Do not use a full appliance power cycle as a routine first troubleshooting step.
Follow the Bosch documentation and the installation's normal electrical-isolation
procedure. If a restart causes a controller-assignment fault, restore the physical
controller configuration first. Then reload Bosch Compress Local to discover the
settled capability tree.

### Reporting a problem

Enable debug logging for `custom_components.bosch_compress`, reproduce the problem,
and download the integration diagnostics from its Home Assistant device page. Check
all attachments before publishing them. The integration intentionally omits
credentials, the configured host, and live endpoint values from diagnostics.

## Security and privacy

The IP module uses Bosch's AES-256-encrypted payload format over local HTTP. The
integration communicates directly with the configured gateway and sends nothing to
the Bosch cloud or any project-operated service. Credentials are stored in Home
Assistant's private config-entry storage and are never included in diagnostics.
Protect Home Assistant backups and restrict access to the local network as usual.

## Development

Use Python **3.14** (selected by `.python-version`) to match the Home Assistant
runtime. Create the development environment and run all local checks with:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI tests Home Assistant Core **2026.11.0** (minimum) and **2026.11.1** (current
validated release), including Celsius/Fahrenheit state publication and climate
service conversion. To reproduce the second environment without changing the lockfile:

```bash
UV_PROJECT_ENVIRONMENT=.local/core-2026.11.1 uv sync --locked
uv pip install --python .local/core-2026.11.1/bin/python homeassistant==2026.11.1
.local/core-2026.11.1/bin/python -m pytest
```

CI also runs Home Assistant hassfest and HACS repository validation. The integration
uses libraries already provided by Home Assistant Core and therefore has no extra
runtime package requirements.

## License

Bosch Compress Local is released under the [MIT License](LICENSE), including its
disclaimer of warranties and limitation of liability.
