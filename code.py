import board
import displayio
import sdcardio
import storage
import os
import gc
import json
import struct
import time

from fourwire import FourWire
from adafruit_st7789 import ST7789


# ============================================================
# USER SETTINGS
# ============================================================

# ------------------------------------------------------------
# USB DEVELOPMENT MODE
# ------------------------------------------------------------
#
# True:
#   Adds a pause between frames so USB has time to service
#   the CIRCUITPY drive while developing.
#
# False:
#   Normal animation playback.
#
DEVELOPMENT_MODE = False

USB_IDLE_SECONDS = 0.5

# ------------------------------------------------------------
# COPY MODE
# ------------------------------------------------------------
#
# True:
#   Mounts the SD card and then does nothing, so the PC has the
#   SPI bus to itself while copying animations onto the card.
#
# False:
#   Normal animation playback.
#
COPY_MODE = False


# ------------------------------------------------------------
# PLAYBACK
# ------------------------------------------------------------
#
# With several animations on the card, each one loops for at
# least this long before moving on (loading one takes a few
# seconds, so switching every loop would mostly show loading).
#
MIN_PLAY_SECONDS = 30

# How often to look for new or changed animations on the card
# while a single animation is playing.
RESCAN_SECONDS = 10

# ------------------------------------------------------------
# NEXT ANIMATION: TIMER OR BUTTON
# ------------------------------------------------------------
#
# "timer":
#   Move on to the next animation after MIN_PLAY_SECONDS.
#
# "button":
#   Keep playing the same animation until the button is pressed.
#   Wire a push button between BUTTON_PIN and GND; the pin's
#   internal pull-up is used, so no resistor is needed.
#
ADVANCE_MODE = "timer"

# IO14 is labelled A2 on the FeatherS3. Don't use IO8/IO9 (I2C
# for the battery gauge) or any pin already wired to the TFT/SD.
BUTTON_PIN = board.IO14

# Print read/send timing after the first loop of each animation.
PRINT_TIMING = True


# ------------------------------------------------------------
# LOW BATTERY WARNING
# ------------------------------------------------------------
#
# A battery symbol is drawn over the animation when the LiPo is
# low: yellow below BATTERY_WARN_PERCENT, red below
# BATTERY_CRITICAL_PERCENT. Hidden while USB power is present.
#
BATTERY_WARN_PERCENT = 20
BATTERY_CRITICAL_PERCENT = 10

BATTERY_CHECK_SECONDS = 30

# On USB power the battery is charging (or absent), so the
# symbol is normally hidden. False shows it anyway (testing).
BATTERY_HIDE_ON_USB = True

# Print every battery reading (testing).
BATTERY_PRINT_READINGS = False

# Top-left corner of the symbol in PANEL coordinates (native
# 240 x 320 portrait, before any converter rotation). The
# default is the panel's bottom-left, which is top-right when
# viewed with sourceRotation 180. The symbol is 48 x 24.
BATTERY_ICON_X = 4
BATTERY_ICON_Y = 292

# Turn the symbol and the charging screen 180 degrees to match
# sourceRotation 180.
BATTERY_ICON_FLIP = True


# ------------------------------------------------------------
# CHARGING MODE
# ------------------------------------------------------------
#
# With USB power AND the battery switched on, animations stop
# and a charging screen is shown instead. That also leaves the
# SPI bus free, so files can be copied onto the SD card safely.
#
# With USB power and the battery switched OFF, animations keep
# playing (for development) - don't copy files then.
#
CHARGING_MODE = True

# The battery switch can't be read directly. With the battery
# off, the charger's output wobbles by tenths of a volt and goes
# above 4.3 V; a connected LiPo holds a steady voltage. So the
# battery counts as connected when the last POWER_SAMPLES
# readings (one every POWER_SAMPLE_SECONDS) stay within
# BATTERY_STEADY_VOLTS of each other and below BATTERY_MAX_VOLTS.
POWER_SAMPLE_SECONDS = 0.5
POWER_SAMPLES = 10
BATTERY_STEADY_VOLTS = 0.05
BATTERY_MAX_VOLTS = 4.30

# Print every power reading (testing).
POWER_PRINT_READINGS = False


# ============================================================
# TFT CONFIGURATION
# ============================================================

DISPLAY_BAUDRATE = 80000000

TFT_CS = board.IO10
# Not IO8/IO9: those are the I2C pins the battery gauge needs.
TFT_DC = board.IO5
TFT_RST = board.IO12

PANEL_WIDTH = 240
PANEL_HEIGHT = 320

PANEL_MADCTL = 0x00

# ------------------------------------------------------------
# PANEL REFRESH RATE (FRCTRL2, 0xC6)
# ------------------------------------------------------------
#
# The panel redraws itself from its memory continuously. When a
# frame takes longer to send than one panel refresh, the redraw
# overtakes the write and shows a tear line that drifts up the
# screen. Slowing the refresh lets each frame write finish first.
#
#   0x0F = 60 Hz (ST7789 default)
#   0x15 = 50 Hz
#   0x1F = 39 Hz (slowest)
#
# If the screen flickers, go back towards 0x0F.
#
PANEL_FRAME_RATE = 0x1F


# ============================================================
# SD CONFIGURATION
# ============================================================

SD_CS = board.IO6

# sdcardio defaults to 8 MHz, which makes each 150 KB frame take
# ~300 ms to read. SD cards in SPI mode are rated for 25 MHz.
SD_BAUDRATE = 24000000


# ============================================================
# PIXEL FORMATS
# ============================================================
#
# format name -> (COLMOD value, bytes per frame)
#
# RGB444 packs two pixels into three bytes, so frames are 25%
# smaller than RGB565 and whole animations fit in RAM.
#
PIXEL_FORMATS = {
    "RGB444": (0x53, PANEL_WIDTH * PANEL_HEIGHT * 3 // 2),
    "RGB565": (0x55, PANEL_WIDTH * PANEL_HEIGHT * 2),
}


# ============================================================
# SPI
# ============================================================

displayio.release_displays()

spi = board.SPI()


# ============================================================
# SD CARD
# ============================================================

sd = sdcardio.SDCard(
    spi,
    SD_CS,
    baudrate=SD_BAUDRATE
)

vfs = storage.VfsFat(
    sd
)

# Mounted read-only for CircuitPython so the PC gets write
# access to the card over USB. The board only reads frames.
storage.mount(
    vfs,
    "/sd",
    readonly=True
)

print("SD mounted")

if COPY_MODE:

    print("Copy mode: display idle, copy files to the card now")

    # Never exits: if code.py stops, the SD card unmounts.
    while True:

        time.sleep(1)


# ============================================================
# TFT
# ============================================================

display_bus = FourWire(
    spi,
    command=TFT_DC,
    chip_select=TFT_CS,
    reset=TFT_RST,
    baudrate=DISPLAY_BAUDRATE,
)

# The ST7789 is only used to run the panel init sequence.
# All frames are written raw with display_bus.send(), so the
# displayio "rotation" setting has no effect on them.
#
display = ST7789(
    display_bus,
    width=PANEL_WIDTH,
    height=PANEL_HEIGHT,
    rotation=0,
)

display.auto_refresh = False


# ============================================================
# MADCTL (memory access order)
# ============================================================
#
# Frames are pre-rotated by the converter into the panel's
# native 240 x 320 order, so the panel must NEVER be put into
# a rotated (MV) mode here. Rotation lives only in the converter.
#
#   0x00 = native portrait
#   0xC0 = native portrait, flipped 180 degrees
#
# If everything shows upside down, change this to 0xC0.
# If everything is mirrored, try 0x40 or 0x80.
#
display_bus.send(
    0x36,
    bytes([PANEL_MADCTL])
)

# Normal mode frame rate.
display_bus.send(
    0xC6,
    bytes([PANEL_FRAME_RATE])
)

# Each RAMWR (0x2C) restarts at the top-left corner of the
# address window and fills it row by row.
def set_window(x, y, width, height):

    display_bus.send(
        0x2A,
        struct.pack(
            ">HH",
            x,
            x + width - 1
        )
    )

    display_bus.send(
        0x2B,
        struct.pack(
            ">HH",
            y,
            y + height - 1
        )
    )


def set_full_window():

    set_window(
        0,
        0,
        PANEL_WIDTH,
        PANEL_HEIGHT
    )


set_full_window()


# ============================================================
# LOW BATTERY SYMBOL
# ============================================================
#
# The symbol is pre-encoded in both pixel formats and painted
# straight into the frames, so every frame already contains it.
# (Drawing it on the panel after each frame made it flicker,
# because each new frame briefly painted over it.)
#

# 1 = outline/fill in the warning colour, 0 = black.
# Width must be even: RGB444 packs pixels in pairs.
BATTERY_SHAPE = (
    "000000000000000000000000",
    "011111111111111111111000",
    "010000000000000000001000",
    "010110000000000000001110",
    "010110000000000000000010",
    "010110000000000000000010",
    "010110000000000000000010",
    "010110000000000000000010",
    "010110000000000000001110",
    "010000000000000000001000",
    "011111111111111111111000",
    "000000000000000000000000",
)

BATTERY_ICON_SCALE = 2

BATTERY_ICON_WIDTH = len(BATTERY_SHAPE[0]) * BATTERY_ICON_SCALE
BATTERY_ICON_HEIGHT = len(BATTERY_SHAPE) * BATTERY_ICON_SCALE

BATTERY_YELLOW = (255, 200, 0)
BATTERY_RED = (255, 0, 0)


def encode_icon(color, colmod):

    rows = BATTERY_SHAPE

    if BATTERY_ICON_FLIP:

        # CircuitPython doesn't support row[::-1].
        rows = [
            "".join(
                row[i]
                for i in range(len(row) - 1, -1, -1)
            )
            for row in reversed(rows)
        ]

    pixels = []

    for row in rows:

        line = []

        for bit in row:

            pixel = color if bit == "1" else (0, 0, 0)

            line.extend(
                [pixel] * BATTERY_ICON_SCALE
            )

        for _ in range(BATTERY_ICON_SCALE):

            pixels.extend(line)

    data = bytearray()

    if colmod == PIXEL_FORMATS["RGB565"][0]:

        for r, g, b in pixels:

            data.extend(
                struct.pack(
                    ">H",
                    (r & 0xF8) << 8 | (g & 0xFC) << 3 | b >> 3
                )
            )

    else:

        # Two pixels -> three bytes: R1G1 B1R2 G2B2.
        for i in range(0, len(pixels), 2):

            r1, g1, b1 = [c >> 4 for c in pixels[i]]
            r2, g2, b2 = [c >> 4 for c in pixels[i + 1]]

            data.append(r1 << 4 | g1)
            data.append(b1 << 4 | r2)
            data.append(g2 << 4 | b2)

    return bytes(data)


# (colour, colmod) -> encoded symbol
battery_icons = {}

for warning_color in (BATTERY_YELLOW, BATTERY_RED):

    for colmod, _ in PIXEL_FORMATS.values():

        battery_icons[(warning_color, colmod)] = encode_icon(
            warning_color,
            colmod
        )


# ============================================================
# BATTERY GAUGE (MAX17048)
# ============================================================

try:

    import adafruit_max1704x
    import digitalio

    battery_gauge = adafruit_max1704x.MAX17048(
        board.I2C()
    )

    vbus_sense = digitalio.DigitalInOut(
        board.VBUS_SENSE
    )

    vbus_sense.direction = digitalio.Direction.INPUT

except Exception as e:

    battery_gauge = None

    print(
        "Battery gauge unavailable:",
        e
    )

# None, BATTERY_YELLOW or BATTERY_RED
battery_warning = None

next_battery_check = 0


def update_battery_warning():

    global battery_warning, next_battery_check

    now = time.monotonic()

    if battery_gauge is None or now < next_battery_check:

        return

    next_battery_check = now + BATTERY_CHECK_SECONDS

    try:

        on_usb = vbus_sense.value

        percent = battery_gauge.cell_percent

        voltage = battery_gauge.cell_voltage

    except Exception as e:

        print(
            "Battery read failed:",
            e
        )

        battery_warning = None
        return

    if BATTERY_PRINT_READINGS:

        print(
            "Battery {:.0f}% {:.2f} V, USB power: {}".format(
                percent,
                voltage,
                on_usb
            )
        )

    if on_usb and BATTERY_HIDE_ON_USB:

        battery_warning = None
        return

    if percent < BATTERY_CRITICAL_PERCENT:

        warning = BATTERY_RED

    elif percent < BATTERY_WARN_PERCENT:

        warning = BATTERY_YELLOW

    else:

        warning = None

    if warning != battery_warning:

        print(
            "Battery {:.0f}%".format(percent)
        )

    battery_warning = warning


# ============================================================
# POWER STATE (charging or playing)
# ============================================================

# Recent battery voltages while on USB power.
power_samples = []

next_power_sample = 0

charging = False


def update_power():

    global charging, next_power_sample

    if not CHARGING_MODE or battery_gauge is None:

        return

    now = time.monotonic()

    if now < next_power_sample:

        return

    next_power_sample = now + POWER_SAMPLE_SECONDS

    try:

        on_usb = vbus_sense.value

        if on_usb:

            power_samples.append(
                battery_gauge.cell_voltage
            )

    except Exception as e:

        print(
            "Power check failed:",
            e
        )

        return

    if not on_usb:

        # Unplugged: running on the battery, so play at once.
        del power_samples[:]

        battery_connected = False

    else:

        if len(power_samples) > POWER_SAMPLES:

            power_samples.pop(0)

        # Keep the current state until there are enough readings.
        if len(power_samples) < POWER_SAMPLES:

            return

        lowest = min(power_samples)
        highest = max(power_samples)

        battery_connected = (
            highest - lowest <= BATTERY_STEADY_VOLTS
            and
            highest <= BATTERY_MAX_VOLTS
        )

        if POWER_PRINT_READINGS:

            print(
                "Power: USB, {:.2f}-{:.2f} V, battery connected: {}".format(
                    lowest,
                    highest,
                    battery_connected
                )
            )

    if battery_connected != charging:

        charging = battery_connected

        print(
            "Charging mode: animations paused"
            if charging
            else "Playing animations"
        )


# At power-up on USB, find out whether the battery is connected
# before touching the SD card (takes POWER_SAMPLES readings).
def wait_for_power_state():

    if not CHARGING_MODE or battery_gauge is None:

        return

    try:

        on_usb = vbus_sense.value

    except Exception:

        return

    if not on_usb:

        return

    print("USB power: checking whether the battery is connected...")

    # update_power() decides as soon as it has all the readings.
    while vbus_sense.value and len(power_samples) < POWER_SAMPLES:

        update_power()

        time.sleep(0.05)


# ============================================================
# CHARGING SCREEN
# ============================================================
#
# Drawn in RGB565 with filled rectangles, in viewer coordinates
# (turned 180 degrees when BATTERY_ICON_FLIP is set). Only
# redrawn when the percentage changes, so the SPI bus stays idle
# for copying.
#

# 3 x 5 characters, scaled up. Only what the screen uses.
FONT = {
    " ": ("000", "000", "000", "000", "000"),
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("110", "101", "101", "101", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "W": ("101", "101", "111", "111", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "%": ("101", "001", "010", "100", "101"),
}

CHARGING_GREEN = (0, 200, 0)
CHARGING_WHITE = (255, 255, 255)
CHARGING_GREY = (150, 150, 150)
BLACK = (0, 0, 0)

# Small hint lines at the top.
HINT_LINES = (
    "SWITCH BATTERY OFF",
    "TO PLAY ANIMATIONS",
)

HINT_SCALE = 3
HINT_Y = 8
HINT_LINE_SPACING = 20

# Battery outline, in viewer coordinates.
BODY_X = 60
BODY_Y = 72
BODY_WIDTH = 120
BODY_HEIGHT = 170
BODY_LINE = 8

TERMINAL_WIDTH = 50
TERMINAL_HEIGHT = 16

# Inside of the battery, leaving a black gap inside the outline.
FILL_X = BODY_X + 2 * BODY_LINE
FILL_Y = BODY_Y + 2 * BODY_LINE
FILL_WIDTH = BODY_WIDTH - 4 * BODY_LINE
FILL_HEIGHT = BODY_HEIGHT - 4 * BODY_LINE

# Percentage below the battery.
PERCENT_SCALE = 8
PERCENT_Y = 262


def rgb565(color):

    r, g, b = color

    return struct.pack(
        ">H",
        (r & 0xF8) << 8 | (g & 0xFC) << 3 | b >> 3
    )


def fill_rect(x, y, width, height, color):

    if width <= 0 or height <= 0:

        return

    if BATTERY_ICON_FLIP:

        x = PANEL_WIDTH - x - width
        y = PANEL_HEIGHT - y - height

    set_window(
        x,
        y,
        width,
        height
    )

    display_bus.send(
        0x2C,
        rgb565(color) * (width * height)
    )


# Centred on the screen; scale is the size of one font dot.
def draw_text(text, y, color, scale):

    char_width = 3 * scale
    gap = scale

    total_width = len(text) * char_width + (len(text) - 1) * gap

    x = (PANEL_WIDTH - total_width) // 2

    # Clear the whole text line first.
    fill_rect(
        0,
        y,
        PANEL_WIDTH,
        5 * scale,
        BLACK
    )

    for char in text:

        for row, bits in enumerate(FONT[char]):

            for column, bit in enumerate(bits):

                if bit == "1":

                    fill_rect(
                        x + column * scale,
                        y + row * scale,
                        scale,
                        scale,
                        color
                    )

        x += char_width + gap


def draw_charging_screen(percent, full_redraw):

    display_bus.send(
        0x3A,
        bytes([PIXEL_FORMATS["RGB565"][0]])
    )

    if full_redraw:

        fill_rect(0, 0, PANEL_WIDTH, PANEL_HEIGHT, BLACK)

        for line_number, line in enumerate(HINT_LINES):

            draw_text(
                line,
                HINT_Y + line_number * HINT_LINE_SPACING,
                CHARGING_GREY,
                HINT_SCALE
            )

        # Terminal, then the four sides of the body.
        fill_rect(
            BODY_X + (BODY_WIDTH - TERMINAL_WIDTH) // 2,
            BODY_Y - TERMINAL_HEIGHT,
            TERMINAL_WIDTH,
            TERMINAL_HEIGHT,
            CHARGING_WHITE
        )

        fill_rect(BODY_X, BODY_Y, BODY_WIDTH, BODY_LINE, CHARGING_WHITE)

        fill_rect(
            BODY_X,
            BODY_Y + BODY_HEIGHT - BODY_LINE,
            BODY_WIDTH,
            BODY_LINE,
            CHARGING_WHITE
        )

        fill_rect(BODY_X, BODY_Y, BODY_LINE, BODY_HEIGHT, CHARGING_WHITE)

        fill_rect(
            BODY_X + BODY_WIDTH - BODY_LINE,
            BODY_Y,
            BODY_LINE,
            BODY_HEIGHT,
            CHARGING_WHITE
        )

    if percent is None:

        return

    if percent < BATTERY_CRITICAL_PERCENT:

        color = BATTERY_RED

    elif percent < BATTERY_WARN_PERCENT:

        color = BATTERY_YELLOW

    else:

        color = CHARGING_GREEN

    # Fill from the bottom up.
    level_height = FILL_HEIGHT * percent // 100

    fill_rect(
        FILL_X,
        FILL_Y,
        FILL_WIDTH,
        FILL_HEIGHT - level_height,
        BLACK
    )

    fill_rect(
        FILL_X,
        FILL_Y + FILL_HEIGHT - level_height,
        FILL_WIDTH,
        level_height,
        color
    )

    draw_text(
        str(percent) + "%",
        PERCENT_Y,
        CHARGING_WHITE,
        PERCENT_SCALE
    )


def read_percent():

    try:

        # The gauge reads a little over 100% when full.
        return max(
            0,
            min(
                100,
                int(battery_gauge.cell_percent + 0.5)
            )
        )

    except Exception as e:

        print(
            "Battery read failed:",
            e
        )

        return None


# Shows the charging screen until USB is unplugged or the
# battery is switched off.
def run_charging_mode():

    shown_percent = -1
    full_redraw = True

    next_percent_check = 0

    while charging:

        now = time.monotonic()

        if now >= next_percent_check:

            next_percent_check = now + BATTERY_CHECK_SECONDS

            percent = read_percent()

            if full_redraw or percent != shown_percent:

                draw_charging_screen(
                    percent,
                    full_redraw
                )

                shown_percent = percent
                full_redraw = False

        time.sleep(0.1)

        update_power()

    # Playback writes whole frames again.
    set_full_window()


def battery_icon_rows(colmod):

    # Bytes per pixel as a fraction: RGB444 is 3/2, RGB565 is 2/1.
    # BATTERY_ICON_X must be even so RGB444 rows start on a byte.
    if colmod == PIXEL_FORMATS["RGB565"][0]:

        numerator, denominator = 2, 1

    else:

        numerator, denominator = 3, 2

    row_bytes = BATTERY_ICON_WIDTH * numerator // denominator

    # (offset in frame, offset in symbol, length) per symbol row
    return [
        (
            ((BATTERY_ICON_Y + row) * PANEL_WIDTH + BATTERY_ICON_X)
            * numerator // denominator,
            row * row_bytes,
            row_bytes
        )
        for row in range(BATTERY_ICON_HEIGHT)
    ]


def paint_icon_area(frame, rows, pixels):

    for frame_offset, pixels_offset, length in rows:

        frame[frame_offset:frame_offset + length] = pixels[
            pixels_offset:pixels_offset + length
        ]


def copy_icon_area(frame, rows):

    # Same layout as an encoded symbol, so paint_icon_area() can
    # put it back. (CircuitPython's b"".join() rejects bytearrays.)
    pixels = bytearray()

    for frame_offset, _, length in rows:

        pixels.extend(
            frame[frame_offset:frame_offset + length]
        )

    return pixels


# Brings the frames in RAM up to date with battery_warning: paints
# the symbol in, or puts back the original pixels.
def apply_battery_warning(animation):

    frames = animation["frames"]

    # Streamed frames are painted as they are read instead.
    if frames is None or animation["icon_color"] == battery_warning:

        return

    rows = animation["icon_rows"]

    if animation["icon_backup"] is None:

        animation["icon_backup"] = [
            copy_icon_area(frame, rows)
            for frame in frames
        ]

    for frame, backup in zip(frames, animation["icon_backup"]):

        if battery_warning is None:

            pixels = backup

        else:

            pixels = battery_icons[(battery_warning, animation["colmod"])]

        paint_icon_area(
            frame,
            rows,
            pixels
        )

    animation["icon_color"] = battery_warning


# ============================================================
# NEXT-ANIMATION BUTTON
# ============================================================
#
# keypad scans the pin in the background and debounces it, so a
# short press isn't missed while a frame is being sent.
#

buttons = None

if ADVANCE_MODE == "button":

    try:

        import keypad

        buttons = keypad.Keys(
            (BUTTON_PIN,),
            value_when_pressed=False,
            pull=True
        )

        print("Button mode: press the button for the next animation")

    except Exception as e:

        print(
            "Button unavailable, using the timer:",
            e
        )


# Set when the button is pressed during playback.
advance_requested = False


def button_pressed():

    if buttons is None:

        return False

    pressed = False

    event = buttons.events.get()

    while event:

        if event.pressed:

            pressed = True

        event = buttons.events.get()

    return pressed


# ============================================================
# FIND ANIMATIONS
# ============================================================
#
# Returns {folder name: signature}. The signature changes when
# the animation is re-converted, so it gets reloaded.
#

reported_skips = set()


def find_animations():

    animations = {}

    for name in os.listdir("/sd"):

        folder = "/sd/" + name

        # Folders without animation.json (e.g. System Volume
        # Information) are silently ignored.
        try:

            os.stat(folder + "/animation.json")

        except OSError:

            continue

        try:

            metadata = read_metadata(
                folder
            )

            info = os.stat(
                folder + "/" + metadata["file"]
            )

            # (size, modification time)
            animations[name] = (
                info[6],
                info[8]
            )

        except Exception as e:

            # Only report each problem once, not every rescan.
            message = name + " : " + str(e)

            if message not in reported_skips:

                reported_skips.add(
                    message
                )

                print(
                    "Skipping",
                    message
                )

    return animations


# ============================================================
# READ / VALIDATE METADATA
# ============================================================

def read_metadata(folder):

    with open(
        folder + "/animation.json",
        "r"
    ) as f:

        metadata = json.load(f)

    if (
        metadata.get("width") != PANEL_WIDTH
        or
        metadata.get("height") != PANEL_HEIGHT
        or
        metadata.get("format") not in PIXEL_FORMATS
        or
        "file" not in metadata
    ):
        raise ValueError(
            "not a "
            + str(PANEL_WIDTH)
            + "x"
            + str(PANEL_HEIGHT)
            + " animation from the current converter (re-run it)"
        )

    frame_count = int(
        metadata["frames"]
    )

    frame_size = PIXEL_FORMATS[metadata["format"]][1]

    size = os.stat(
        folder + "/" + metadata["file"]
    )[6]

    if size != frame_count * frame_size:
        raise ValueError(
            metadata["file"]
            + " is "
            + str(size)
            + " bytes, expected "
            + str(frame_count * frame_size)
            + " (still copying?)"
        )

    return metadata


# ============================================================
# LOAD ANIMATION
# ============================================================
#
# Loads every frame into RAM when it fits, so playback is only
# limited by the display SPI (~23 ms per RGB444 frame). Reading
# from the SD card manages ~150 ms per frame at best.
#
# If it doesn't fit, frames are streamed from the card instead.
#

def load_animation(name):

    folder = "/sd/" + name

    metadata = read_metadata(
        folder
    )

    colmod, frame_size = PIXEL_FORMATS[metadata["format"]]

    frame_count = int(
        metadata["frames"]
    )

    durations = metadata.get(
        "durationsMs",
        [100] * frame_count
    )

    animation = {
        "name": name,
        "path": folder + "/" + metadata["file"],
        "colmod": colmod,
        "frame_size": frame_size,
        "frame_count": frame_count,
        "durations_ns": [
            int(d) * 1000000
            for d in durations
        ],
        "frames": None,
        "icon_rows": battery_icon_rows(colmod),
        # Warning colour currently painted into the frames.
        "icon_color": None,
        # Original pixels under the symbol, one per frame.
        "icon_backup": None,
    }

    gc.collect()

    needed = frame_size * frame_count

    print()
    print(
        "Loading:",
        name,
        "(" + metadata["format"] + ",",
        frame_count,
        "frames,",
        needed // 1024,
        "KB, free RAM",
        gc.mem_free() // 1024,
        "KB)"
    )

    # gc.mem_free() under-reports on ESP32-S3 because the heap
    # grows on demand, so just try and fall back on MemoryError.
    start = time.monotonic()

    try:

        frames = []

        with open(
            animation["path"],
            "rb"
        ) as f:

            for _ in range(frame_count):

                # Stop reading the card as soon as charging starts.
                update_power()

                if charging:

                    print("Loading stopped: charging")

                    return None

                frame = bytearray(
                    frame_size
                )

                read_fully(
                    f,
                    frame
                )

                frames.append(
                    frame
                )

        animation["frames"] = frames

        print(
            "Loaded into RAM in {:.1f} s".format(
                time.monotonic() - start
            )
        )

    except MemoryError:

        frames = None
        gc.collect()

        print(
            "Ran out of RAM, streaming from SD (slow)"
        )

    return animation


def read_fully(f, buffer):

    view = memoryview(buffer)
    offset = 0

    while offset < len(buffer):

        n = f.readinto(
            view[offset:]
        )

        if not n:

            raise RuntimeError(
                "Unexpected end of file"
            )

        offset += n


# ============================================================
# PLAY ANIMATION (one loop)
# ============================================================

def play_animation(animation, report_timing):

    global advance_requested

    display_bus.send(
        0x3A,
        bytes([animation["colmod"]])
    )

    # A change in the warning shows from the next loop on.
    apply_battery_warning(
        animation
    )

    frames = animation["frames"]
    durations_ns = animation["durations_ns"]
    frame_count = animation["frame_count"]

    stream = None
    buffer = None

    if frames is None:

        stream = open(
            animation["path"],
            "rb"
        )

        buffer = bytearray(
            animation["frame_size"]
        )

    read_ns_total = 0
    send_ns_total = 0
    late_frames = 0

    # Frames are scheduled against an absolute clock so small
    # per-frame overheads don't add up into drift.
    next_frame_ns = time.monotonic_ns()

    try:

        for frame_number in range(frame_count):

            read_start_ns = time.monotonic_ns()

            if stream is None:

                frame = frames[frame_number]

            else:

                read_fully(
                    stream,
                    buffer
                )

                if battery_warning is not None:

                    paint_icon_area(
                        buffer,
                        animation["icon_rows"],
                        battery_icons[(battery_warning, animation["colmod"])]
                    )

                frame = buffer

            send_start_ns = time.monotonic_ns()

            display_bus.send(
                0x2C,
                frame
            )

            send_end_ns = time.monotonic_ns()

            update_battery_warning()

            update_power()

            # Stop mid-loop so the bus goes quiet right away.
            if charging:

                return

            if button_pressed():

                advance_requested = True

                return

            read_ns_total += send_start_ns - read_start_ns
            send_ns_total += send_end_ns - send_start_ns

            next_frame_ns += durations_ns[frame_number]

            wait_ns = next_frame_ns - time.monotonic_ns()

            if wait_ns > 0:

                time.sleep(
                    wait_ns / 1000000000
                )

            else:

                late_frames += 1

                # Too far behind: resync instead of rushing
                # through frames to catch up.
                if -wait_ns > durations_ns[frame_number]:

                    next_frame_ns = time.monotonic_ns()

            if DEVELOPMENT_MODE:

                time.sleep(
                    USB_IDLE_SECONDS
                )

                next_frame_ns = time.monotonic_ns()

    finally:

        if stream is not None:

            stream.close()

    if report_timing and PRINT_TIMING:

        read_ms = read_ns_total / frame_count / 1000000
        send_ms = send_ns_total / frame_count / 1000000
        target_ms = sum(durations_ns) / frame_count / 1000000

        print(
            "Timing: read {:.1f} ms + send {:.1f} ms = {:.1f} ms/frame"
            " (target {:.1f} ms), late frames: {}/{}".format(
                read_ms,
                send_ms,
                read_ms + send_ms,
                target_ms,
                late_frames,
                frame_count
            )
        )


# ============================================================
# MAIN LOOP
# ============================================================
#
# Never exits: if code.py stops, CircuitPython unmounts the SD
# card and it disappears from the PC as well.
#

RETRY_SECONDS = 2

current = None
current_signature = None

previous_names = None
next_index = 0

wait_for_power_state()

while True:

    update_power()

    if charging:

        # Free the animation; it's reloaded after charging.
        current = None
        gc.collect()

        run_charging_mode()

        continue

    animations = find_animations()

    names = sorted(
        animations
    )

    if names != previous_names:

        print(
            "Animations found:",
            names
        )

        previous_names = names

    if not names:

        time.sleep(
            RETRY_SECONDS
        )

        continue

    # Pick the next animation in order.
    name = names[next_index % len(names)]
    next_index += 1

    try:

        # Only reload when it's a different animation or the
        # files on the card changed.
        if (
            current is None
            or
            current["name"] != name
            or
            current_signature != animations[name]
        ):

            # Free the previous animation before loading.
            current = None
            gc.collect()

            current = load_animation(
                name
            )

            # Charging started while loading: load this one again
            # afterwards.
            if current is None:

                next_index -= 1
                gc.collect()

                continue

            current_signature = animations[name]

            first_loop = True

        else:

            first_loop = False

        # Presses while loading don't count.
        button_pressed()
        advance_requested = False

        started = time.monotonic()

        # With one animation, rescan the card every RESCAN_SECONDS.
        # Otherwise loop for MIN_PLAY_SECONDS, or until the button
        # is pressed in button mode.
        if len(names) == 1:

            play_seconds = RESCAN_SECONDS

        elif buttons is not None:

            play_seconds = None

        else:

            play_seconds = MIN_PLAY_SECONDS

        while True:

            play_animation(
                current,
                first_loop
            )

            first_loop = False

            if charging:

                # Carry on with this animation after charging.
                next_index -= 1

                break

            if advance_requested:

                break

            if (
                play_seconds is not None
                and
                time.monotonic() - started >= play_seconds
            ):

                break

    except Exception as e:

        # A broken or half-copied animation is skipped instead
        # of stopping playback.
        print(
            "Error playing",
            name,
            ":",
            e
        )

        current = None
        gc.collect()

        time.sleep(
            RETRY_SECONDS
        )
