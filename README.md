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
- Config-entry diagnostics that exclude credentials, addresses, and live values.

The integration polls only core endpoints and endpoints used by enabled entities.
All discovered scalar endpoints are available in Home Assistant, while nonessential
or unverified Bosch entities are disabled by default.

## Default entities

The default Bosch set includes both heating circuits, domestic hot water, outdoor
and circuit temperatures, heat-source supply/return temperatures, compressor
modulation and derived running state, DHW setpoints, and clearly identified gateway
health/notification-history diagnostics. It also includes current-day compressor
and auxiliary-heater energy totals from the IP module's hourly recordings. These
energy sensors use kWh and Home Assistant's `total_increasing` state class, so they
can be selected directly in the Energy dashboard.

Bosch notification records are historical diagnostics. They are not treated as a
live alarm, and the integration does not infer a current alarm from notification
history alone.

## Missing local controls after installation

The Bosch IP module can temporarily expose an incomplete capability tree. On the
initial test system—an `iCom_Low_NSC_v1` running firmware `04.08.02`—Extra Warm
Water, the DHW program, and both heating-circuit target setters returned HTTP 404
until the complete heat-pump appliance was power-cycled. After the controller setup
was restored, the IP module advertised seven newly writable local HTTP resources:
Extra Warm Water activation and duration, the DHW program, and the manual and active
room-target endpoints for both HC1 and HC2.

A complete power loss can also reset room-controller assignments on this system. In
the observed case, the indoor unit reported `A11/3081` for duplicate or unconfigured
room-temperature controllers and omitted the complete HC1 endpoint tree until the
controller assignments were entered again. Clearing that configuration fault made
HC1 return without changing the IP-module firmware or credentials.

Do not use a full appliance power cycle as a routine first troubleshooting step.
Follow the Bosch documentation and the installation's normal electrical isolation
procedure, including the documented powered-off interval. If a restart causes a
controller-assignment fault, restore the physical controller configuration first.
The fixed climate and DHW controls recover automatically. If Home Assistant started
while the IP module's tree was incomplete, reload the integration once the appliance
has settled to repopulate the full optional sensor and setting catalog before
concluding that a resource is unsupported.

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
- the IP-module password.

The connection address can later be validated and changed through the integration's
options flow.

## Control behavior

- Heating-circuit mode uses the Bosch `auto` and `manual` controls and is presented
  as Home Assistant `auto` and `heat` respectively.
- A circuit advertises target-temperature control only when the IP module exposes a
  verified writable room-setpoint endpoint for its current operating mode.
- Extra Warm Water is exposed as a switch when the IP module advertises the writable
  domestic-hot-water charge resource.
- The domestic-hot-water program is exposed as a select when the IP module advertises
  its writable operation-mode resource.

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
