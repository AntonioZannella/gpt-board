// Pure decision logic for saving, merging, recovery and AI permissions.
// No DOM and no network here, so every rule below is unit-tested in Node
// (tests/sync.test.js). app.js applies the results.

// Three-way merge of shapes, keyed by shape ID. Each argument is a
// Map of id -> JSON text (a missing id means "no such shape").
//   base   = what this tab last knew the server had
//   local  = what this tab has now
//   server = what the server has now
// Rule per shape: if only one side changed it, that change is kept. If both
// changed it differently, the already-saved server copy wins and the ID is
// reported, so the tab can tell the user instead of silently overwriting.
export function mergeShapes(base, local, server) {
  const merged = new Map(),
    conflicts = [];
  let remote = 0;
  for (const id of new Set([...base.keys(), ...local.keys(), ...server.keys()])) {
    const b = base.get(id),
      l = local.get(id),
      s = server.get(id);
    let winner;
    if (l === b) {
      winner = s; // this tab did not touch it
      if (s !== b) remote++;
    } else if (s === b || s === l) {
      winner = l; // only this tab changed it, or both made the same change
    } else {
      winner = s;
      conflicts.push(id);
      remote++;
    }
    if (winner !== undefined) merged.set(id, winner);
  }
  return { merged, conflicts, remote };
}

// The unsent edits of a tab, small enough to keep in browser storage:
// one [id, baseJson | null, localJson | null] entry per shape that differs.
export function outboxEntries(persisted, local) {
  const entries = [];
  for (const [id, json] of local)
    if (persisted.get(id) !== json) entries.push([id, persisted.get(id) ?? null, json]);
  for (const [id, json] of persisted) if (!local.has(id)) entries.push([id, json, null]);
  return entries;
}

// Replay stored unsent edits onto a freshly loaded notebook. It is the same
// three-way merge: for untouched shapes all three sides agree.
export function replayOutbox(entries, server) {
  const base = new Map(server),
    local = new Map(server);
  for (const [id, before, after] of entries) {
    before === null ? base.delete(id) : base.set(id, before);
    after === null ? local.delete(id) : local.set(id, after);
  }
  const result = mergeShapes(base, local, server);
  let applied = 0;
  for (const [id] of entries) if (result.merged.get(id) !== server.get(id)) applied++;
  return { ...result, applied };
}

// May an AI tool call change or delete this shape? Pages and imported page
// images: never. The student's handwriting: only when the call carries the
// user's explicit authorization. Anything the AI added: yes.
export function aiMayEdit(shape, authorized) {
  if (['frame', 'image'].includes(shape.type)) return false;
  if (shape.meta?.ai === true) return true;
  return authorized === true;
}

// Find a page by stable ID or by name. A name shared by two pages is refused
// rather than quietly reading the first one.
export function findFrame(frames, key) {
  const wanted = String(key).trim();
  const byId = frames.find((f) => f.id === wanted || f.id === 'shape:' + wanted);
  if (byId) return { frame: byId };
  const named = frames.filter((f) => String(f.props.name).toLowerCase() === wanted.toLowerCase());
  if (named.length === 1) return { frame: named[0] };
  if (!named.length) return { error: 'Frame not found. Use an exact frame name or frame ID.' };
  return {
    error:
      `${named.length} frames are named "${wanted}". Use a frame ID instead: ` +
      named.map((f) => f.id.replace('shape:', '')).join(', '),
  };
}

// Runs tasks one at a time, in order. A task that fails is reported and the
// queue carries on: one failed AI request must not block every later one.
export function serialQueue(onError = () => {}) {
  let tail = Promise.resolve();
  return (task) => (tail = tail.then(task).catch(onError));
}

// One place decides the status line, from facts rather than from whichever
// event fired last. "Saved" is only ever shown when nothing is waiting.
export function statusText({ failed, saving, pending, connected, keptLocally, aiActive, notice }) {
  let text = 'Saved';
  if (failed)
    text = keptLocally
      ? 'Not saved to the Mac yet — retrying. Your changes are kept on this device.'
      : 'Not saved yet — retrying. Keep this tab open.';
  else if (saving) text = keptLocally ? 'Saving to Mac — kept on this device' : 'Saving to Mac…';
  else if (pending) text = keptLocally ? 'Kept on this device — waiting to sync to Mac' : 'Unsaved changes — keep this tab open';
  text += connected ? ' · Connected' : ' · Offline, reconnecting';
  if (aiActive) text += ' · AI active';
  return notice ? `${text} · ${notice}` : text;
}
