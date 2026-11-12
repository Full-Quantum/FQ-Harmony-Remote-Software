# FQ-Harmony-Remote-Software

Independent, open-source Windows software for backing up and working with
Logitech Harmony One remotes over USB. Runs locally without an online
configuration service.

## Download v0.3.0

**[Download the Windows desktop app](https://github.com/Full-Quantum/FQ-Harmony-Remote-Software/releases/download/v0.3.0/FQ-Harmony-Remote-Software-v0.3.0.exe)**

[Latest release and notes](https://github.com/Full-Quantum/FQ-Harmony-Remote-Software/releases/latest)

The portable executable does not need Python installed. Put it in a writable
folder before running it: backups, icons, slideshow images, and saved custom
styles are kept beside the app, not in AppData. Windows may warn about this
unsigned application; download only from this repository's releases.

**Early development:** general device/activity configuration generation is
not finished. Local Remote Data JSON edits are not generally uploadable.
Keep an original backup and use a spare remote for experimental writes.

## Why this exists

The original Harmony One configuration service is gone. Although Harmony One
remotes can still be configured with Logitech's newer Harmony software, I worry
that it too could eventually be shut down. I want a way to keep using and
preserving these remotes without relying on an online configuration service.

For now, I recommend using Logitech's newer Harmony software while it remains
available to add devices and set up activities. Its device library is far
larger than this project's starter library, and setup from scratch here is
still under development.

I also wanted to bring back the **recharging slideshow**: the older software
allowed pictures to appear while a Harmony One sat on its charging pad, a
feature dropped from the newer software.

> **A small legal-ish note:** I asked GitHub Copilot to help build this in a
> clean-room setting, with instructions not to borrow Logitech's source code.
> The robot was expected to bring its own tools. This is an independent project,
> not affiliated with or endorsed by Logitech. Harmony and Logitech are
> trademarks of their respective owner.
> Third-party components and their licenses are identified in
> [data sources](docs/DATA-SOURCES.md); this note is not a legal certification.

## New in v0.3.0

- **Home remote preview:** back up a connected remote and edit a copy; preview
  decoded activity/device pages, names, order, artwork, icons, and detected style.
  Activities appears before Devices throughout the relevant UI.
- **Backups & Transfer:** browse saved contents, select the bundled older/newer
  FQ presets or another backup, and back up/check a destination before a guarded
  full-configuration transfer. Backup-folder shortcuts are included.
- **Remote icons:** change individual activity/device menu icons; scan a remote
  and save its menu icons to the local `icons` folder. User-added PNG/GIF files
  appear in Change icon pickers. Defaults include 15 menu icons and 86 additional
  system/button images in `icons/button_assets`; those other-size images are
  not offered as menu-row icons.
- **Display styles and custom-style maker:** separate Activities and Devices
  pages offer solid, two-colour gradient, or fitted PNG/GIF backgrounds, button
  fill, rounded/sharp corners, and optional coloured borders with pixel
  thickness. Preview and save named artwork for the existing apply workflow.
  Embedded text/font controls remain unavailable and existing text is preserved.
- **Slideshow:** six visible slots, per-slot image browsing and whole-second
  time controls. All six default time fields start at **3 seconds**; loading a
  backup preserves its current timings until edited.
- **Return to default slideshow:** restore the original four bundled images
  (charging battery and three blurred-light images) at 3 seconds each in the
  candidate. A supported six-image cycle returns to four active images, leaving
  slots 5 and 6 empty and disabled. Reset uses bundled originals, not customized
  user-library copies.
- **Clock:** set wall time using the local or a selected IANA time zone.
  The remote does not retain the selected zone or future daylight-saving rules.
- **Remote Assistant:** turn recognized saved prompt settings on/off without
  deleting assistant screens; unsupported layouts are explicitly disabled.

See [feature status](docs/FEATURES.md) and [UI reference](docs/UI-REFERENCE.md)
for details.

## What remains limited

- Structural device/activity changes in local Remote Data JSON cannot generally
  be compiled and uploaded. Individual object exports are archival source data,
  not transferable remote configurations.
- Experimental activity addition uses devices already in the backup, requires
  Node.js, and does not add arbitrary new devices or copy every template behavior.
- Slideshow slots are limited to the recognized backup cycle; four-image cycles
  are not expanded to six. Shared pictures can also appear on system/activity
  screens, and screen sleep may interrupt playback.
- Saving a custom style stores self-contained artwork, not an editable design
  recipe. Applying display styles replaces recognized menu-row icons with a
  generic list icon while preserving labels and order.
- Cross-remote firmware compatibility, custom styles, icon edits, clock changes,
  Assistant settings, and the new slideshow timing/cycle-shortening behavior
  are not hardware-validated. Offline validation is not a hardware guarantee.

## Basic workflow

1. Connect a charged Harmony One over USB and choose **Back up connected Harmony
   One Remote and start editing a copy...** on Home.
2. Keep the saved original `.EZHex`. Local Remote Data JSON is a separate file.
3. Use Activities, Devices, Slideshow, Clock, Remote Assistant, Display Style,
   or Backups & Transfer as appropriate. Preview and prepare changes offline.
4. Only use an explicit write action when ready. Hardware writes require the
   relevant confirmations, a matching live baseline, and read-back verification.
   The transfer workflow replaces the complete destination configuration.

**Discard unwritten changes** on Slideshow restores the loaded images, timings,
and cycle. Editing or resetting a slideshow does not itself write to USB.

## Safety

Fixed-size slideshow picture replacement and one same-length activity rename
have been written and read-back verified on a spare Style A remote.
Experimental activity-addition uploads and corrected device-screen navigation
have also been tested on one configuration; infrared behavior needs more testing.

Writing erases and rewrites remote flash; interruption can leave a remote
unusable, and recovery is not guaranteed. Read
[remote-write safety and evidence](docs/REMOTE-WRITES.md) before writing.
Keep the remote connected until completion and preserve recovery backups.

## Run from source

Use Python 3.10 or later on Windows:

```powershell
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m harmony_one
```

The optional activity composer needs Node.js installed separately.
Normal app operation is offline; installing dependencies is a separate step.

### Test

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Private-backup-dependent tests may be skipped. Public presets and examples are
included for reproducible offline checks.

### Build the portable executable

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\HarmonyOne.spec
```

The result is `dist\HarmonyOne.exe`. Versions use `major.minor.patch`, and the
app banner identifies its version and separate local Remote Data save state.

## Contributing, bundled data, and license

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
Follow [clean-room guidance](docs/CLEANROOM.md), include test evidence, and
review all files before sharing.

Owner-approved remote photographs and examples remain included. v0.3.0 also
includes the two owner-approved FQ preset backups and extracted default
icon/slideshow/style artwork. These assets contain remote configuration and
embedded artwork and are distributed with the owner's explicit permission;
their inclusion does not imply they are MIT licensed. Other personal backups,
local project JSON, runtime folders, and third-party manuals/screenshots are
not published.

Project code is MIT licensed; see [LICENSE](LICENSE). Third-party components and
data retain their own notices and terms; see [data sources](docs/DATA-SOURCES.md)
and the bundled license files.
