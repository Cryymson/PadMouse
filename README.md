# PadMouse

**Turns your game controller input into a desktop mouse and keyboard.**

PadMouse is a lightweight Windows utility that allows you to control your desktop using an XInput-compatible game controller. It translates controller inputs into mouse movements, scrolling, clicks, and keyboard actions.

Originally developed as a personal utility, PadMouse aims to make desktop navigation more accessible through a game controller.

## Features

- **Analog mouse movement** — Control your cursor using the left analog stick.
- **Scrolling** — Scroll vertically using the right analog stick.
- **Mouse controls** — Perform left, right, and middle clicks using controller inputs.
- **Keyboard navigation** — Use controller buttons for common keyboard actions.
- **Precision mode** — Slow down cursor movement for more accurate positioning.
- **Adaptive polling** — Adjust controller polling behaviour depending on activity.
- **Controller handling** — Handle disconnections and reconnections.

## Controller Mapping

| Controller Input | Action |
|---|---|
| Left Stick | Mouse Movement |
| Right Stick | Scrolling |
| RT | Left Click |
| LT | Right Click |
| R3 | Middle Click |
| L3 | Precision Mode |
| A | Enter |
| B | Escape |
| X | Backspace |
| Y | On-Screen Keyboard |
| D-Pad | Arrow Keys |
| LB | Browser Back |
| RB | Browser Forward |
| Start | Windows Key |
| Back | Alt + Tab |

## Requirements

- Windows operating system
- XInput-compatible game controller

## Installation

Download the latest pre-built executable from the [Releases](../../releases) section.

No separate Python installation should be necessary when using the packaged executable.

## Building from Source

1. Clone or download this repository.
2. Install Python.
3. Run the included `build.bat` file.
4. The executable will be generated in the `dist` directory.

## Technology

- Python
- Windows XInput API
- Windows SendInput API
- PyInstaller

## Project Status

PadMouse V1.0 — Initial working release.

Future development may include configurable controls, a settings interface, controller diagnostics, and system tray integration.
