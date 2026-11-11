import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {PREFIX, makeJournal, durableWrite, removeIfUnchanged} from '../src/recovery.js';
import {createJournalDatabase} from '../src/journal-db.js';
class Storage {
  values = new Map();
  getItem(k) { return this.values.get(k) ?? null; }
  setItem(k,v) { this.values.set(k,v); }
  removeItem(k) { this.values.delete(k); }
}
for (const file of ['src/app.js', 'dist/native/app.js']) {
  const source = fs.readFileSync(new URL('../'+file, import.meta.url), 'utf8');
  const code = source.slice(source.indexOf('function writeOutbox() {'),source.indexOf('// A synchronous fallback'));
  function context(database) {
    const local = new Storage();
    const ctx = vm.createContext({book:'math', store:{}, tab:'one', device:'ipad', revision:1,
      baseRev:'r', recoverySequence:0, dirty:true, keptLocally:false,
      journalChain:Promise.resolve(), lastJournalEncoded:null, recoveredJournals:[],
      PREFIX, makeJournal, durableWrite, removeIfUnchanged, journalDatabase:database,
      recoveryStorage:()=>local, readLocal:key=>local.getItem(key), refreshStatus(){},
      persistedShapes:new Map(), shapeState:()=>new Map(), outboxEntries:()=>[['shape:a',null,'{}']],
    });
    vm.runInContext(code,ctx);
    return {ctx,local};
  }
  test(file+': local retention waits for commit and old completion cannot confirm newer ink', async()=>{
    let commit;
    const {ctx}=context({put:()=>new Promise(resolve=>{commit=resolve;})});
    const writing=ctx.writeOutbox(); await Promise.resolve();
    assert.equal(ctx.keptLocally,false);
    ctx.revision=2;
    commit(true); await writing;
    assert.equal(ctx.keptLocally,false);
  });
  test(file+': unavailable database falls back and acknowledgment clears only matching records', async()=>{
    const removed=[];
    const {ctx,local}=context({put:async()=>false,removeIfUnchanged:async(...args)=>removed.push(args)});
    await ctx.writeOutbox(); assert.equal(ctx.keptLocally,true);
    const key=PREFIX+'math:one', original=local.getItem(key);
    ctx.dirty=false; const cleanup=ctx.writeOutbox();
    local.setItem(key,'newer emergency journal');
    await cleanup;
    assert.equal(local.getItem(key),'newer emergency journal');
    assert.equal(removed[0][1],original);
  });
}
test('IndexedDB unavailable or denied fails safely without claiming retention',async()=>{
  for(const factory of [null,{open(){throw Error('denied');}}]){
    const db=createJournalDatabase(factory);
    assert.equal(await db.put('a','b'),false);
    assert.deepEqual(await db.list('math'),[]);
    assert.equal(await db.removeIfUnchanged('a','b'),false);
  }
});
