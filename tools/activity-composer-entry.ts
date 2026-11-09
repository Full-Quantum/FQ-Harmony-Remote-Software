import { readFileSync, writeFileSync } from 'node:fs';
import {
  ACTION_LIST_TABLE_SLOT, activityBindings, activityNames, activityScreens,
  applyEdits, archSlot, assertQueueFits, assertStateTableConsistent,
  coverage, handlerSets, parse, roundTrip, screenProgram, SCREEN_END,
  SCREEN_QUEUE_INSTRUCTION, trailerAgrees, valueMaps, type Edit,
} from './packages/codec/src/index.ts';

function argument(name: string): string {
  const at = process.argv.indexOf(`--${name}`);
  if (at < 0 || process.argv[at + 1] === undefined) {
    throw new Error(`Missing --${name}`);
  }
  return process.argv[at + 1]!;
}

const repairExisting = process.argv.includes('--repair-existing');
const source = parse(new Uint8Array(readFileSync(
  argument(repairExisting ? 'original' : 'in'),
)));
const activity = Number(argument('source-activity-id'));
const set = Number(argument('source-set'));
if (!Number.isInteger(activity) || !Number.isInteger(set)
    || !activityBindings(source).some(one => one.activity === activity && one.set === set)) {
  throw new Error('Source activity identity does not match its key map');
}
const names = activityNames(source);
const selected = names.filter(one => one.activity === activity);
if (selected.length !== 1 || names.filter(one => one.name === selected[0]!.name).length !== 1) {
  throw new Error('Source activity has no unambiguous menu label');
}
if (!repairExisting) {
  if (names.some(one => one.name.toLowerCase() === argument('label').toLowerCase())) {
    throw new Error('The new activity name already exists in the remote menu');
  }
  process.argv.push('--startup-like', selected[0]!.name, '--icon-like', selected[0]!.name);
  await import('./packages/codec/bin/compose-activity.ts');
}

const output = argument('out');
const built = parse(new Uint8Array(readFileSync(repairExisting ? argument('in') : output)));
const oldSetCount = handlerSets(source)?.addresses.length;
if (oldSetCount === undefined) throw new Error('Source has no handler-set table');
const added = activityBindings(built).filter(one => one.set === oldSetCount);
const newActivity = added[0]?.activity;
const originalMode = activityScreens(source)?.screens.get(activity);
const blankMode = newActivity === undefined ? undefined : activityScreens(built)?.screens.get(newActivity);
if (newActivity === undefined || added.some(one => one.activity !== newActivity)
    || originalMode === undefined || blankMode === undefined || originalMode === blankMode) {
  throw new Error('Cannot identify the new and template working screens');
}
const oldLists = source.actionLists();
const lists = built.actionLists();
const pointers = built.pointerArray(archSlot(built.architecture!, ACTION_LIST_TABLE_SLOT));
if (oldLists === undefined || lists === undefined || pointers === undefined) {
  throw new Error('Cannot locate new action lists');
}
const edits: Edit[] = [];
for (let index = oldLists.length; index < lists.length; index++) {
  const start = built.blobOffsetOf(pointers[index]!);
  if (start === undefined) throw new Error('New action list points outside the image');
  for (let instruction = 0; instruction < lists[index]!.length; instruction++) {
    const one = lists[index]![instruction]!;
    if (one.opcode === 0x7e && one.operand === blankMode) {
      edits.push({
        start: start + 1 + instruction * 3,
        bytes: new Uint8Array([originalMode & 0xff, originalMode >> 8]),
        owner: 'new activity working-screen return',
      });
    }
  }
}
if (edits.length !== 1) throw new Error(`Expected one new screen-return instruction, found ${edits.length}`);
const screenMap = activityScreens(built);
const map = screenMap === undefined ? undefined : valueMaps(built)?.[screenMap.map];
const sourceCases = map?.entries.filter(([value]) => value === activity) ?? [];
const addedCases = map?.entries.filter(([value]) => value === newActivity) ?? [];
if (map === undefined || sourceCases.length !== 1 || addedCases.length !== 1) {
  throw new Error('Cannot identify unique template and new activity return cases');
}
const target = addedCases[0]![1];
const maps = valueMaps(built)!;
const references = maps.flatMap(one => [
  ...one.entries.map(([, address]) => address),
  ...one.ranges.map(([, , address]) => address),
]).filter(address => address === target);
const program = screenProgram(built, target);
const queue = program?.[0];
if (references.length !== 1 || program?.length !== 2
    || queue?.opcode !== SCREEN_QUEUE_INSTRUCTION || program[1]?.opcode !== SCREEN_END
    || queue.operands[2] !== 0x7e
    || (queue.operands[0]! | (queue.operands[1]! << 8)) !== blankMode) {
  throw new Error('New activity return case is shared or has an unexpected program');
}
edits.push({
  start: queue.start + 1,
  bytes: new Uint8Array([originalMode & 0xff, originalMode >> 8]),
  owner: 'new activity return case',
});
const repaired = parse(applyEdits(built, edits).bytes);
if (activityScreens(repaired)?.screens.get(newActivity) !== originalMode) {
  throw new Error('New activity does not return through the template working screen');
}
for (const [value, mode] of activityScreens(source)!.screens) {
  if (activityScreens(repaired)?.screens.get(value) !== mode) {
    throw new Error(`Changed an original activity screen: ${value}`);
  }
}
assertQueueFits(repaired);
assertStateTableConsistent(repaired);
const accounting = coverage(repaired);
if (accounting.accounted !== accounting.total) {
  throw new Error(`Repaired navigation leaves ${accounting.total - accounting.accounted} bytes unaccounted`);
}
if (accounting.overlaps.length !== 0) throw new Error('Repaired navigation has overlapping claims');
if (!trailerAgrees(repaired)) throw new Error('Repaired navigation checksum does not match');
if (!roundTrip(repaired).equal) throw new Error('Repaired navigation does not round-trip');
writeFileSync(output, repaired.blob);
process.stdout.write(`Template working screen ${originalMode} reused; new activity ${newActivity} return verified\n`);
