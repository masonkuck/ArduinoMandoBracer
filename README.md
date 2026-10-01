# ArduinoMandoBracer

A wrist bracer that plays animated GIFs on a 2.0" 240x320 ST7789 TFT,
driven by an Unexpected Maker **FeatherS3** running **CircuitPython 10**.
GIFs are converted on the PC into raw frames, copied onto a microSD card,
loaded into RAM by the board and pushed to the display as fast as SPI allows.

- `code.py` – the CircuitPython program (copy to the `CIRCUITPY` drive).
- `GifConverter/` – .NET console app that turns GIFs into frame files.
- `animations/` – your GIFs and their converted frames (ignored by git).

## Hardware

| Part | Notes |
|---|---|
| Unexpected Maker FeatherS3 (ESP32-S3) | CircuitPython 10.2.1. Has an onboard MAX17048 battery gauge and a USB-power sense pin. |
| 2.0" 240x320 ST7789 TFT breakout with microSD slot | The display and the card share one SPI bus. |
| microSD card | FAT32. Holds the converted animations. |
| LiPo battery with an inline on/off switch | Plugs into the FeatherS3's battery connector. |

## Pinout

| TFT breakout pin | FeatherS3 pin | Used in `code.py` as |
|---|---|---|
| SCK | SCK (IO36) | `board.SPI()` |
| MOSI | MOSI (IO35) | `board.SPI()` |
| MISO | MISO (IO37) | `board.SPI()` (SD card reads) |
| TFT CS | IO10 | `TFT_CS` |
| DC | IO5 (A5) | `TFT_DC` |
| RST | IO12 (A3) | `TFT_RST` |
| SD CS | IO6 (A4) | `SD_CS` |
| VIN | 3V3 | power |
| GND | GND | ground |

Optional next-animation button (see [Button mode](#button-mode)):

| Button leg | FeatherS3 pin | Used in `code.py` as |
|---|---|---|
| One side | IO14 (A2) | `BUTTON_PIN` |
| Other side | GND | – |

> **Keep IO8 and IO9 free.** They are the FeatherS3's I2C pins (SDA/SCL),
> which the onboard battery gauge uses. If the display is wired to them,
> `code.py` prints `Battery gauge unavailable: SCL in use` and the battery
> features switch off.

Used on the board itself, no wiring needed:

| Signal | Pin | Used for |
|---|---|---|
| MAX17048 battery gauge | I2C (IO8/IO9) | Battery percentage and voltage |
| USB power sense | `board.VBUS_SENSE` | Detecting USB power |

## How it works

### Converting GIFs

Keep GIFs in the `animations/` folder. Git ignores everything in it, so
GIFs and converted frames never get committed.

```
dotnet run --project GifConverter -c Release -- animations/my.gif
```

This writes `animations/my_frames/` next to the GIF:

- `frames.bin` – every frame, back to back, as raw 240x320 pixels.
- `animation.json` – frame count, pixel format and per-frame delays.

Frames are pre-rotated into the panel's native portrait order, so the board
never rotates anything. The default colour format is **RGB444** (12-bit), which
is small enough for whole animations to fit in RAM. Run with `--help` for
rotation, fit, dithering and colour options.

### Playback

On power-up `code.py` mounts the SD card, finds every folder with an
`animation.json`, and plays them in alphabetical order. Each animation loops
for at least 30 seconds (`MIN_PLAY_SECONDS`). Frames are loaded into RAM first,
so each frame takes only about 13 ms to send. Animations that don't fit in RAM
are streamed from the card instead, which is much slower.

The card is mounted **read-only** for the board, so the PC can write to it over
USB: it shows up as a second drive next to `CIRCUITPY`.

### Button mode

Set `ADVANCE_MODE = "button"` to switch animations with a push button instead
of the timer. The current animation then keeps looping until the button is
pressed, and the next one loads straight away. Wire the button between
`BUTTON_PIN` (IO14 by default) and GND. The pin's internal pull-up is used, so
no resistor is needed.

The pin is watched in the background with debouncing (CircuitPython's
`keypad` module), so a quick tap isn't missed while a frame is being sent.
Presses made while an animation is loading, or in charging mode, are ignored.
If the pin can't be set up, `code.py` prints a message and falls back to the
timer.

### Charging mode

| USB | Battery switch | What happens |
|---|---|---|
| Plugged in | On | **Charging mode:** animations stop and a charging screen shows the battery level. |
| Plugged in | Off | Animations play (handy while developing). |
| Unplugged | On | Animations play. |

The battery switch can't be read directly, so the board looks at the battery
voltage while on USB. A connected LiPo holds a steady voltage. With the switch
off, the charger's output wobbles by tenths of a volt and goes above 4.3 V.
Charging mode starts after about 10 seconds of steady readings and ends as soon as
USB is unplugged. The thresholds are in the `CHARGING MODE` settings.

### Low battery warning

When running on the battery, a battery symbol appears in the top-right corner:
**yellow** below 20% and **red** below 10% (`BATTERY_WARN_PERCENT`,
`BATTERY_CRITICAL_PERCENT`). It is painted into the frames in RAM, so it doesn't
flicker, and it is hidden on USB power.

## Adding animations

**Only copy files to the SD card while the board is in charging mode.** The
display and SD card share the SPI bus. Files copied while an animation is
playing arrive corrupted, with parts of the file landing in the wrong place.

1. Plug in USB with the battery switch **on** and wait for the charging screen.
2. Convert straight onto the card, or copy the `_frames` folder over:
   ```
   dotnet run --project GifConverter -c Release -- animations/my.gif -o H:\
   ```
3. Unplug USB or press reset. The new animation joins the rotation.

Without a battery, set `COPY_MODE = True` in `code.py` instead. The board then
mounts the card and leaves the display idle. Set it back to `False` afterwards.

> Windows caches what it writes. To check a copy really landed, reset the board
> first, then compare the files.

## Updating `code.py`

Copy `code.py` onto the `CIRCUITPY` drive. CircuitPython restarts it
automatically. The settings are at the top of the file:

| Setting | Default | What it does |
|---|---|---|
| `ADVANCE_MODE` | `"timer"` | `"timer"` or `"button"`: what moves on to the next animation |
| `BUTTON_PIN` | `board.IO14` | Button input for button mode, wired to GND |
| `MIN_PLAY_SECONDS` | 30 | How long each animation plays before the next one (timer mode) |
| `CHARGING_MODE` | True | Pause animations while charging |
| `COPY_MODE` | False | Mount the card and keep the display idle, for copying files |
| `BATTERY_WARN_PERCENT` / `BATTERY_CRITICAL_PERCENT` | 20 / 10 | When the yellow / red symbol appears |
| `BATTERY_ICON_FLIP` | True | Turn the symbol and charging screen 180° to match how the panel is mounted |
| `PANEL_FRAME_RATE` | 0x1F | Panel refresh rate. Slower reduces tearing. |
| `POWER_PRINT_READINGS` / `BATTERY_PRINT_READINGS` | False | Print battery readings to the serial console, for debugging |

Required libraries in `CIRCUITPY/lib`: `adafruit_st7789`, `adafruit_max1704x`,
`adafruit_bus_device` and `adafruit_register`.
