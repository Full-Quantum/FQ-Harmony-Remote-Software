# Features

The machine-readable feature catalog is in `src/harmony_one/data/features.json`.

| Feature | Status | Source |
|---|---|---|
| Local project editing (devices/activities JSON) | Working | Prior own code |
| Bundled IR device library (82 profiles) | Partial; opt-in via "Add from IR library..."; new projects start blank | Flipper-IRDB, CC0 |
| Flipper `.ir` import | Partial | Prior own code |
| Harmony One configuration backup over USB | Read-only | Public protocol docs |
| Separate device/activity object export | Partial; individual objects selectable, one-click fresh-backup export; archival data only, not upload-ready | Own decoders |
| Backup browser tab (view devices/activities inside a backup, read-only) | Working; decoded names, IR summaries, steps and bindings; shares export workflow | Own decoders |
| Import/export one editable project object at a time | Partial; activity dependencies are copied and IDs remapped; not upload-ready | Own project format |
| Backup explorer (`tools/inspect_backup.py`) | Dev tool | Own analysis |
| Read devices, IR records, activity names and state steps from a backup | Partial | Public docs + own backups |
| Map device buttons to physical/touch-screen buttons | Planned | R1 |
| Local activity setup (devices, ordered actions, optional control roles and input notes) | Partial; local project only, input notes do not send commands | Manual analysis + own project model |
| Activity icons | Partial; built-in and custom 32 x 32 local previews only; remote icons are not decoded or writable | Local project format |
| Fixed-size slideshow and same-length activity-name writes | Verified on spare | USB writer; owner-confirmed hardware results |
| General project-to-remote configuration generation | Not implemented | Requires an encoder and relocation verification |
| Add activity using existing remote devices | Experimental; Test TV upload/read-back and corrected device-screen navigation owner-confirmed; startup title reused | Approved attributed MIT composer + independent preservation checks |

See [REMOTE-WRITES.md](REMOTE-WRITES.md) for the tested write sequence, evidence,
and current safety boundaries.
