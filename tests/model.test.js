import {test} from 'node:test';import assert from 'node:assert/strict';import {encode,decode,transform,apply,inverse,cut} from '../src/model.js';
import fs from 'node:fs';
test('pressure and coordinates round trip',()=>{const points=[{x:125.25,y:38.5,z:.5},{x:127.25,y:37.5,z:.75},{x:130.25,y:44.5,z:1}];assert.deepEqual(decode({path:encode(points)}),points)});
test('nested transformed coordinates round trip',()=>{let parent={id:'p',typeName:'shape',x:200,y:500,rotation:.5},s={id:'s',typeName:'shape',parentId:'p',x:20,y:10,rotation:.2},m=transform(s,{p:parent});let p={x:50,y:40},out=inverse(m,apply(m,p));assert.ok(Math.abs(out.x-p.x)<1e-8&&Math.abs(out.y-p.y)<1e-8)});
test('partial eraser cuts a segment crossing its circle',()=>{const runs=cut([{x:0,y:0,z:.2},{x:100,y:0,z:.8}],{x:50,y:0},10);assert.equal(runs.length,2);assert.equal(runs[0].at(-1).x,40);assert.equal(runs[1][0].x,60)});

import {penSamples,smoothInk} from '../src/model.js';
test('pen samples survive empty, absent and unavailable coalescing',()=>{
 for(const method of [undefined,()=>[],()=>{throw Error('unsupported')}]){const event={clientX:20,clientY:30,getCoalescedEvents:method};assert.deepEqual(penSamples(event),[event])}
 const sample={clientX:10},event={clientX:20,getCoalescedEvents:()=>[sample]};assert.deepEqual(penSamples(event),[sample,event]);
});
test('smoothing retains endpoints and rounds corners without overshooting',()=>{
 const source=[{x:0,y:0,z:.5},{x:20,y:0,z:.6},{x:20,y:20,z:.7}],out=smoothInk(source);
 assert.deepEqual(out[0],source[0]);assert.equal(out.at(-1).x,20);assert.equal(out.at(-1).y,20);
 assert.ok(out.some(p=>p.x>0&&p.x<20&&p.y>0&&p.y<20));
 assert.ok(out.every(p=>p.x>=-1e-9&&p.x<=20+1e-9&&p.y>=-1e-9&&p.y<=20+1e-9&&Number.isFinite(p.z)));
 assert.equal(source.length,3);assert.deepEqual(smoothInk([]),[]);assert.deepEqual(smoothInk([source[0]]),[source[0]]);
});
