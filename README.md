# BrewCreator integration for Home Assistant
Monitor and control your devices in [BrewCreator](https://brewcreator.com) such as
[Fercubator Connect](https://www.brewolution.com/fermentation/fercubator-and-accessories/fercubator-connect-wifi-thermostat-controlled-fermentation)
(previously named Ferminator Connect) through Home Assistant.

![BrewCreator](assets/images/ferminator-climate-entity.png)

## Supported devices
- Fercubator Connect / Ferminator Connect
- Tilt hydrometer
- Graviator hydrometer

Several hydrometers can be registered on one BrewCreator account. Devices added to BrewCreator appear in
Home Assistant automatically, without reloading the integration, and devices removed from BrewCreator are disabled.

A Ferminator's Hydrometer select changes which Tilt or Graviator is linked to it, like Connect Device in the
BrewCreator web app.

A Ferminator's Temperature Control entity reports the linked hydrometer's temperature when it has a reading, and the
Ferminator's built-in probe otherwise. The Built-in Probe Temperature sensor always shows the probe.

## Batch info
A Ferminator's batch info (name, owner, beer style, OG, FG, EBC, IBU, volume, fermentation type and whether the
brew is started) is shown as sensors and changed with the `brewcreator.set_batch_info` action. Only the fields you
give are changed, so it works from the UI, scripts, automations and AI assistants that can call Home Assistant
actions. For example, to start a new batch:

```yaml
action: brewcreator.set_batch_info
data:
  device_id: <your Ferminator's device ID>
  batch_name: Christmas Miracle
  beer_style: Scottish Export
  original_gravity: 1.058
  final_gravity: 1.015
  started: true
```

The final gravity must be below the original gravity. Version 1.0.0 replaces the batch options dialog and the
Batch Started switch, Batch Name text and OG/FG number entities with this action and read-only sensors.

## Quiet regulation (reduce fan noise)
The Ferminator's fan runs whenever temperature regulation is on, also while it is neither cooling nor heating.
The *Ferminator quiet regulation* blueprint turns regulation off while the beer is close to its target temperature,
and back on once it drifts too far.

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fbjorncs%2Fha-brewcreator%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fbjorncs%2Fferminator_quiet_regulation.yaml)

Use *Re-import blueprint* on the blueprint to get later updates.

### Smooth the beer temperature first
The hydrometer temperature that BrewCreator reports can vary by several tenths of a degree between readings, and a
standard Tilt only reports whole degrees Fahrenheit (about 0.56 °C; a Tilt Pro reports 0.1 °F). Acting on single
readings could switch regulation on and off far too often. Instead, create a
[Statistics helper](https://my.home-assistant.io/redirect/config_flow_start/?domain=statistics) over the
hydrometer's Temperature sensor with:

- Name: *Beer Temperature*. Home Assistant adds the helper to the hydrometer's device and puts the device name in
  front, so with a Tilt Black this gives *Tilt Black Beer Temperature* (`sensor.tilt_black_beer_temperature`), next
  to the Tilt's other entities. The default name *Stats* is hard to find among your temperature sensors.
- Characteristic: *Average step* (`average_step`). It weighs each value by how long it lasted. A plain *Mean*
  counts every state change once, so a burst of flickering readings skews it and long steady periods count too little.
- Max age: 30 minutes.
- Keep last sample: on. The helper only gets a sample when the temperature changes, so without it a temperature that
  holds steady for longer than the max age leaves the helper unknown, and the blueprint waits until it changes.

Then select the helper as the blueprint's *Beer temperature*. If you leave it empty, the blueprint uses the
Temperature Control entity's current temperature as is.

### Inputs
| Input | Default | Description |
|---|---|---|
| Ferminator | | The Ferminator's Temperature Control entity. |
| Beer temperature | *(none)* | The smoothed beer temperature. Empty uses the Temperature Control entity's current temperature. |
| Off threshold | 0.5 | Turn regulation off when the beer is at most this far from the target. |
| On threshold | 1.0 | Turn regulation on when the beer is at least this far from the target. Must be larger than the off threshold. |

Temperatures and thresholds are in your Home Assistant temperature unit. The defaults are meant for °C; with °F, use
about 0.9 and 1.8 instead. If the on threshold isn't larger than the off threshold, the blueprint leaves
regulation alone and logs a warning.

### Caveats
- The blueprint only acts while the Ferminator is online, so a Ferminator you have switched off after a batch is left
  alone. While it is online, the blueprint overrides regulation you change by hand: it turns it back on if you turned
  it off, and off again if you turned it on while the beer is within the off threshold. Turn the automation off to
  change regulation by hand.
- A hydrometer that stops reporting keeps its last reading in BrewCreator until the Ferminator's next hourly upload,
  so for up to an hour the blueprint acts on a stale temperature. After that the hydrometer's sensors, and so the
  *Beer temperature* helper, become unavailable. Once the helper has been unavailable for 2 minutes, the blueprint
  turns regulation on, and the Ferminator regulates on its built-in probe.

## Installation
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bjorncs&repository=ha-brewcreator&category=integration)
- Use [HACS](https://www.hacs.xyz/docs/use/) to install the integration through the Home Assistant UI.
  Select [Custom Repositories](https://www.hacs.xyz/docs/faq/custom_repositories/) and use repository `bjorncs/ha-brewcreator` with type `Integration`.

## Known issues
### A Tilt colour selected on the Ferminator but not registered in BrewCreator
If you select a Tilt colour in the Ferminator Connect's own menu that isn't registered in BrewCreator, its readings
keep flowing, but BrewCreator logs them under the Tilt currently linked to the Ferminator. In Home Assistant they
show up on that Tilt's device, whose model still shows the linked Tilt's colour, e.g. a Black Tilt's readings on a
device with model *TiltOrange*.

To fix it, register the Tilt in BrewCreator, then link it with the Ferminator's *Hydrometer* select.

## Contributing
Feel free to make an issue or a pull request.
Please attach [diagnostics output](https://www.home-assistant.io/docs/configuration/troubleshooting/#download-diagnostics) and relevant log if you're reporting an issue (make sure to
[enable debug logging](https://www.home-assistant.io/docs/configuration/troubleshooting/#enabling-debug-logging) prior to dumping the log).

# License
This work is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for more information.