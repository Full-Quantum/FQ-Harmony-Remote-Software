"""Read activities and devices from a Harmony One configuration (read-only).

An activity is a value of the CurrentActivityState counter; its key map (base slot 9)
carries the enter/leave/re-pick handlers, and the enter handler sets each device's
state (power, input). A device's infrared group is found from the action lists its
Power/Input variable transitions run (base slot 13). Layouts follow the public
reference (dannybloe/harmony-explorations, MIT).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from harmony_one.config_reader import RemoteConfig, raw_slot_for_base_slot
from harmony_one.fonts import read_font_sets
from harmony_one.key_bindings import (
    Instruction,
    OP_CALL,
    OP_DELAY,
    OP_SEND,
    KeyMap,
    read_action_lists,
    read_key_maps,
)
from harmony_one.screens import read_mode_pages
from harmony_one.text_decode import decode_text, load_bundled_alphabet

OP_SWITCH_MODE = 0x7E
STATE_BASE_SLOT = 13
TRANSITION_SIZE = 8


@dataclass(frozen=True)
class Transition:
    source: int
    target: int
    instruction: Instruction


@dataclass(frozen=True)
class StateRecord:
    index: int
    first: int
    maximum: int
    transitions: tuple[Transition, ...]


@dataclass(frozen=True)
class Device:
    label: str
    group: int | None
    variables: dict[str, int]  # property -> variable index


@dataclass(frozen=True)
class Step:
    kind: str  # power | input | send | delay | other
    device: str
    value: int | None = None
    group: int | None = None
    record: int | None = None


@dataclass
class Activity:
    number: int
    name: str
    key_map: int
    mode: int | None
    steps: list[Step] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)


def _s16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=True)


def read_state_records(data: bytes, config: RemoteConfig) -> dict[int, StateRecord]:
    section = config.sections.get(raw_slot_for_base_slot(STATE_BASE_SLOT))
    if not section:
        return {}
    start = section[0]
    count = int.from_bytes(data[start : start + 2], "little")
    records = {}
    for index in range(count):
        entry = start + 8 + 3 * index
        at = int.from_bytes(data[entry : entry + 3], "little") - config.base_address
        first = int.from_bytes(data[at : at + 2], "little")
        maximum = int.from_bytes(data[at + 2 : at + 4], "little")
        transition_count = int.from_bytes(data[at + 4 : at + 6], "little")
        transitions = []
        for n in range(transition_count):
            t = at + 7 + TRANSITION_SIZE * n
            transitions.append(
                Transition(
                    _s16(data, t + 1),
                    _s16(data, t + 3),
                    Instruction(data[t + 7], int.from_bytes(data[t + 5 : t + 7], "little")),
                )
            )
        records[index] = StateRecord(index, first, maximum, tuple(transitions))
    return records


def _flatten(lists, instructions, depth=0, seen=()):
    """Instructions of a list with nested calls (0x7F) expanded in place."""
    for item in instructions:
        if item.opcode == OP_CALL and depth < 6 and item.operand < len(lists) and item.operand not in seen:
            yield from _flatten(lists, lists[item.operand], depth + 1, seen + (item.operand,))
        else:
            yield item


def _groups_reached(lists, record: StateRecord) -> set[int]:
    groups = set()
    for transition in record.transitions:
        for item in _flatten(lists, [transition.instruction]):
            if item.opcode == OP_SEND:
                groups.add(item.operand >> 8)
    return groups


PROPERTY = re.compile(r"^(?P<label>.+)_(?P<prop>Power|Input|InputType|TVInput|CompAVInput|Screen)_\d+$")


def read_devices(data: bytes, config: RemoteConfig, lists=None, records=None) -> list[Device]:
    lists = lists if lists is not None else read_action_lists(data, config)
    records = records if records is not None else read_state_records(data, config)
    by_label: dict[str, dict[str, int]] = {}
    for variable in config.state_variables:
        match = PROPERTY.match(variable.name)
        if match:
            by_label.setdefault(match["label"], {})[match["prop"]] = variable.index
    devices = []
    for label, variables in by_label.items():
        groups = set()
        for index in variables.values():
            if index in records:
                groups |= _groups_reached(lists, records[index])
        devices.append(
            Device(label.replace("_", " "), next(iter(groups)) if len(groups) == 1 else None, variables)
        )
    return sorted(devices, key=lambda device: (device.group is None, device.group or 0, device.label))


def fix_ambiguous_i(text: str) -> str:
    """Capital I and lowercase l share a shape: restore 'I' where context makes it plain."""
    text = re.sub(r"\bl(?=[sfn])", "I", text)
    text = re.sub(r"(?<=[A-Z])l\b", "I", text)
    return text.replace("http:??", "http://")


def read_activities(data: bytes, config: RemoteConfig) -> list[Activity]:
    lists = read_action_lists(data, config)
    key_maps = read_key_maps(data, config)
    records = read_state_records(data, config)
    devices = read_devices(data, config, lists, records)
    variable_names = {variable.index: variable.name for variable in config.state_variables}
    device_of_variable = {}
    for device in devices:
        for prop, index in device.variables.items():
            device_of_variable[index] = (device, prop)
    counter = next(
        (v.index for v in config.state_variables if v.name.startswith("CurrentActivityState")), None
    )

    alphabet = load_bundled_alphabet()
    fonts = read_font_sets(data, config)
    pages_by_mode: dict[int, list] = {}
    for page in read_mode_pages(data, config):
        pages_by_mode.setdefault(page.mode, []).append(page)

    activities = []
    for key_map in key_maps[7:]:
        enter = next((b for b in key_map.bindings if b.tag == 1), None)
        leave = next((b for b in key_map.bindings if b.tag == 2), None)
        if enter is None or leave is None or enter.opcode != OP_CALL:
            continue  # the idle entry has no leave handler
        number, mode, steps = None, None, []
        for item in _flatten(lists, lists[enter.operand]):
            if item.opcode == OP_SWITCH_MODE and mode is None:
                mode = item.operand
            elif item.opcode & 0x80:
                index = item.opcode & 0x7F
                if index == counter:
                    number = item.operand
                elif index in device_of_variable:
                    device, prop = device_of_variable[index]
                    steps.append(Step("power" if prop == "Power" else "input", device.label, item.operand))
            elif item.opcode == OP_SEND:
                steps.append(Step("send", "", None, item.operand >> 8, item.operand & 0xFF))
            elif item.opcode == OP_DELAY:
                steps.append(Step("delay", "", item.operand & 0xFF, item.operand >> 8))
        name = f"Activity {number if number is not None else key_map.index}"
        for page in pages_by_mode.get(mode, []):
            seen_draws, lines = set(), []
            for draw in page.draws:
                if (draw.x, draw.y, draw.codes) in seen_draws:
                    continue
                seen_draws.add((draw.x, draw.y, draw.codes))
                lines.append((draw, fix_ambiguous_i(decode_text(data, config, draw, alphabet, fonts))))
            for position, (draw, text) in enumerate(lines):
                if text.startswith("Starting "):
                    parts = [text[len("Starting ") :]]
                    for later, more in lines[position + 1 :]:
                        if later.font != draw.font or later.y <= draw.y:
                            break
                        parts.append(more)
                    name = " ".join(parts)
        used = [step.device for step in steps if step.kind in ("power", "input") and step.device]
        activities.append(
            Activity(
                number if number is not None else -1,
                name,
                key_map.index,
                mode,
                steps,
                list(dict.fromkeys(used)),
            )
        )
    return sorted(activities, key=lambda activity: activity.number)
