# BrewCreator API: observed behaviour

Observed behaviour of the BrewCreator cloud API (`api.brewcreator.com`) and its web app.

## Equipment JSON

| Field | Meaning |
|---|---|
| `isActive` (Ferminator) | Online. Changes as soon as BrewCreator notices a connect or disconnect, but also drops for up to ~35 s at every cloud reconnect (see Connectivity). |
| `isActive` (Tilt) | Always `false`, even while reporting. |
| `deviceTwinState.connectionState` | Online too, but lags `isActive` by seconds to minutes in both directions. `deviceTwinState` is null for equipment that has never been online. |
| `isLoggingData` | "Batch started": BrewCreator logs the brew. Unrelated to temperature regulation. |
| `lastActivityTime` (Ferminator) | Real UTC. Updated by every message from the Ferminator, including probe readings, and by a PUT to it. |
| `lastActivityTime` (hydrometer) | Time of the newest brew log point, not of the last reading; null if no brew was ever logged on it. Danish local time labelled `+00:00` (CET/CEST). |
| `actualTemperature` (Ferminator) | The built-in probe, never the hydrometer. |
| `sg`, `abv` (Ferminator) | A stale copy of a hydrometer reading; stayed 1.001 while the linked Tilt read 1.018. Don't use. |
| `isRegulatingTemperature`, `hProcess` | Change straight after a PUT that turns regulation on or off. |
| `lProcess` | Follows ~10 s later, once the Ferminator reports back. Keeps its last value while the Ferminator is offline, e.g. "Cooling". |
| `activityTimeSession` | Always 180. Not used by the web app. |
| Device twin `color`, `colorID` | Stay "None"/"0"; they don't reflect the selected Tilt. |

## Hydrometer readings

- A Tilt only reports through a Ferminator. A Graviator reports through a Ferminator (Bluetooth) or, when unlinked,
  directly over Wi-Fi, and still logs then.
- The Ferminator only sends a Tilt's temperature (`tgrav`) when it changes, so a stored temperature can be old but
  still current. After the Ferminator boots, the Tilt's `actualTemperature` is null until the next `tgrav`.
- After switching the linked Tilt, the new one shows its old stored values until its first reading (5–10 s).
  Readings sent just before the switch (`selectgrav`) are saved to the previously linked Tilt.
- A Tilt colour selected on the Ferminator but not registered in BrewCreator keeps reporting; its readings are saved
  to the linked Tilt.
- Unlinked Tilts keep their last values indefinitely, possibly garbage such as SG 0.
- **Silent linked Tilt** (e.g. switched off):
  - The Ferminator switches its display and regulation to the built-in probe at once, and sends no further readings.
  - BrewCreator keeps the Tilt's last `actualTemperature` until the next hourly telemetry batch, which carries null
    readings. Then the Tilt's `actualTemperature` and `sg` become null.

## Linking a hydrometer

- Stored on both sides: the Ferminator's `connectedEquipments` and the hydrometer's `equipmentId`.
- A partial `PUT /api/v1.0/equipments/<ferminator id>` with `{"connectedEquipments": ["<id>"]}` (or `[]`) moves the
  link on both sides and leaves the Ferminator's other fields alone.
- The server copies the Ferminator's batch info (at least `brewName`, `brewDate`) to the newly linked hydrometer,
  but not `isLoggingData`. Previously linked hydrometers keep the copied batch info and `isLoggingData`.
- Changing the Tilt in the Ferminator's own menu updates BrewCreator within ~3 s.
- Stopping the brew clears `brewDate` only on the Ferminator and the currently linked hydrometer.
- Linking a hydrometer that is linked elsewhere fails with "Equipment Has Already Been Connected" (web app text).

## Adding and removing equipment

The API publishes its spec at `https://api.brewcreator.com/swagger/v1/swagger.json`.

**Adding** takes two calls, as the web app's Add Device dialog makes them:

1. `POST /api/v1.0/equipments` with `{"iotHubId": "<serial>"}` (`Equipments_RegisterEquipment`). The server works
   out the type and returns the new equipment as a single object in `data`, although the spec says a collection.
   - Ferminator and Graviator: the serial printed with the QR code, entered in the web app with the mask
     `AAAA-AAA-AAA`.
   - Tilt: the literal `"Tilt"`, case-insensitive (`"tilt"` worked too). Every call creates a new Tilt, with a random
     6-character `iotHubBrewEquipmentId`, `color: "None"`, `name: null`, `og: 1` and `fg: 0.9`.
   - The web app also sends `name`, `owner`, `color` and an empty `iotHubBrewEquipmentGroupId`; the server ignores
     them.
2. `PUT /api/v1.0/equipments/<id>` with `name` and `owner`, and for a Graviator or Tilt a `color`. The web app
   requires all of them (name and owner up to 128 characters, owner defaulting to the user's full name) and only
   offers colours that no other equipment of the same type has.

The server checks none of that: a `color` already used by another Tilt (`TiltBlack`) and a Graviator colour on a
Tilt (`Teal`) were both stored with 200. Colours by type: Graviator `Red`, `Blue`, `Green`, `Pink`, `Yellow`,
`Teal`, `White`; Tilt `TiltRed`, `TiltGreen`, `TiltBlack`, `TiltPurple`, `TiltOrange`, `TiltBlue`, `TiltYellow`,
`TiltPink`.

Cancelling the dialog after step 1 deletes the new equipment again.

**Removing** is `DELETE /api/v1.0/equipments/<id>` (`Equipments_UnregisterEquipment`), after a confirmation dialog.
It returned 200 with `data: true`, and the equipment was gone from the list at once.

| Case | Response |
|---|---|
| Unknown serial (`ZZZZ-ZZZ-ZZZ`, `hello world`), or a Tilt's random ID | 400, `errors: ["equipmentDoesNotExistOnIotHub"]` |
| Empty `iotHubId` | 500, message "The parameter deviceId cannot be null, empty or whitespace." |
| `DELETE` of equipment already removed | 500, message `equipmentDoesNotExist` |
| `GET` of equipment already removed | 400, `errors: ["equipmentDoesNotExist"]` |

The web app's translations also have `EquipmentHaveAlreadyRegistered` ("Equipment have already registered"),
`incorrect_serial_number` and `User has not found`, presumably for registering a serial that is already in an account.

Not tested, to leave the user's Ferminator alone:
- Registering a Ferminator or Graviator, and a serial that is already registered (in this or another account).
- Removing equipment that is linked: whether the other side's `connectedEquipments`/`equipmentId` is cleared, and
  what happens to the brew log.
- Whether a removed Ferminator or Graviator keeps its history if registered again.

## Batch info (tested 2026-11-12 on an unlinked Tilt)

- A partial `PUT /api/v1.0/equipments/<id>` changes only the fields it carries; a field sent as `null` is left as it
  is. Every case below returned 200 with `succeeded: true`.
- The server has no required fields or minimums, unlike the web app's Batch Info form, which requires every field and
  EBC, IBU and volume of 1–9999. Fields are cleared like this:

| Field | Clears with | Ignored | Stored as something else |
|---|---|---|---|
| `brewName`, `owner`, `beerStyle` | `""` | `null` | `0` → `"0"` |
| `ebc`, `ibu`, `volume`, `og`, `fg` | `0` | `null`, `""` | |
| `fermented` | `"None"` or `0` | `null`, `""` | |

- Clearing `brewName` on the Ferminator (tested 2026-11-14, mid-brew) left `brewDate`, `isLoggingData` and the other
  fields as they were, and also cleared the name of its linked hydrometer, but not the name of an unlinked one. The web
  app still showed the brew as started, with no name. Setting the name again set it on the linked hydrometer too.
- A hydrometer's `abv` is worked out from its own `og` and `sg` whenever it is read: 5.2224464 for og 1.058 and
  sg 1.018, i.e. ((og − sg) × 1.05 / sg) / 0.79 × 100. It is `0` while `og` is cleared, and comes back as soon as
  `og` is set again (tested on a linked Tilt). The Ferminator's own `abv` in `data2` is rounded to one decimal.
- `fermented` is a nullable enum `FermentedType`: `None` = 0, `Top` = 1, `Bottom` = 2, as names or numbers. Any
  other string fails with 400 and an error under `errors.fermented`. The web app shows `None` as "None" ("Ingen" in
  Danish), but shows "-" for an empty string or a 0.

## Websocket (SignalR, `devicetwin`)

- Only the Ferminator sends messages, identified by `deviceId`, which matches its `iotHubBrewEquipmentId`. Never a
  hydrometer's ID; hydrometer readings arrive as the Ferminator's.
- The server closes the connection every 1–5 min with `{"type":7,"allowReconnect":true}`.
- Timestamps inside the hourly batch are Danish local time labelled `Z`; those on single commands are real UTC.

| `opType` | Payload |
|---|---|
| `telemetry`, `isTelemetry: false` | One `Command`/`Value`: `tact` (probe °C), `tgrav` (hydrometer °C), `dgrav` (SG, with `abv` in `data2`), `selectgrav` (slot), `fanspeed`, `vbat` |
| `telemetry`, `isTelemetry: true` | Hourly batch of 15-minute samples: `SelectedGraviator`, `GraviatorTemperature`, `GraviatorDensity` (null while no hydrometer is heard), `FerminatorMeasuredTemperature`, `FerminatorSetTemperature`, `HeatingProcess`, `LowProcess`, `GraviatorError` (0 even when the Tilt is lost). Sent once an hour at the same minute (e.g. :16 past), and on boot. |
| `updateTwin` | Device twin changes; often `HProcess`, `LProcess`, `IsRegulatingTemperature`, `IsLoggingData` |
| `Connected` / `Disconnected` | `IsActive` true/false |

`selectgrav` slots, as mapped by the web app:

| Slot | Hydrometer |
|---|---|
| 1–7 | Graviator Red, Blue, Green, Pink, Yellow, Teal, White |
| 8–15 | Tilt Red, Green, Black, Purple, Orange, Blue, Yellow, Pink |

## Connectivity

| Event | Observed |
|---|---|
| Ferminator switched off → `Disconnected` message and `isActive: false` | 2–5 min |
| `Disconnected` → `connectionState: "Disconnected"` | ~15 s to over 4 min |
| Ferminator switched on → `Connected` message and `isActive: true` | within ~30 s, on boot |
| `Connected` → `connectionState: "Connected"` | 0 s to ~4 min |
| Cloud reconnect while online | Every ~48 min. `Disconnected` → `Connected` in 8–34 s (37 cases over two days); `connectionState` stays "Connected" |

So: treat a Ferminator as online while `isActive` is true, and offline once it has been inactive for longer than a
cloud reconnect (this integration uses 60 s), or `connectionState` also says "Disconnected".

## Brew log

- Brew log points are read with `GET /api/v1.0/equipments/<ferminator id>/telemetrydata` and stored on the
  Ferminator only. Each point also sets the linked hydrometer's `lastActivityTime`.
- Points came ~3 m 45 s apart, then stopped for 10+ min mid-brew. The hourly batch also sets the linked
  hydrometer's `lastActivityTime`, to one of its sample times.

## Web app (1.50.2404)

- Shows a hydrometer as active exactly when its Ferminator is; there is no per-hydrometer offline state.
- On the Ferminator card, shows the linked hydrometer's temperature only if the hydrometer's `lastActivityTime` is
  under 3 hours old (hard-coded), else the probe. Always shows its SG.
- Derives "batch started" from `brewDate` being set, not from `isLoggingData`.
- Applies websocket readings as they come to the Ferminator and all its linked hydrometers, with no plausibility
  check.
- Links a hydrometer only from the Ferminator's Device tab, which is disabled while the Ferminator is inactive.
  Saving PUTs the whole device form, then by default copies the batch info to the hydrometer with a second PUT
  (redundant, as the server already does it).
- Saving the Batch Info form sends `brewDate: null`. Calibrating is `PUT /api/v1.0/equipments/<id>/calibrate` with
  `{}`.

## Open questions

- What triggers brew log points, and why they pause.
- What drives the ~48-minute cloud reconnect, and why `connectionState` sometimes follows in seconds and sometimes in
  minutes.
