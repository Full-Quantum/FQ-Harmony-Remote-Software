# Remote object exports

On the USB tab, select **Export objects from backup**, choose a saved `.EZHex`
file, then choose a destination folder. The app creates a new timestamped
folder with an `index.json` and one JSON file per decoded device and activity.
This operation does not communicate with or write to the remote.

Device files contain the inferred source IR group and recovered record timings.
Activity files contain decoded state steps, key-map entries, and reachable raw
action lists. Their IDs and the index are tied to the source backup's SHA-256;
candidate references may be empty or ambiguous. The full `.EZHex` backup is
the complete source snapshot and should always be retained separately.

The object format identifier is `fq-harmony-object`, schema version 1. These
are partial source archives, not configurations that can be copied to another
remote today: identity, names, input/role semantics and button mappings remain
incomplete, only the first IR timing pointer group is decoded, and encoding
and cross-remote remapping are not implemented. Both the index and individual
objects mark `transfer_ready` as false. Exported objects may contain personal
configuration data; do not publish them without reviewing their contents.

## Editable local project objects

The Devices and Activities editors also support **Export ... object** from an
open object's settings and **Import ... object** from the corresponding tab.
These `fq-harmony-project-object` packages contain the app's local project
representation. An activity package includes its referenced device records;
import gives the activity and copied devices new IDs and updates the activity's
references. It does not merge dependencies with similarly named devices.

This is for editing and moving individual items between local project files.
It is not the raw object format above and does not make an item upload-ready.
In particular, devices decoded from a remote may contain placeholder command
descriptions rather than transferable IR signals. Keep the original `.EZHex`
backup as the complete source, and review imported objects before using them.
Cross-remote encoding, device matching, and upload remain unsupported.
