import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
// Exercise the actual queued browser callback without a physical iPad.
const app=fs.readFileSync(new URL('../src/app.js',import.meta.url),'utf8');
const callback=app.slice(app.indexOf('function touchMove() {'),app.indexOf('// Store raw samples'));
function run(penActive,gesture=null) {
 const state={penActive,gesture,touchStart:{pts:[{x:100,y:100}],camera:{x:0,y:0,z:1}},
  touches:new Map([[1,{x:180,y:140}]]),pageSwipe:null,isWhiteboard:true,
  frames:[],base:1,camera:{x:0,y:0,z:1},viewChanged(){},touchReset(){}};
 vm.createContext(state);vm.runInContext(callback+'\ntouchMove();',state);
 return [state.camera.x,state.camera.y,state.camera.z];
}
test('queued palm motion cannot change the camera after Pencil contact starts',()=>{
 assert.deepEqual(run(true),[0,0,1]);
});
test('queued touch motion cannot displace an active ink stroke',()=>{
 assert.deepEqual(run(false,{kind:'ink'}),[0,0,1]);
});
test('finger panning still works outside a drawing contact',()=>{
 assert.deepEqual(run(false),[80,40,1]);
});
