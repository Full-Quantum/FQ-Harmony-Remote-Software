# UI reference (from owner photos in `docs/reference/`)

Observations only; nothing here is copied from Logitech software.

## Desktop layout

The app's high-level device/activity lists and sequence editor were informed
by owner-provided PC-client references. Those third-party screenshots and the
manual are not distributed here. Activity sequences remain distinct groups
of device commands rather than only a flat list of steps.

The current desktop UI follows that structure with a teal product header,
tabbed navigation, two-column device/activity cards, contextual card actions,
and separate settings windows. Activity settings present sequence, device, and
command columns. This is a visual and workflow reference only; unsupported
functions such as live IR learning and per-activity button customization are
identified in the UI rather than presented as working features.

## Custom display-style maker

Where both menu types are presented together, Activities comes before Devices:
left-to-right in the main tabs, custom-style tabs, style previews and object
export columns, and top-to-bottom in the backup browser. The Home preview
selector also lists Activities first, matching the remote's left/right order.

**Display Style** contains **Apply styles** and **Create custom style**.
The maker has independent **Activities** and **Devices** pages, each with
background colour pickers, a solid/gradient/image selector, gradient direction,
PNG/GIF upload, button fill, rounded/sharp corners, and an optional border
with colour and 0-24 pixel thickness. The preview renders the actual 176 x 220
artwork; sample labels do not change the remote text. Colours are RGB565.
Image imports preserve aspect ratio, fill padding/transparency with the first
colour, and use the existing 2 MiB / 1024 x 1024 source limits.

Font, text colour, boldness, and size selectors are disabled and show
**Unchanged**, because the remote's shared bitmap fonts are preserved.
Save a uniquely named design to the existing custom-style library beside the
backup/icon folders. The saved artwork embeds imported pixels and does not
depend on the original image, but the editable maker settings are not saved.
The new style is selected on **Apply styles**, with any previous candidate
cleared. Generating/saving a backup candidate and guarded USB apply remain
separate actions. Applying any display style uses the existing generic row
icon replacement behavior; hardware appearance is not yet validated.

The Transfer page has **Open backup folder** immediately beside **Back up
destination and check compatibility**. It creates the actual backup directory
if needed, opens it in Explorer, and reports opening failures.

## Three physical remotes
The owner provided three Harmony One remotes with distinct screen styles. The
style numbering follows newest-to-oldest:

| Style | Remote generation | Activities list artwork | Devices list artwork |
|---|---|---|---|
| 1 | Newest | Background picture 46; row pictures 18 / 71 / 22 (the final row on the last page uses 18) | Clean black background; row pictures 15 / 2 / 65 |
| 2 | Older | Background picture 65; row pictures 18 / 85 / 8 | Background picture 5; row pictures 82 / 20 / 55 |
| 3 | Oldest | Background picture 52; row pictures 61 / 42 / 61 | Background picture 72; row pictures 62 / 31 / 63 |

The picture numbers above identify remote-bank assets, not label strings. Style
transfer changes these full-screen backgrounds and row-button pictures and uses
one neutral generic list icon on every recognized Activities/Devices row. Row
labels remain attached to their existing rows; per-item icons on the target
remote are replaced by this generic icon.

On **Backups & Transfer → Browse Contents**, selecting a decoded activity or
device enables an individual menu-icon editor. It lists each recognized
menu-page occurrence and offers 51 x 48 images already in that backup or an
imported PNG/GIF. Each edit affects just the chosen row and preserves the
backup's current background and row-button artwork. Save the validated edit as
a separate `.EZHex` candidate before choosing it in the Transfer workflow.
This image-reference edit has not been visually confirmed on hardware.

The **Icons** tab's **Scan remote and save menu icons** action reads a connected
remote after the user confirms USB and charge readiness, saves the full
validated `.EZHex` backup, and exports each distinct 51 x 48 activity/device
menu icon as a PNG to the `icons` folder beside the `backups` folder. PNG/GIF
images users add to this folder are loaded into the **Change icon** picker on
its next open.
Use **Open icons folder** on the Icons tab to open that folder in Explorer. The
scan action does not write to the remote.

The Home page keeps the **Back up connected Harmony One Remote and start
editing a copy...** and **Open backup folder** actions beside a preview of the
last scanned or loaded backup. Choose Activities or Devices and a page to see
the menu artwork, icons, labels, order, and detected style. The preview is
read-only and appears after a USB scan or when a backup is loaded. Device menus
can be stored under multiple internal screen modes; the preview shows the mode
covering the most decoded devices as one consistent paginated list, rather than
repeating each mode as another device list. Icon scanning
and the icon folder shortcut are on the Icons tab beside Display Style. The
Features and Memory Map tabs are not shown.

The **Slideshow** tab starts with six slots: four reference images and two empty
placeholders. The images are the charging battery screen and three blurred
colored-light images extracted in playback order from
the oldest available copy of the newer-profile remote backup. These defaults
are copied into the shared `slideshow` folder beside `backups` and `icons`
without overwriting user files. Each detected slot in a loaded backup has its
own **Browse...** button; selection is disabled until a backup is loaded.
Supported six-image cycles enable all six slots. Slots beyond the detected cycle
remain empty and disabled, with no preview or export; they do not add blank
screens to playback or extend a four-image backup's cycle.
Every row has a whole-second time field and **Set** button, enabled only for
supported active slots. All six default fields show 3 seconds. Loading a backup
retains its actual timings rather than silently changing the candidate.
**Return to default slideshow** confirms restoring the four original bundled
images at 3 seconds each, independent of user-edited library files. For a known
six-image cycle it bypasses the extra screens and retains the terminal screen's
final action, leaving slots 5 and 6 empty/disabled. Stored unused pictures are
not deleted. Shared/overlapping timers and timers referenced by unrelated
actions are rejected. Failed resets are atomic; the candidate is unchanged.
**Discard unwritten changes** returns to the loaded cycle, images, and timings.
Replacing an image changes only the in-memory backup candidate, and the
existing explicit write acknowledgement remains required to send it to the
remote. **Open slideshow folder** opens the local image library.

The **Remote Assistant** tab displays the loaded backup's saved prompt setting.
**Turn prompts on...** and **Turn prompts off...** save a separate `.EZHex`
candidate, never overwriting the original. **Apply saved change to remote...**
opens the existing Backups & Transfer workflow with the candidate selected.
The destination must match the edit's original backup both when read and again
immediately before writing. Charged/USB readiness and explicit replacement
acknowledgements remain required. These controls retain the assistant screens,
Options entry, Help, devices, activities, and style. Backups without a unique
recognized on/off toggle show an unsupported explanation with disabled controls;
this is not an assistant installation/removal tool. The saved setting is not a
live query, and assistant-specific screen behavior remains hardware-unverified.

The Icons tab initializes the shared `icons` folder with the menu images found
in the existing bundled backups, copying only files that are not already
present. PNG/GIF files added by the user are picked up by each newly opened
Change icon picker.
The bundled library now retains 101 unique non-fullscreen images from the local
backup collection: 15 menu-sized icons plus 86 other system/button images in
`icons/button_assets`. The latter retain their original sizes and transparency
and are intentionally excluded from the menu-icon picker. First-run seeding
copies this subfolder too, without overwriting user versions.

The **Devices** and **Activities** cards also provide **Change icon** beside
**Troubleshoot**. Their pickers have no **Menu Button** dropdown and apply the
chosen icon to all matching menu rows. Backup images, user-library icons, and
PNG/GIF imports can be chosen. If an activity's decoded project name
does not match a menu label, a list of actual activity rows from the loaded
backup is shown so the user can choose the intended row. Load the matching
backup first; changes remain an offline candidate until saved as a new
`.EZHex`. The Browse Contents editor still allows editing one occurrence at a
time. Both are separate from the local project preview icons in Activity
Settings.

After loading a backup, **Import all decoded items to Remote Data...** creates
a separate editable project copy from that backup and opens the Devices tab.
The Home action **Back up connected remote and start editing a copy...** first
downloads and saves the complete original `.EZHex`, then imports its decoded
devices and activities. Save local project changes as Remote Data JSON; general
device/activity edits are not compiled into an `.EZHex` configuration.

Ignoring labels and icons, Style 2 is clearly distinct: its Activities buttons
have cyan outlines with dark interiors over a blue-teal panel, while its Devices
buttons have orange outlines over a dark brown panel. Styles 1 and 3 share black
list backgrounds and the same Devices buttons; their Activities buttons also
match except for the first-page bottom button (85 of 8,200 pixels differ), and
Style 1 uses the Style 3 button in that row on its final page. Thus all three
measured styles are still distinct, though Styles 1 and 3 are very similar
outside that single button detail.

The desktop app's **Display Style** selector includes **Newer** (Style 1) and
**Older** (Style 2). Style 3 (oldest) is no longer in the default list. To add
it or create another custom style, select **Use custom backup style...** and
then **Save custom style...**; the named artwork is stored in the user's local
custom-style library and can be reused in future sessions. Custom styles are
stored as `custom_screen_styles.json` beside the `backups` folder.
A generated candidate can be written to a
connected remote only after explicit acknowledgement and confirmation, with a
fresh exact-match backup and full read-back verification. Hardware validation of
style writes remains pending.
Use **Preview all styles...** beside the selector to compare sample Activities
and Devices pages rendered from the saved backgrounds, buttons, and generic
icon. Previews show first-page button artwork; Newer Activities uses a different
bottom-row button on its final page.

## Clock

The **Clock** tab previews the current computer-local time by default and lets
the user select or type an IANA timezone. It offers an acknowledgement-gated
clock update; each listed timezone includes its current UTC offset. When a
backup has been loaded, it can also save a separate offline clock candidate
from that exact file without USB. The live clock-write action takes a fresh
backup, updates the configuration's clock
timestamp and first seven state records, verifies the live baseline before writing,
and reads the whole configuration back before restart. The remote does not store
the selected timezone or daylight-saving rule. Clock-specific hardware behavior and accuracy
remain unverified.

Picture numbers are indexes in each backup's picture bank, not fixed image IDs.
The newest backup's Devices background was accidentally replaced with the
Rotom slideshow test image. The bundled Style 1 preset uses the solid-black
Devices background from an earlier clean copy of that remote; its row artwork
remains from the newest baseline. Other Rotom drawings on non-list
screens are not treated as list artwork unless they meet the list-page text
and geometry checks. The development baselines contain 4 devices / 3 activities
(Style 3), 24 device list pages / 3 activity pages (Style 2), and 10 device list
pages / 3 activity pages (Style 1).

## Touch screen layout (both styles)
- Header: page title ("My Activities" / "Devices"), "n/m pages", day and clock, battery icon.
- Three large rows per page, each with an icon and a name.
- Left and right arrows on the screen edges change page.
- Two soft buttons under the LCD map to two bottom labels.
  - Activities page: left "Options", right "Devices".
  - Devices page: left "Activities", right unused.
- Row icons: activities use a person/webcam icon; devices use a type icon (TV, AV receiver, disc player).
- Capacity: pages x 3 rows, so ordering (R3) is a list of rows grouped three per page.
- Photo clocks read different days/times on the two remotes, so the clock (R4) is worth testing on both.

## Physical buttons
Transcribed to `src/harmony_one/data/physical_buttons.json`: Off, Activities, Help,
Menu/Exit/Info/Guide, page rocker, D-pad + OK, Vol/Ch, Mute, Back, 8 transport keys,
number pad with clear and enter, plus two soft buttons and two touch arrows.

## Display-style transfer
The style transfer identifies Activities and Devices list pages from their decoded
item names and drawing geometry, then applies the selected backgrounds, row-button
images (including the observed empty-row variant), and a generic icon on each
recognized list row. Device/activity names, order, infrared records, and behavior
are preserved. Fonts and other screens are not part of style transfer. It creates
a separate offline `.EZHex` candidate and does not update the editable Remote Data
JSON. For per-row artwork, use the separate **Browse Contents** icon editor
described above. Ambiguous or unsupported screen-program references are refused
rather than guessed. The candidate must be reviewed before any future hardware test.

## Options menu (2 pages, 3 rows per page)
| Style 2 (older), `style-a-options-*.jpg` | Style 1 (newer), `style-b-options-*.jpg` |
|---|---|
| p1: Tutorial, Slideshow, Date and Time | p1: Turn Remote Assistant Off, Tutorial, Date and Time |
| p2: Turn Screen Sounds Off, Screen Sensitivity | p2: Turn Screen Sounds Off, Screen Sensitivity |

Style A has **Slideshow** but no Remote Assistant; Style B has **Remote Assistant** but no
Slideshow entry. Goal: one build exposing all options on either style.
Brightness was not seen here (owner recalls it as an on-board setting; location unknown).

## Device button page (`style-a-device-buttons-tv.jpg`, `style-b-device-buttons-samsung-tv.jpg`)
- Header: device name, "1/6 pages", day/clock, battery.
- **2 columns x 3 rows = 6 buttons per page**, 6 pages in the examples.
- Labels are the command names: Style A TV page 1 = PwrToggle, InputHdmi1, InputHdmi2, Home, Tools, List.
  Style B Samsung TV page 1 = Home, Power Toggle, PowerOn, PowerOff, A, B.
- Both soft buttons are lit orange; bottom labels are "Activities" (left) and "Devices" (right).
- Physical-button mappings (R2) and these touch buttons are separate lists per device.

## Owner's slide show (Style A remote, uploaded images)
Eggman with moustache, Ralsei with blunt, Sonic and Shadow outside the White House,
Rotom (Pokemon), graffiti text "Full_Quantum Was Here". Behavior: on the charger the
remote shows the charging battery image first, then plays the custom images.
Needs the old software and USB to extract the stored images and their format.

## Still not shown
Brightness setting location, screen-sensitivity and screen-sound screens, Date and Time screen,
per-activity button layouts.
