# Bosch Compress Local

A Home Assistant custom integration for direct, local communication with Bosch
Compress heat pumps through the Bosch IP module. It is designed around current
Home Assistant entity semantics and preserves multiple heating circuits as
independent climate entities.

Initial compatibility is Home Assistant Core 2026.8.3. Older releases are not a
compatibility target unless a concrete need is identified.

## What it provides

- Encrypted, local Bosch IP-module communication with no cloud dependency.
- Recursive capability discovery instead of a fixed model-specific endpoint list.
- Separate climate entities for heating circuits 1 and 2.
- A domestic-hot-water entity with measured and active target temperatures.
- Native sensor, binary sensor, number, select, switch, text, and date-time entities.
- Optional Husdata H60 MQTT supplementation for values and controls that this IP
  module does not expose.
- Config-entry diagnostics that exclude credentials, addresses, and live values.

The integration polls only core endpoints and endpoints used by enabled entities.
All discovered scalar endpoints are available in Home Assistant, while nonessential
or unverified Bosch entities are disabled by default.

## Default entities

The default Bosch set includes both heating circuits, domestic hot water, outdoor
and circuit temperatures, heat-source supply/return temperatures, compressor
modulation and derived running state, DHW setpoints, and clearly identified gateway
health/notification-history diagnostics.

When an H60 MQTT topic prefix is configured, currently used values that have no
confirmed Bosch equivalent are also enabled. This includes Extra Warm Water, the
DHW program, active alarm and current alarm code, specialist temperatures, pump and
valve states, and legacy energy/runtime telemetry. Confirmed duplicate H60 readings
are intentionally replaced by their Bosch source. The unverified HC1 automatic and
HC2 raw target-number entities remain available but disabled by default; the two
climate entities route target writes to the appropriate registers.

Bosch notification records are historical diagnostics. They are not treated as a
live alarm. If H60 is configured, its live alarm register supplies the active-alarm
binary sensor.

## Installation with HACS

Until the repository is published in the HACS default catalog, add its GitHub URL as
a custom repository with category **Integration**, then install **Bosch Compress
Local**. Restart Home Assistant afterward.

The integration can also be installed manually by copying
`custom_components/bosch_compress` into the matching directory below the Home
Assistant configuration directory and restarting Home Assistant.

## Configuration

In Home Assistant, open **Settings → Devices & services → Add integration** and
select **Bosch Compress Local**. Enter:

- the Bosch IP-module hostname or IP address;
- the IP-module login name;
- the IP-module password; and
- optionally, the H60 MQTT topic prefix.

The optional H60 support requires Home Assistant's MQTT integration to be configured
and connected. Connection settings can later be validated and changed through the
integration's options flow.

## Control behavior

- Heating-circuit mode uses the Bosch `auto` and `manual` controls and is presented
  as Home Assistant `auto` and `heat` respectively.
- HC1 target writes use H60 register `0203` in manual mode and `020C` in automatic
  mode.
- HC2 target writes use H60 register `0220`; live write validation is intentionally
  deferred until it can be performed with an explicit rollback value.
- Extra Warm Water is a switch using the verified H60 values `0` and `1`.
- DHW program is a select: `0` Off, `1` Eco / Normal, `2` Comfort, `4` Manual time
  program, and `5` Eco+. Value `3` is rejected as undefined.

Bosch complex schedule and holiday-assignment payloads are discoverable as disabled
diagnostic entities, but are not offered as writable controls until they can be
represented by a safe native Home Assistant type.

## Development

Create the development environment and run the checks with:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
