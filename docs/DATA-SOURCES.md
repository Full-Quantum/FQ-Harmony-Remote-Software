# Data sources

## Owner-approved remote presets and artwork

For v0.3.0 the owner explicitly approved public distribution of both bundled
`FQ Preset older.EZHex` and `FQ Preset newer.EZHex` configurations and the
extracted default artwork. The presets are full remote configurations, not
blank profiles or proof of compatibility with another remote.

`data/default_slideshow` contains the charging battery screen and three
blurred-light images. `data/default_icons` contains 15 menu-sized icons, with
86 additional system/button images in `button_assets`. `data/screen_styles.json`
contains validated display-style artwork extracted from the owner's backups.
The owner's redistribution approval does not relicense these assets as MIT.
Other private backups, project exports, manuals, and original PC-client
screenshots are not included.

## Flipper-IRDB curated device profiles

- Repository: <https://github.com/Lucaslhm/Flipper-IRDB>
- License statement: the repository [README](https://github.com/Lucaslhm/Flipper-IRDB#readme)
  says submissions are CC0-1.0, and explicitly excludes commits before
  [`2319685f2cbf0cd3f809609622cade14d24fb819`](https://github.com/Lucaslhm/Flipper-IRDB/commit/2319685f2cbf0cd3f809609622cade14d24fb819)
  (2025-08-07).
- Pinned source snapshot:
  [`d126fb1b6f1e114c52b4a8c19839ea65e3a9c24d`](https://github.com/Lucaslhm/Flipper-IRDB/commit/d126fb1b6f1e114c52b4a8c19839ea65e3a9c24d)
  (2026-04-12).
- Selection: 124 `.ir` profiles reported as newly added when comparing the
  snapshot against the license-cutoff commit. Of those, 82 profiles (1,956
  commands) contain only supported parsed signals and valid unique button
  names. The remaining 42 were excluded because they contained raw signals or
  failed project validation; the app does not discard or rename individual
  commands to force them through.
- The resulting parsed data is bundled in `data/catalogs.json`. Each device
  entry records its repository path and a source URL pinned to the snapshot.

This is a curated cross-category starter collection, not a complete database
and not a claim that a code set matches every model listed. Verify each code
against the actual device. Command values and names are preserved from the
selected parsed entries.

## Other sources evaluated

- [probonopd/irdb](https://github.com/probonopd/irdb) is a much larger,
  manufacturer-independent protocol/device/subdevice/function code database.
  Its [custom license](https://github.com/probonopd/irdb/blob/master/LICENSE.md)
  requires notifying the project before product use, including its notice, and
  providing up to three copies of the product on request. Those terms have not
  been acted on, so this database is not bundled.
- [IRremoteESP8266](https://github.com/crankyoldgit/IRremoteESP8266) is an
  LGPL-2.1-licensed protocol implementation with a strong focus on air
  conditioners. It is not a general manufacturer/model remote-code catalog in
  the format currently supported by this app, so its code has not been copied
  or bundled.
- The currently approved Flipper-IRDB branch still points to the pinned
  `d126fb1b6f1e114c52b4a8c19839ea65e3a9c24d` snapshot; no newer additions were
  available there during this review.

## Harmony USB programming research

The application does not include or copy Concordance code. Its public
documentation was consulted to understand the practical USB-transfer boundary:

- Project: <https://github.com/jaymzh/concordance>
- License: GNU GPL-3.0, as declared by its
  [license file](https://github.com/jaymzh/concordance/blob/main/LICENSE).
- The project lists Harmony One configuration update as working when supplied
  with a configuration blob. Its README says Logitech's service that generated
  the required configuration blobs shut down in May 2025 and that it can still
  back up and restore existing configurations, but cannot generate edited
  activities/devices itself.
- Model support list:
  <https://github.com/jaymzh/concordance/blob/main/SupportedModels.md>

This is evidence about Concordance's documented behavior, not runtime testing
of a Harmony One. It establishes the difference between restoring an existing
remote-specific blob and generating edited device/activity configurations.

## Read-only Harmony One configuration backup

The backup implementation was independently specified from the public
MIT-licensed [`harmony-explorations`](https://github.com/dannybloe/harmony-explorations)
research project, particularly its
[USB protocol](https://github.com/dannybloe/harmony-explorations/blob/main/docs/usb-protocol.md),
[flash reader](https://github.com/dannybloe/harmony-explorations/blob/main/packages/usb/src/remote.ts),
[configuration reader](https://github.com/dannybloe/harmony-explorations/blob/main/packages/corpus/src/read.ts),
and [GSPM container codec](https://github.com/dannybloe/harmony-explorations/blob/main/packages/codec/src/gspm.ts).
The backup implementation does not copy upstream source. It sends only the `READ_FLASH`
command to a device with the Harmony One VID/PID, checks response sequencing,
and validates the `GSPM` header, `PTYY` end marker, and trailer checksum before
it saves the remote bytes as a bare-container `.EZHex` file. The connected
remote is the user's Harmony One; the backup action is read-only and is not an
upload operation.

## Experimental activity composer

The owner approved integrating independent MIT code with attribution. The
standalone activity composer under `src/harmony_one/vendor/activity` is bundled
from `dannybloe/harmony-explorations` commit
`c016f5b51866bb989e8815c081fb45e1e72d2e95`, using esbuild 0.25.12:

```
Get-Content -Raw -LiteralPath "<project>\tools\activity-composer-entry.ts" | npx --yes esbuild@0.25.12 --loader=ts --sourcefile=activity-entry.ts --bundle --platform=node --format=esm --outfile=compose-activity.mjs
```

The distributed layout places the bundle in `bin` and the upstream Oxanium
fonts in sibling `fonts`, preserving their relative resource lookup. MIT and
dependency notices accompany the bundle; Oxanium's OFL accompanies its fonts.
It runs locally using Node.js, reads a saved backup and writes a candidate.
No Logitech IR archive is used, and personal backups are not transmitted.
This exception does not change the independent provenance of the USB backup
implementation. New device composition is not included.
Run the build command from the pinned upstream checkout; the wrapper's imports
are relative to that checkout, not to this project's tools directory. The
wrapper selects the upstream template name through the activity number and
key-map identity, because a decoded startup title may differ from a menu label.
