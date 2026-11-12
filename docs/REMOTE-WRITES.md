# Writing a configuration to a Harmony One

## What has been verified

The USB flash writer has been tested on the owner's spare Style A Harmony One
(firmware 3.2, hardware 0.5, architecture 12, skin 54). The following edits have
been written and read back successfully:

- A same-length activity-name change, `Watch TV` to `Watch VT`. The owner
  confirmed `Watch VT` appeared in the activity menu.
- A same-size slideshow picture replacement, which the owner confirmed displayed
  correctly on the remote.

The experimental `Test DVD` activity addition was also programmed to the same
spare after a fresh matching backup. The 1,475,067-byte configuration grew by
470 bytes; all 23 erase blocks (1,507,328 bytes including the preserved tail)
passed byte-for-byte read-back, and reset was sent. This is structural USB-write
evidence. The owner subsequently confirmed `Test DVD` appears and both original
activities remain. Infrared operation has not been tested: the spare's configured
equipment is not available for the owner to test.

A fresh USB backup taken on 2026-11-03 decoded the spare's activities as
`Watch a DVD` and `Watch VT`. That backup is kept locally as
`backups/HarmonyOne-SPARE-after-rename-20261103.EZHex` and is git-ignored.
These results establish the write path for this tested One; they do not prove
that a structurally different configuration, another firmware, or another
Harmony model is safe to write.

The Slideshow page now also prepares per-image whole-second timer edits and
**Return to default slideshow** candidates. Reset restores the four bundled
images at 3 seconds each; a recognized six-image cycle is shortened to four by
changing timer transitions while retaining its terminal screen's existing final
action. No records are relocated or deleted. Timer records must be isolated
and uniquely referenced, and tests verify that only the requested picture/timer
bytes and checksum change. These new timing and cycle-shortening behaviors
are offline-validated, not hardware-verified. They use the existing exact-live-
baseline, acknowledgement, confirmation, and read-back write gates.

## Write safety checks

The write tool requires `HARMONY_ENABLE_WRITES=1`. Before it erases anything,
`write_configuration` reads the live configuration and requires an exact match
with the supplied current backup. It also reads and preserves bytes after the
configuration through the end of the last erase block. A mismatch or missing
write opt-in aborts before the erase/write sequence.

The intended workflow is:

1. Connect exactly one Harmony One over USB and take a fresh, validated backup.
2. Make an edit to a copy of that backup; keep the original untouched.
3. Parse and validate the edited container, and inspect the change before writing.
4. With the remote still connected, enable writes and supply both the edited
   configuration and the exact backup from step 1.
5. Keep the remote connected until the tool reports successful read-back
   verification and restarts it. The verified real write took about three minutes.
6. Read a fresh backup after restart, compare it with the intended file, and
   inspect the result on the remote.

The command-line rehearsal tool accepts the original backup and optional new
configuration:

```powershell
$env:PYTHONPATH = "src"
$env:HARMONY_ENABLE_WRITES = "1"
python tools\rehearse_write.py backups\CURRENT.EZHex backups\EDITED.EZHex
```

With one path it writes that backup back unchanged. The second path, when
provided, is the new image; the first remains the required live-state baseline.
This tool writes to hardware. Do not run it unless the target is the intended
Harmony One, a fresh backup exists, and the change has been inspected.

## Editing one Activities/Devices menu icon

The **Browse Contents** page can prepare an offline `.EZHex` candidate with one
recognized row icon changed. Select a decoded activity or device, choose the
specific mode/page/row occurrence, then use an existing 51 x 48 image from
that backup or import a PNG/GIF. The candidate changes only that row's picture
reference, appends an encoded picture when needed, recalculates the container
checksum, and verifies that all other recognized icons, menu backgrounds,
row-button artwork, and decoded device/activity data remain unchanged.

Save the candidate as a new `.EZHex` file and select it in the **Transfer**
workflow to apply it. The imported icon is previewed over the selected row's
existing button art. This proves the offline image and reference checks only;
the remote's visual rendering of custom or reassigned icons has not been
confirmed on hardware.

## USB sequence implemented

### Remote Assistant prompt setting

The Remote Assistant page prepares an on/off candidate, not an installation or
removal of the assistant's screens. It recognizes the paired Options actions
(click, write the same state variable to 1 or 3, redraw), requires an isolated
four-value state record with no transition handlers, and changes only its
saved value plus the container checksum. Values 0 through 2 mean prompts are
enabled; 3 disables them. Enabling a previously disabled setting uses 1.
Already-enabled substates are left intact for a no-op request.

Candidate preservation is tested against captured backups and the bundled older
preset. The bundled newer preset currently has no recognized paired toggle and
is explicitly unsupported rather than patched speculatively. The page saves a
new file and stages it in Backups & Transfer. That staged edit additionally
requires the destination to match the original backup during the destination
read and immediately before writing, on top of the existing write/read-back
guards. Manually selecting another source clears that edit-specific restriction.
No assistant-specific hardware test has been performed; on-screen behavior
must still be checked after an explicitly authorized write.

The implementation sends 64-byte HID reports to the Harmony One interface
(`VID_046D`, `PID_C121`). It performs these operations in order:

1. Read the current configuration and compare it byte-for-byte with the supplied
   backup.
2. Send `WRITE_MISC` selector `0x02` to drop cached region descriptors.
3. Erase every 64 KiB flash block occupied by the padded configuration.
4. Program the image in transfers of at most 3,150 bytes. Data is divided into
   packet sizes representable by the HID length nibble.
5. Read the programmed image back and compare every byte, including preserved
   tail bytes in the last erased block.
6. Send reset escape `0xE1 0x02` only after successful verification.

Errors before the erase stage leave flash untouched. A transfer or verification
failure after erasure can leave the remote unusable until recovery; do not
disconnect or remove power during a write. The writer deliberately does not
write firmware.

## Transfer a complete save between two remotes

The **Transfer Remote** tab is for scanning one Harmony One and transferring
its complete raw configuration to a different unit. It is not the editable
Remote Data JSON workflow and does not merge selected devices or activities.
The source `.EZHex` contains a full remote snapshot; restoring it replaces the
destination's existing configuration.

The app bundles two full-save presets: **FQ Preset older** is the destination's
saved pre-transfer configuration, and **FQ Preset newer** is the source save
that was transferred to the remote. They can be selected as the source without
connecting the original source remote. Other `.EZHex` files and a live source
scan are also supported.

The Home transfer action opens a staged workflow:

1. To archive a remote, connect it, confirm it is charged, and choose
   **Download connected remote backup (.EZHex)** on Home or
   **Scan and download connected remote backup (.EZHex)...** on Transfer Remote.
   This reads the complete
   configuration without changing the remote and automatically saves a
   timestamped `.EZHex` file in the app's `backups` folder. It appears in the
   **Saved remote contents** list on the same page. You can stop here; later,
   select that backup from the list and choose **Select for upload**. The
   bundled FQ presets appear in the same list. **Browse for .EZHex...** can
   select a backup stored elsewhere.
2. Connect the Harmony One that should receive the selected backup and read
   its destination backup. If continuing directly after a download, disconnect
   the remote you just backed up first and connect the different destination.
   The app saves the target backup even if compatibility checks subsequently
   refuse the transfer.
3. The app checks architecture, screen skin, and flash capacity. Matching
   architecture and skin are only compatibility filters; the software cannot
   prove physical model identity or firmware compatibility.
4. Acknowledge replacement and confirm the exact write in a separate prompt.
   The app takes and saves another fresh destination backup immediately before
   writing, then requires an exact live match before erase.
5. The app preserves destination flash bytes after the source image through its
   last erase block, reads the full programmed region back, compares every
   byte, and only then resets the destination. Keep it powered and connected.

This is an experimental raw-configuration transfer, not a general
cross-firmware migration. A `.EZHex` save does not provide a verified
firmware-compatibility check; matching architecture and skin are not proof that
the source will operate correctly on a different remote. Cross-remote transfer
has not yet been hardware-validated. Keep the destination backup for recovery,
and inspect the destination after restarting. A failure after erasure can
leave it unusable; recovery is not guaranteed.

## Current edit boundaries

The verified edits are in-place edits that preserve the configuration's length.
The activity rename substituted existing glyph codes for the same number of
characters and refreshed the trailer checksum. The picture replacement likewise
kept the encoded picture size and refreshed that checksum.

This does **not** make arbitrary device/activity project changes uploadable.
Growing or shrinking sections requires relocation: every absolute flash address
that targets moved data must be adjusted, the container end address and checksum
must be restamped, and implicit positions and packed records must remain valid.
The Home **Select a saved backup or transfer to another remote...** action copies a raw existing
save; it does not upload project editor changes because there is no verified
project-to-container encoder. Device and
activity additions, longer labels, command-table changes, and activity step
changes remain unsupported through the general project editor. The separate
experimental activity-addition workflow below is a narrow exception, not a
general project encoder.

## Encoder groundwork

### Experimental GUI activity addition

Activities now offers **Add activity to remote (experimental)** after reading
or importing a backup. It uses `runtime/node/node.exe` if present or Node.js on
the system PATH. The workflow
reads a fresh USB backup, then invokes the attributed independent MIT composer
offline and saves a candidate and report in a new ignored backup directory.
It copies the selected activity's decoded power/input targets and hard-key
bindings, excluding the old screen-navigation binding. It does not copy extra
startup sends/delays or create new touch-screen command rows. The startup
picture/title and working screen are shared with the selected template, so the
template's existing touch-screen commands are reused. The startup screen still
shows the original activity name, not the new menu label.
Template selection is resolved by activity number and key-map identity in the
independent codec, not by assuming our decoded startup title equals its menu
label. Duplicate or missing upstream template labels are refused.

Independent checks require unchanged remote identity, decoded devices and IR,
all existing action lists and key maps, existing activities and device state
transitions, and exactly one added activity/key map. Growth beyond the current
last erase block is refused. The upstream composer additionally checks
coverage, overlaps, checksum, round trip, state-table consistency and queue
limits. These checks do not prove every relocated reference or hardware behavior.

Upload requires an explicit experimental-risk confirmation and uses the
existing exact-live-baseline, preserved-tail, erase/program/read-back/reset
writer. The modal window cannot close during the operation. General local
project uploads and device additions remain blocked. Successful flash read-back
is distinct from confirming the menu and infrared operation on the remote.
The `Test DVD` trial has owner-confirmed menu evidence as described above;
its infrared behavior remains unverified.

On the owner's firmware 3.4 remote, `Test TV` was composed from `FQS1 (PC)`
and uploaded with a verified backup and full flash read-back. The owner
confirmed the new and original menu entries and that Test TV operated like the
template. The initial result showed a mostly blank working screen. A separately
validated screen repair was uploaded after a fresh matching backup; its full
read-back matched, and the owner confirmed Test TV now reaches the correct
device-button panel and the original FQS1 continues to work. The startup
screen still says `FQS1 (PC)`, because it is shared with the template.
Individually tested power, input, volume and mute results were not reported.

For new activity candidates, the composer wrapper now redirects both the
startup completion instruction and the newly owned activity-return screen
program to the template's working mode. It refuses unexpected or shared
return cases and verifies original screen mappings, byte coverage, checksum,
round trip, state consistency and queue limits. The same change was tested on
the existing Test TV entry as described above. These checks do not establish
correct behavior for every configuration.

`src/harmony_one/config_image.py` implements a lossless, fixed-length byte
patch primitive for future encoders. It checks the source envelope, checksum,
expected original bytes, non-overlap, and section bounds, and refreshes the
checksum. An empty patch list returns the original bytes exactly. This is
deliberately not a device/activity encoder: it does not establish that a byte
belongs to the intended object, check all semantics, relocate anything, or
write to USB. Each future supported edit must add its own semantic and
cross-reference checks and tests before its write gate can be opened.
`locate_action_lists` additionally exposes action-list offsets and detects
truncation; shared or overlapping records and references from other structures
must still be accounted for before changing an instruction.

`src/harmony_one/device_encoder.py` can prepare an **offline experimental**
carrier-frequency change to an existing class-1 IR command. It inventories all
IR pointer groups, refuses overlapping IR records and timing blocks, and checks
the decoded IR records after editing. On the spare backup, IR headers occur
before the first section-table target, so the generic section-limited patcher
cannot address them. This narrowly targeted implementation uses the IR record's
self-pointer and bounds checks instead. These checks do not prove all possible
cross-references or that the signal operates the intended equipment. It is not
wired to USB or the project editor and does not create devices, import commands,
or make carrier edits upload-ready.

The project change preview reports device fields, activity setup fields, and
unknown project fields as well as additions, deletions, order, and steps. The
general project-write gate remains closed for all of them.

For a single same-length activity rename, the project write preview can save an
**offline candidate**. It checks the source checksum, refuses ambiguous names
and unexpected decoded text matches, and re-decodes activities after the edit.
It preserves the original length and updates the checksum. These checks do not
cover every screen or prove the candidate is safe to flash; generating it does
not send anything to the remote. Other edits and all project uploads stay
blocked. A single previous same-length rename was separately verified on the
spare, not every possible candidate.

### Experimental Display Style write

The Display Style tab can write a generated style candidate to USB. It transfers
Activities/Devices list backgrounds and row-button artwork and replaces each
recognized row icon with a common generic list icon. Labels, decoded device/
activity data, and order are preserved by the candidate builder. The generic
icon is generated by the app rather than stored in individual style presets.
Keep the original backup if the per-item icons should be restored.
Before enabling the write, the app takes and saves a fresh backup and requires it
to match the loaded target byte-for-byte. It saves the candidate, then uses the
same full erase/program/read-back/reset path. A stale or different loaded backup
is refused before the write begins.

Style writes have not yet been validated on hardware. Successful byte-for-byte
read-back confirms stored configuration data, not the appearance or operation of
the list screens. Keep the fresh baseline and candidate files for recovery and
comparison.

## Evidence and implementation

- `src/harmony_one/flash_writer.py`: write packet encoding, erase/write/read-back
  sequence, opt-in and baseline checks.
- `src/harmony_one/harmony_usb_write.py`: HID transport for write reports.
- `src/harmony_one/screen_style_tab.py`: candidate generation, fresh-baseline
  comparison, acknowledgement-gated style write, and read-back status.
- `src/harmony_one/save_restore_tab.py` and `save_restore.py`: complete-save
  source selection, fresh target backups, compatibility checks, and gated restore.
- `src/harmony_one/config_blocks.py` and `memory_map_tab.py`: read-only visual map
  of header, section ranges, and trailer for a loaded or selected backup.
- `tools/rehearse_write.py`: explicit command-line hardware writer.
- `src/harmony_one/slideshow.py` and `src/harmony_one/rename.py`: fixed-size
  edits that refresh the container checksum.
- `src/harmony_one/slideshow_tab.py`: the app's acknowledgement-gated picture
  and timer write UI, with bundled-default restoration.
- `src/harmony_one/slideshow_cycle.py`: conservative playback-order decoding,
  isolated timer edits, and four-image default-cycle restoration.
- `src/harmony_one/remote_writer.py`: separate project-write gate, still closed
  until configuration generation and relocation are verified.
