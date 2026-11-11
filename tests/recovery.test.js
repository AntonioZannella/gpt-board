import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeJournal, durableWrite, journals, removeIfUnchanged, PREFIX } from '../src/recovery.js';
import { pageAction, hiddenRecord } from '../src/pages.js';
class Storage {
  values = new Map(); fail = false;
  get length() { return this.values.size; }
  key(i) { return [...this.values.keys()][i]; }
  getItem(k) { return this.values.get(k) ?? null; }
  setItem(k,v) { if (this.fail) throw Error('Quota or storage denied'); this.values.set(k,v); }
  removeItem(k) { this.values.delete(k); }
}
const journal = tab => makeJournal({ notebook:'math',device:'device',tab,revision:'r1',sequence:1,entries:[['shape:a',null,'{}']] });
test('separate tabs preserve operation, device, notebook and base revision identity', () => {
 const storage = new Storage(), a=PREFIX+'math:a', b=PREFIX+'math:b';
 assert.ok(durableWrite(storage,a,journal('a'))); assert.ok(durableWrite(storage,b,journal('b')));
 const original=storage.getItem(a); durableWrite(storage,a,{...journal('a'),operation:'a:2'});
 removeIfUnchanged(storage,a,original); assert.equal(journals(storage,'math').length,2);
 removeIfUnchanged(storage,b,storage.getItem(b)); assert.ok(storage.getItem(a));
 assert.equal(journals(storage,'physics').length,0);
});
test('quota and interrupted writes retain the previous journal and never claim success', () => {
 const s=new Storage(), key=PREFIX+'math:a'; durableWrite(s,key,journal('a')); const before=s.getItem(key);
 s.fail=true; assert.equal(durableWrite(s,key,{...journal('a'),operation:'a:2'}),false); assert.equal(s.getItem(key),before);
 s.fail=false; s.setItem(PREFIX+'math:damaged','{'); assert.equal(journals(s,'math').length,1); assert.equal(s.getItem(PREFIX+'math:damaged'),'{');
});
test('page duplication remaps descendants; reorder and trash preserve writing coordinates and IDs', () => {
 const store={p:{id:'p',typeName:'shape',type:'frame',parentId:'page:x',x:0,y:0,props:{w:100,h:100,name:'A'}},
  ink:{id:'ink',typeName:'shape',type:'draw',parentId:'p',x:4,y:8,props:{}},
  q:{id:'q',typeName:'shape',type:'frame',parentId:'page:x',x:0,y:180,props:{w:100,h:100,name:'B'}}};
 let n=0; const before=structuredClone(store.ink), frames=[store.p,store.q];
 const copy=pageAction(store,frames,'p','duplicate',null,()=>`copy${++n}`);
 assert.equal(store.copy2.parentId,copy); assert.deepEqual(store.ink,before);
 pageAction(store,frames,'p','down'); assert.equal(store.p.meta.order,1); assert.deepEqual(store.ink,before);
 pageAction(store,frames,'p','trash'); assert.ok(hiddenRecord(store.ink,store));
 pageAction(store,frames,'p','restore'); assert.equal(hiddenRecord(store.ink,store),false); assert.deepEqual(store.ink,before);
});
