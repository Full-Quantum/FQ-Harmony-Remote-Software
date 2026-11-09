# FQ-Harmony-Remote-Software

An independent, open-source Windows application for backing up and working with
Logitech Harmony One remote data over USB.

## Why this exists

The original Harmony One configuration service is gone. Although Harmony One
remotes can still be configured with Logitech's newer Harmony software, I worry
that it too could eventually be shut down. I want a way to keep using and
preserving these remotes without relying on an online configuration service.

FQ-Harmony-Remote-Software runs locally and can back up a Harmony One, browse
decoded devices and activities, and edit local Remote Data offline. The
long-term goal is to create and upload complete device and activity
configurations independently. **That goal is not finished:** edits to local
Remote Data generally cannot yet be uploaded to a remote.

For now, I recommend using Logitech's newer Harmony software while it remains
available to add devices and set up activities. Its device library is far
larger than this project's starter library, and setup from scratch here is
still under development.

I also wanted to bring back the **recharging slideshow**: the older software
allowed pictures to appear while a Harmony One sat on its charging pad, a
feature dropped from the newer software. This project can detect and replace
slideshow-cycle pictures on supported configurations, with playback order
decoded from the backup. Picture replacement has been tested on a spare
remote, but support is not universal or complete: slots may be shared with
other screens, screen sleep can interrupt playback, and adding slots or
adjusting slide timing is not supported.

> **A small legal-ish note:** I asked GitHub Copilot to help build this in a
> clean-room setting, with instructions not to borrow Logitech's source code.
> The robot was expected to bring its own tools. This is an independent project,
> not affiliated with or endorsed by Logitech. Harmony and Logitech are
> trademarks of their respective owner.
> Third-party components and their licenses are identified in
> [data sources](docs/DATA-SOURCES.md); this note is not a legal certification.

Status: early development (v0.1.2). See [docs/FEATURES.md](docs/FEATURES.md)
for what works today.

## Version 0.1.2 at a glance

- **Available:** local USB backups, backup browsing, local device/activity
  editing, and fixed-size slideshow picture replacement on supported backups.
- **Experimental:** adding an activity based on existing remote devices. This
  requires Node.js and does not copy every behavior from the template.
- **Not yet available:** general upload of edited or newly created Remote Data,
  adding arbitrary devices to a remote, and complete slideshow management.

The application does not need an internet connection to run. Installing build
dependencies or Node.js, if needed, is a separate step.

The banner displays the application version from `src/harmony_one/__init__.py`.
Versions use `major.minor.patch`: increase the patch for fixes, the minor for
backward-compatible features, and the major for incompatible changes. The
banner also shows the current local Remote Data JSON's save state. This editable
file is distinct from the raw `.EZHex` remote backup. Existing JSON filenames
and formats remain compatible.

## Core concepts

- **Devices**: living-room IR devices (TV, receiver, streamer...) whose IR
  controls are stored on the remote and mapped to physical or touch-screen buttons.
- **Activities**: one-touch sequences that power on several devices and set
  inputs/channels.

Requirements: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).
Clean-room rules: [docs/CLEANROOM.md](docs/CLEANROOM.md).

## Run from source

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m harmony_one
```

## Test

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Build the portable executable

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\HarmonyOne.spec
```

The built Windows GUI is `dist\HarmonyOne.exe`. It is a portable, single-file
desktop app; run it directly without installing Python. Devices, activities,
and sequence edits are currently saved as local Remote Data. Structural
device/activity changes cannot yet be uploaded to the remote. An
experimental **Add activity to remote** workflow uses existing remote devices,
with a fresh backup, candidate inspection and explicit upload acknowledgement.
It uses an optional portable Node.js runtime when present, otherwise a local
Node.js installation, and an attributed independent MIT composer. The portable
runtime is not included in the repository; the activity workflow needs Node.js
installed or supplied separately.
It does not add devices or copy all template behavior. Fixed-size
slideshow and same-length activity-name writes are available only through their
separate verified workflows; see [docs/REMOTE-WRITES.md](docs/REMOTE-WRITES.md).
The USB tab also exports decoded devices and activities as separate JSON
objects from a backup. These are incomplete source archives, not upload-ready
configurations; see [docs/REMOTE-OBJECTS.md](docs/REMOTE-OBJECTS.md).

In an activity's **Settings**, choose its devices and add actions in the order
they should occur. Actions can be moved, removed, or separated with delays.
Optional channel/volume roles and input notes are local metadata; input notes
do not themselves add an action. Save the Remote Data from the main toolbar to
keep these edits. Imported remote state steps may be shown with decoded labels
or numeric input settings where the original names are not available.
Activity Settings also offers built-in icons and custom PNG/GIF artwork, stored
inside local Remote Data and individual activity packages. Custom files must be
at most 2 MiB and 1024 x 1024 pixels; they are fitted to a 32 x 32 preview.
GIF uses its first frame, and partial transparency becomes opaque. This is a
local preview limit, not a verified remote specification. Existing remote icons
are not decoded and icon changes cannot yet be uploaded to a remote.

When a backup starts from any page, the USB tab and a yellow header button
show reading progress. Select the button to see the USB page; the indicator
clears when the read finishes or fails.

The Slideshow tab shows only the detected timed picture cycle in playback order
when the configuration matches the tested layout. The number of available slots
is the maximum for that backup; replacing a picture does not add a slot. If the
cycle cannot be identified, or its screens share a picture, replacement is
disabled rather than offering unrelated pictures. The cycle can include a
charging or system screen, and any picture may be reused on activity or system
screens. Screen sleep can interrupt playback, so the number of slots is not a
guarantee that every image will be visible before the screen turns off.
Adjustable timing and extending the screen-on period are not yet supported.

## Safety

Fixed-size slideshow replacements and one same-length activity rename have been
written and read-back verified on the spare Style A remote. General device and
activity Remote Data changes cannot yet be uploaded because configuration generation
and the general encoder are not implemented. The separate experimental
activity-addition workflow has verified test uploads and owner-confirmed
device-screen navigation on one configuration. Its startup screen still shows
the template name; individual infrared functions need further testing.
See [docs/REMOTE-WRITES.md](docs/REMOTE-WRITES.md)
before any hardware write. Personal backups are git-ignored except for the
owner-approved example listed below.
Writing interrupts and rewrites remote flash; a failed write can leave a remote
unusable, and recovery is not guaranteed. Test with a spare and keep a backup.

## Contributing and examples

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) for setup,
testing, provenance, and hardware safety guidelines.

Owner-approved remote photographs are in `docs/reference`, numbered test images
in `examples/slideshow-test`, and an editable Remote Data JSON and raw `.EZHex`
backup in `examples/primary-remote`. The raw backup contains configuration data
and embedded artwork. The owner confirmed permission to redistribute that
artwork. The third-party manual and PC-client screenshots are not included.
Other personal backups remain ignored; Remote Data JSON and individual exports
may have arbitrary filenames and should be reviewed before sharing.

## License

The project's code is MIT licensed; see [LICENSE](LICENSE). Bundled
third-party components and data have their own notices and terms; see
[docs/DATA-SOURCES.md](docs/DATA-SOURCES.md) and the bundled license files.
The inclusion of example artwork and photographs does not imply they are
licensed under MIT.
