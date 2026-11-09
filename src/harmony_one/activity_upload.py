"""Experimental activity addition using the attributed, local MIT composer."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harmony_one.activities import read_activities, read_devices, read_state_records
from harmony_one.config_image import validate_image
from harmony_one.config_reader import ConfigError, parse_config
from harmony_one.flash_writer import ERASE_BLOCK
from harmony_one.ir_records import read_ir_groups
from harmony_one.key_bindings import OP_CALL, read_action_lists, read_key_maps


@dataclass(frozen=True)
class ActivityCandidate:
    baseline: bytes
    image: bytes
    label: str
    source_activity: str
    report: str
    path: Path


def find_node_runtime() -> str | None:
    """Prefer the optional runtime supplied alongside the portable project."""
    if getattr(sys, "frozen", False):
        project = Path(sys.executable).resolve().parent.parent
    else:
        project = Path(__file__).resolve().parents[2]
    bundled = project / "runtime" / "node" / "node.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("node")


def validate_activity_addition(before: bytes, after: bytes) -> None:
    """Independent preservation checks, not proof of hardware behavior."""
    validate_image(before)
    validate_image(after)
    old, new = parse_config(before), parse_config(after)
    if (old.architecture, old.skin, old.base_address) != (
        new.architecture, new.skin, new.base_address
    ):
        raise ConfigError("Candidate changes the remote identity")
    if not len(before) < len(after) <= ((len(before) + ERASE_BLOCK - 1) // ERASE_BLOCK) * ERASE_BLOCK:
        raise ConfigError("Activity addition must grow within the existing final erase block")
    if read_devices(before, old) != read_devices(after, new):
        raise ConfigError("Candidate changes existing devices")
    if read_ir_groups(before, old) != read_ir_groups(after, new):
        raise ConfigError("Candidate changes existing decoded infrared signals")
    old_lists, new_lists = read_action_lists(before, old), read_action_lists(after, new)
    if new_lists[:len(old_lists)] != old_lists:
        raise ConfigError("Candidate changes existing action lists")
    old_keys, new_keys = read_key_maps(before, old), read_key_maps(after, new)
    if len(new_keys) != len(old_keys) + 1 or new_keys[:len(old_keys)] != old_keys:
        raise ConfigError("Candidate does not preserve existing key maps")
    old_activities, new_activities = read_activities(before, old), read_activities(after, new)
    if len(new_activities) != len(old_activities) + 1 or any(
        activity not in new_activities for activity in old_activities
    ):
        raise ConfigError("Candidate does not preserve existing activities")
    old_states, new_states = read_state_records(before, old), read_state_records(after, new)
    for device in read_devices(before, old):
        for variable in device.variables.values():
            if old_states.get(variable) != new_states.get(variable):
                raise ConfigError(f"Candidate changes {device.label}'s state transitions")


def prepare_activity_addition(
    baseline: bytes, label: str, source_activity: str, directory: Path
) -> ActivityCandidate:
    validate_image(baseline)
    if not label.strip() or len(label) > 20 or not label.isascii() or not label.isprintable():
        raise ConfigError("Use a printable ASCII activity name of 1 to 20 characters")
    node = find_node_runtime()
    if node is None:
        raise ConfigError("Activity composition requires a locally installed Node.js runtime")
    config = parse_config(baseline)
    activities = read_activities(baseline, config)
    if any(activity.name.casefold() == label.casefold() for activity in activities):
        raise ConfigError("An activity with that name already exists")
    sources = [activity for activity in activities if activity.name == source_activity]
    if len(sources) != 1:
        raise ConfigError("Select one unambiguous activity from the freshly read backup")
    source = sources[0]
    devices = {device.label: device for device in read_devices(baseline, config)}
    targets = []
    for step in source.steps:
        if step.kind in ("power", "input"):
            prop = "Power" if step.kind == "power" else "Input"
            variable = devices[step.device].variables[prop]
            targets.append(f"{variable}={step.value}")
    if not targets:
        raise ConfigError("The selected activity has no decoded device state targets")
    bindings = read_key_maps(baseline, config)[source.key_map].bindings
    # Scan 55 navigates to the old screen; the composer wires its own navigation.
    keys = [
        f"{binding.scan_code}:{binding.operand}" for binding in bindings
        if binding.is_press and binding.opcode == OP_CALL and binding.scan_code != 55
    ]
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(baseline).hexdigest()
    source_path = directory / f"source-{digest}.EZHex"
    if source_path.exists() and source_path.read_bytes() != baseline:
        raise ConfigError("Candidate source file does not match its recorded digest")
    if not source_path.exists():
        source_path.write_bytes(baseline)
    output = directory / "candidate.EZHex"
    if output.exists():
        raise ConfigError("Candidate destination already exists; use a new operation directory")
    vendor = Path(__file__).parent / "vendor" / "activity"
    # Stage on the local temporary drive: repeated font reads on removable/project
    # storage can otherwise consume the subprocess deadline without doing composition.
    with tempfile.TemporaryDirectory(prefix="harmony-activity-") as temporary:
        staging = Path(temporary)
        shutil.copytree(vendor, staging / "codec")
        staged_source, staged_output = staging / "source.EZHex", staging / "candidate.EZHex"
        staged_source.write_bytes(baseline)
        composer = staging / "codec" / "bin" / "compose-activity.mjs"
        try:
            result = subprocess.run(
                [node, str(composer), "--in", str(staged_source), "--out", str(staged_output),
                 "--label", label, "--targets", ",".join(targets), "--keys", ",".join(keys),
                 "--source-activity-id", str(source.number), "--source-set", str(source.key_map)],
                capture_output=True, text=True, timeout=300, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as error:
            raise ConfigError("Local activity composition exceeded five minutes; no upload occurred") from error
        if result.returncode != 0:
            raise ConfigError(f"Activity composer failed:\n{result.stderr or result.stdout}")
        if "all four hops read back" not in result.stdout:
            raise ConfigError("Composer did not report its structural checks")
        if "return verified" not in result.stdout:
            raise ConfigError("Composer did not verify template screen navigation")
        image = staged_output.read_bytes()
    validate_activity_addition(baseline, image)
    added = [
        activity for activity in read_activities(image, parse_config(image))
        if activity.key_map == len(read_key_maps(baseline, config))
    ]
    expected = [step for step in source.steps if step.kind in ("power", "input")]
    if len(added) != 1 or added[0].steps != expected:
        raise ConfigError("New activity's device targets differ from the selected template")
    output.write_bytes(image)
    report = (
        f"New menu activity: {label}\nTemplate: {source_activity}\n"
        "Copies device power/input targets and hard-key bindings only.\n"
        "Does NOT copy extra startup sends/delays or build custom touch-screen rows.\n"
        "Reuses the template's startup picture/title and post-start working screen/navigation,\n"
        "including any commands that screen already binds to the template's devices.\n"
        "Existing devices, activities, key maps and action lists passed preservation checks.\n"
        "Experimental: hardware behavior is not established by these checks.\n\n"
        + result.stdout
    )
    (directory / "report.txt").write_text(report, encoding="utf-8")
    return ActivityCandidate(baseline, image, label, source_activity, report, output)
