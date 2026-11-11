import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const script=fs.readFileSync(new URL('../ios/GPTBoardPad/PencilToggle.js',import.meta.url),'utf8');
test('native gesture uses existing buttons and alternates pen and eraser',()=>{
 let tool='pen',clicks=0;
 const buttons=Object.fromEntries(['pen','eraser'].map(name=>[name,{disabled:false,click(){tool=name;clicks++;}}]));
 const context=vm.createContext({document:{querySelector(selector){
  if(selector.includes('aria-pressed'))return {dataset:{tool}};
  return buttons[selector.includes('"eraser"')?'eraser':'pen'];
 }}});
 assert.equal(vm.runInContext(script,context),'eraser');assert.equal(tool,'eraser');
 assert.equal(vm.runInContext(script,context),'pen');assert.equal(tool,'pen');
 tool='highlight';assert.equal(vm.runInContext(script,context),'eraser');
 assert.equal(clicks,3);
 buttons.pen.disabled=true;assert.equal(vm.runInContext(script,context),null);assert.equal(clicks,3);
});
test('pairing/loading pages safely ignore Pencil toggles',()=>{
 assert.equal(vm.runInNewContext(script,{document:{querySelector:()=>null}}),null);
});
