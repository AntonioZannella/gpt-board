import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
for (const file of ['src/app.js']) {
  const source = fs.readFileSync(new URL('../' + file, import.meta.url), 'utf8');
  test(file + ': completed strokes save during the next ink gesture, other edits wait', async () => {
    const code = source.slice(source.indexOf('function save() {'), source.indexOf('async function exportImage('));
    for (const kind of ['ink', 'erase', null]) {
      let pushed = 0, deferred = 0;
      const ctx = vm.createContext({
        saveTimer: null, saveChain: Promise.resolve(), dirty: true,
        gesture: kind ? {kind} : null, saving: false, saveFailed: false,
        clearTimeout() {}, setTimeout() {deferred++;}, refreshStatus() {}, writeOutbox() {},
        async pushChanges() {pushed++; ctx.dirty = false; return true;},
      });
      vm.runInContext(code, ctx);
      await ctx.save();
      assert.equal(pushed, kind === 'erase' ? 0 : 1);
      assert.equal(deferred, kind === 'erase' ? 1 : 0);
    }
  });
}
