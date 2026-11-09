# Contributing

Contributions are welcome. Please keep the project usable offline and describe
the evidence and limitations behind changes, especially changes to remote
configuration formats.

## Develop and test

Use Python 3.10 or later on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
.\.venv\Scripts\python -m harmony_one
.\.venv\Scripts\python -m unittest discover -s tests -v
```

Tests that require private backup fixtures may be skipped. The experimental
activity composer also requires a local Node.js installation. See the
[README](README.md) for building the Windows executable.

## Safety and provenance

- Read [REMOTE-WRITES.md](docs/REMOTE-WRITES.md) before touching USB write
  behavior. Use simulated flash tests first; hardware writes can make a remote
  unusable. Never assume another person's backup matches a connected remote.
- Do not submit personal backups, configuration exports, or other sensitive
  data without the owner's explicit permission. Check files before sharing.
- Follow [CLEANROOM.md](docs/CLEANROOM.md) and document the origin and license
  of new code, data, or assets. Do not copy Logitech source or installer assets.
- Describe what was tested and what remains unverified. Keep existing
  compatibility and third-party license notices intact.
