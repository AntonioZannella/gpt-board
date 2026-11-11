import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const app=fs.readFileSync(new URL('../src/app.js',import.meta.url),'utf8');
const code=app.slice(app.indexOf('async function operation(msg) {'),app.indexOf('// Report a finished request'));
test('AI reads never end an active Pencil stroke or save a partial gesture',async()=>{
 for(const state of [{gesture:{kind:'ink'},penActive:true},{gesture:null,penActive:true}]){
  const context=vm.createContext({...state,finishActive(){assert.fail('Interrupted Pencil');},save(){assert.fail('Saved partial gesture');}});
  vm.runInContext(code,context);
  const result=await context.operation({op:'export'});
  assert.equal(result.ok,false);
  assert.match(result.error,/Pencil lifts/);
 }
});
test('a Pencil contact starting while an AI read saves remains untouched',async()=>{
 const context=vm.createContext({gesture:null,penActive:false,async save(){context.gesture={kind:'ink'};}});
 vm.runInContext(code,context);
 const result=await context.operation({op:'export'});
 assert.equal(result.ok,false);
 assert.equal(context.gesture.kind,'ink');
});
test('image capture yields between drawing batches and encodes without synchronous toDataURL',async()=>{
 let painted=0,yields=0;
 const canvas={getContext:()=>({fillRect(){},scale(){},translate(){}}),
  toDataURL(){assert.fail('Blocking PNG encode');},toBlob(done){done({});}};
 const context=vm.createContext({shapes:[{type:'draw'},{type:'draw'}],
  document:{createElement:()=>canvas},getBounds:()=>({}),intersects:()=>true,
  paintWorld(){painted++;},performance:{now:()=>painted*10},
  setTimeout(done){yields++;queueMicrotask(done);},
  FileReader:class {readAsDataURL(){this.result='data:image/png;base64,test';queueMicrotask(()=>this.onload());}}});
 vm.runInContext(app.slice(app.indexOf('async function exportImage('),app.indexOf('\nfunction allBounds()')),context);
 const result=await context.exportImage({x:0,y:0,w:800,h:1200});
 assert.equal(painted,2);
 assert.equal(yields,2);
 assert.equal(result.png,'data:image/png;base64,test');
});
