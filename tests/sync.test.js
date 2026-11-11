// Unit tests for the pure save/merge/permission rules in src/sync.js.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  mergeShapes,
  outboxEntries,
  replayOutbox,
  aiMayEdit,
  findFrame,
  serialQueue,
  statusText,
} from '../src/sync.js';

const state = (object) => new Map(Object.entries(object));

test('merge keeps each side’s separate changes', () => {
  const base = state({ a: 'a0', b: 'b0', gone: 'g0' });
  const local = state({ a: 'a1', b: 'b0', gone: 'g0', mine: 'm' }); // moved a, drew a new stroke
  const server = state({ a: 'a0', b: 'b1', theirs: 't' }); // other device changed b, erased gone, drew
  const { merged, conflicts, remote } = mergeShapes(base, local, server);
  assert.deepEqual(Object.fromEntries(merged), { a: 'a1', b: 'b1', mine: 'm', theirs: 't' });
  assert.deepEqual(conflicts, []);
  assert.equal(remote, 3);
});

test('the review’s stale-copy case: the saved move is not overwritten', () => {
  // Device A moved the note to x=50 and saved. This tab still had x=0 and changed its text.
  const base = state({ note: '{"x":0,"text":"old"}' });
  const local = state({ note: '{"x":0,"text":"new"}' });
  const server = state({ note: '{"x":50,"text":"old"}' });
  const { merged, conflicts } = mergeShapes(base, local, server);
  assert.equal(merged.get('note'), '{"x":50,"text":"old"}');
  assert.deepEqual(conflicts, ['note'], 'the user is told, instead of a silent overwrite');
});

test('merge edge cases: same change twice, delete against edit', () => {
  const same = mergeShapes(state({ a: '0' }), state({ a: '1' }), state({ a: '1' }));
  assert.deepEqual([same.merged.get('a'), same.conflicts.length], ['1', 0]);
  const erasedHere = mergeShapes(state({ a: '0' }), state({}), state({ a: '1' }));
  assert.equal(erasedHere.merged.get('a'), '1', 'an edit saved elsewhere beats a local erase');
  const erasedThere = mergeShapes(state({ a: '0' }), state({ a: '1' }), state({}));
  assert.equal(erasedThere.merged.has('a'), false);
  assert.deepEqual(erasedThere.conflicts, ['a']);
});

test('unsent edits survive a restart and replay without duplicating or clobbering', () => {
  const persisted = state({ keep: 'k', moved: 'm0', erased: 'e' });
  const local = state({ keep: 'k', moved: 'm1', stroke: 's' });
  const entries = JSON.parse(JSON.stringify(outboxEntries(persisted, local))); // as stored in the browser
  assert.equal(entries.length, 3, 'only the differences are stored');
  // Server unchanged since: everything comes back.
  let replay = replayOutbox(entries, persisted);
  assert.deepEqual(Object.fromEntries(replay.merged), { keep: 'k', moved: 'm1', stroke: 's' });
  assert.equal(replay.applied, 3);
  // The last-moment save actually arrived before the tab died: nothing to redo.
  replay = replayOutbox(entries, local);
  assert.equal(replay.applied, 0);
  assert.deepEqual(Object.fromEntries(replay.merged), Object.fromEntries(local));
  // Another device changed "moved" meanwhile: the new stroke returns, the stale edit does not.
  replay = replayOutbox(entries, state({ keep: 'k', moved: 'm2', erased: 'e', other: 'o' }));
  assert.deepEqual(Object.fromEntries(replay.merged), { keep: 'k', moved: 'm2', other: 'o', stroke: 's' });
  assert.deepEqual(replay.conflicts, ['moved']);
  assert.equal(replay.applied, 2);
});

test('AI may edit its own shapes, never pages, and handwriting only when authorized', () => {
  const ink = { type: 'draw', meta: {} },
    highlight = { type: 'highlight' },
    aiNote = { type: 'text', meta: { ai: true } },
    oldAiNote = { type: 'geo', meta: {} }; // made before shapes were marked
  assert.equal(aiMayEdit(ink, false), false);
  assert.equal(aiMayEdit(highlight, undefined), false);
  assert.equal(aiMayEdit(ink, true), true);
  assert.equal(aiMayEdit(ink, 'yes'), false, 'only a real true counts');
  assert.equal(aiMayEdit(aiNote, false), true);
  assert.equal(aiMayEdit(oldAiNote, false), false);
  for (const type of ['frame', 'image']) assert.equal(aiMayEdit({ type }, true), false);
});

test('pages are found by ID or exact name; a shared name is refused', () => {
  const frames = [
    { id: 'shape:p1', props: { name: 'Problem 1' } },
    { id: 'shape:p2', props: { name: 'Scratch' } },
    { id: 'shape:p3', props: { name: 'scratch' } },
  ];
  assert.equal(findFrame(frames, 'problem 1').frame.id, 'shape:p1');
  assert.equal(findFrame(frames, 'p3').frame.id, 'shape:p3');
  assert.equal(findFrame(frames, 'shape:p2').frame.id, 'shape:p2');
  assert.match(findFrame(frames, 'Scratch').error, /2 frames are named.*p2, p3/);
  assert.match(findFrame(frames, 'Missing').error, /not found/);
});

test('a failed AI request does not block the ones after it', async () => {
  // The review reproduced this with a plain promise chain: after one
  // rejection, later callbacks never ran.
  const ran = [],
    errors = [];
  const enqueue = serialQueue((err) => errors.push(err.message));
  enqueue(async () => ran.push(1));
  enqueue(async () => {
    throw Error('acknowledgement failed');
  });
  enqueue(async () => ran.push(3));
  await enqueue(async () => ran.push(4));
  assert.deepEqual(ran, [1, 3, 4]);
  assert.deepEqual(errors, ['acknowledgement failed']);
});

test('the status line says Saved only when nothing is waiting', () => {
  const facts = { failed: false, saving: false, pending: false, connected: true };
  assert.equal(statusText(facts), 'Saved · Connected');
  // Review finding: reconnecting announced "Saved" while edits were still unsent.
  assert.doesNotMatch(statusText({ ...facts, pending: true }), /^Saved/);
  assert.doesNotMatch(statusText({ ...facts, saving: true }), /^Saved/);
  assert.doesNotMatch(statusText({ ...facts, failed: true, pending: true }), /^Saved/);
  assert.match(statusText({ ...facts, failed: true, keptLocally: true }), /kept on this device/);
  assert.doesNotMatch(statusText({ ...facts, failed: true, keptLocally: false }), /kept on this device/);
  assert.match(statusText({ ...facts, connected: false }), /Offline/);
  assert.doesNotMatch(statusText(facts), /AI/, 'a connection to the server is not an AI connection');
  assert.match(statusText({ ...facts, aiActive: true }), /AI active/);
});
