# Requirements (from the owner)

Primary use: a desktop app to alter settings on a Logitech Harmony One remote
connected over USB.

## R1 Devices
The remote holds a list of living-room devices that use IR remotes. Each
device's IR controller is stored on the remote. Most buttons can be mapped to
the remote's physical buttons or to the touch screen (through a menu).

### R1.1 Add from library
Search a built-in library by brand and model and add the matching device.

### R1.2 Add manually (IR learning)
The Harmony One has a built-in IR receiver. With the remote connected by USB,
the user names each button, then points the original remote at the Harmony
One's IR sensor and presses it; the Harmony One captures the signal.

### R1.3 Button mapping
Map a device's buttons to physical buttons and to the touch screen (through a
menu). Owner will supply photos of the remote and screen layouts (docs/reference/).

## R2 Activities
Pre-programmed orchestrations of several devices. One touch-screen button runs a
sequence; a step can be anything the real IR remotes can send.

Example, "Turn on TV": Start; Power toggle on Samsung TV; Power toggle on Sony AV;
Small delay; Set Samsung TV to HDMI 1; Small delay; Set Sony AV to MediaBox input; Done.
Step types: IR command on a device, delay (small/custom), start/done markers.

## R3 Ordering
Reorder Activities on the Activities page and Devices on the Devices page.

## R4 Clock
Set the remote's clock and time zone.

## R5 Brightness
Set the on-board display brightness.

## R6 Slide show
Choose images shown after the charging-battery image when the remote sits on its charger.

## R7 Read from remote
Read the configuration stored on the connected remote and display it. Read-only
until write support is proven safe.

## Proposed additions
See PROPOSED-FEATURES.md. They must not affect R1-R7.

## Open questions
- Photos: remote buttons, touch-screen layouts, slide show screens.
- Slide show image size/format and count limits?
- Per-activity button mapping (e.g. volume to the AV receiver)?
- Favorite channels per device or activity?
