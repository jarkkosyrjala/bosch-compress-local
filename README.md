# Bosch Compress Local

An in-progress Home Assistant custom integration for local communication with
Bosch Compress heat-pump systems.

The repository currently contains the project scaffold only. It does not yet
connect to or control a heat pump.

## Compatibility baseline

Initial development targets Home Assistant Core 2026.8.3 and Python 3.14.2. The
integration will follow the current Home Assistant APIs and development patterns
for that release line rather than carrying speculative compatibility code for
significantly older versions.

## Repository layout

- `custom_components/bosch_compress/`: Home Assistant integration package
- `tests/`: automated tests

Home Assistant loads a custom integration from
`<config>/custom_components/bosch_compress`.

## Development

Create the development environment with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Run the initial checks with:

```bash
uv run ruff check .
uv run pytest
```
