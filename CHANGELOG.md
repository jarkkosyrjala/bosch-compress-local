# Changelog

All notable changes to Bosch Compress Local are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.3.0 - 2026-08-28

### Added

- Public-release metadata, MIT license, release documentation, and automated
  Ruff, pytest, hassfest, and HACS validation.
- Hardware, credential, security, Energy dashboard, and troubleshooting guidance.
- Original project-owned integration icon and logo assets.
- Persistent capability discovery for arbitrary numbers of heating and domestic
  hot-water circuits, including installations with none.
- Config-flow, reauthentication, options-flow, unload, and reload test coverage.

### Changed

- Align package and integration versions and public repository links.
- Poll optional energy recordings only when the gateway supports them and their
  enabled entities need updates.
- Use Home Assistant's public entity naming behavior, sentence-case names, and
  standard water-heater operation semantics.
- Validate host authorities, normalize IPv6 addresses, and clarify that the local
  sticker access key is the access token rather than the numeric login name.

## 0.2.0 - 2026-08-28

### Added

- Native, mode-aware room-temperature targets for heating circuits.
- Native Extra Warm Water and domestic-hot-water program controls.
- Current-day compressor and auxiliary-heater energy sensors backed by Bosch
  hourly recordings.

### Changed

- Use only the Bosch IP module for controls and data; no supplemental MQTT
  hardware or cloud connection is required.

## 0.1.0 - 2026-08-27

### Added

- Initial local Bosch IP-module integration with capability discovery, climate,
  water-heater, setting, sensor, diagnostics, and config-flow support.
