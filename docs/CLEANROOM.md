# Clean-room policy

Goal: a new, independently written application with its own MIT license.

1. **Specification from the user.** Features come from the owner's description
   of how the original software behaved (see REQUIREMENTS.md).
2. **No Logitech code.** Do not copy, decompile-and-port, or paste code, UI
   assets, strings, or data files from Logitech's installers into this repo.
   The installers are not stored here.
3. **Permitted technology assessment.** Identifying which technologies the
   original used is allowed. Recorded finding: the 7.8.0 installer ships a Java
   desktop shell with an embedded Mozilla/Gecko browser for its UI and Java
   HID/USB libraries. This project instead uses Python, Tkinter and `hidapi`.
4. **USB protocol knowledge** comes from public documentation of
   independent projects (see DATA-SOURCES.md), implemented independently, and
   from behaviour the user observes on their own remote.
5. **Prior work** in `offline-harmony` is the owner's own code and was migrated here.
6. **Personal data.** Backups and Remote Data files stay private unless the
   owner explicitly approves publishing a particular file. The public example
   in `examples/primary-remote` is an owner-approved exception.
7. **Approved independent codec integration.** The owner approved using the
   MIT-licensed harmony-explorations activity composer with attribution on
   2026-11-06. The experimental activity-addition path bundles that independent
   code and its open font dependency; it is not an independently rewritten
   encoder. No Logitech source or catalogue archive is included. Other original
   application modules remain independently implemented.

Every new feature should note its source in FEATURES.md: user description,
public documentation, or observed device behaviour.
