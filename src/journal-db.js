// IndexedDB transactions confirm local retention; failure leaves the fallback journal intact.
export function createJournalDatabase(factory = globalThis.indexedDB) {
  let opened;
  const known = new Map();
  function open() {
    if (!opened) opened = new Promise((resolve) => {
      if (!factory) return resolve(null);
      let done = false;
      const finish = (db) => { if (done) { db?.close(); return; } done = true; clearTimeout(timer); resolve(db); };
      const timer = setTimeout(() => finish(null), 1500);
      try {
        const request = factory.open('gpt-board-recovery', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('journals');
        request.onsuccess = () => {
          const db = request.result;
          db.onversionchange = () => { db.close(); opened = null; };
          finish(db);
        };
        request.onerror = () => finish(null);
        request.onblocked = () => finish(null);
      } catch { finish(null); }
    });
    return opened;
  }
  async function transaction(mode, operation) {
    const db = await open();
    if (!db) return {ok: false};
    return new Promise((resolve) => {
      let tx, result;
      const timer = setTimeout(() => { try { tx?.abort(); } catch {} resolve({ok: false}); }, 2000);
      const finish = (ok) => { clearTimeout(timer); resolve({ok, result}); };
      try {
        tx = db.transaction('journals', mode);
        tx.oncomplete = () => finish(true);
        tx.onabort = tx.onerror = () => finish(false);
        operation(tx.objectStore('journals'), (value) => { result = value; });
      } catch { finish(false); }
    });
  }
  return {
    async put(key, encoded) {
      const {ok} = await transaction('readwrite', (store) => store.put(encoded, key));
      if (ok) known.set(key, encoded);
      return ok;
    },
    async removeIfUnchanged(key, expected) {
      const {ok, result} = await transaction('readwrite', (store, result) => {
        const request = store.get(key);
        request.onsuccess = () => {
          const matches = request.result === expected;
          if (matches) store.delete(key);
          result(matches);
        };
      });
      if (ok && result && known.get(key) === expected) known.delete(key);
      return ok && result;
    },
    async list(notebook) {
      const {ok, result} = await transaction('readonly', (store, result) => {
        const rows = [], request = store.openCursor();
        request.onsuccess = () => {
          const cursor = request.result;
          if (!cursor) return result(rows);
          if (String(cursor.key).startsWith('gpt-board:journal:v2:' + notebook + ':'))
            rows.push({key: cursor.key, encoded: cursor.value});
          cursor.continue();
        };
      });
      if (!ok) return [];
      for (const row of result) known.set(row.key, row.encoded);
      return result;
    },
    cachedRows: () => [...known].map(([key, encoded]) => ({key, encoded})),
  };
}
