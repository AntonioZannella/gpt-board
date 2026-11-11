// Each document owns its journal. A reload gets a new owner; old journals
// remain until their exact contents have been confirmed by the Mac.
export const PREFIX = 'gpt-board:journal:v2:';
export function makeJournal({ notebook, device, tab, revision, sequence, entries }) {
  return {
    version: 2,
    notebook,
    device,
    tab,
    revision,
    operation: `${tab}:${sequence}`,
    sequence,
    updated: Date.now(),
    entries,
  };
}
export function validJournal(value, notebook) {
  return (
    value?.version === 2 &&
    value.notebook === notebook &&
    ['device', 'tab', 'operation'].every((k) => typeof value[k] === 'string') &&
    Array.isArray(value.entries) &&
    value.entries.length <= 50000 &&
    value.entries.every(
      (e) =>
        Array.isArray(e) &&
        e.length === 3 &&
        typeof e[0] === 'string' &&
        e[0].startsWith('shape:') &&
        e.slice(1).every((v) => v === null || typeof v === 'string'),
    )
  );
}
export function durableWrite(storage, key, value) {
  try {
    const encoded = JSON.stringify(value);
    storage.setItem(key, encoded); // atomic: quota/denial leaves the previous value
    if (storage.getItem(key) !== encoded) throw Error('Storage verification failed');
    return true;
  } catch {
    return false;
  }
}
export function removeIfUnchanged(storage, key, expected) {
  try {
    if (storage.getItem(key) === expected) storage.removeItem(key);
  } catch {
    /* Leave recovery material intact if storage is unavailable. */
  }
}
export function journals(storage, notebook) {
  if (!storage) return [];
  const found = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (!key?.startsWith(PREFIX)) continue;
    const encoded = storage.getItem(key);
    try {
      const value = JSON.parse(encoded);
      if (validJournal(value, notebook)) found.push({ key, encoded, value });
    } catch {
      /* Damaged originals stay present for export; never erase them. */
    }
  }
  return found;
}
export async function liveDocuments(tab) {
  if (typeof BroadcastChannel !== 'function') return null;
  const channel = new BroadcastChannel('gpt-board:recovery-owners');
  const alive = new Set([tab]);
  channel.onmessage = ({ data }) => {
    if (data?.kind === 'probe') channel.postMessage({ kind: 'alive', tab });
    if (data?.kind === 'alive') alive.add(data.tab);
  };
  channel.postMessage({ kind: 'probe' });
  await new Promise((resolve) => setTimeout(resolve, 200));
  // Keep responding so another active tab cannot steal this tab's journal.
  return alive;
}

// Includes damaged/unknown-version bytes so recovery export never hides them.
export function rawJournals(storage, notebook) {
  if (!storage) return [];
  const rows = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (key?.startsWith(PREFIX + notebook + ':')) rows.push({ key, encoded: storage.getItem(key) });
  }
  return rows;
}
