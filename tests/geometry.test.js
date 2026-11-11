import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sortShapesByDepth,drawingBounds} from '../src/model.js';
function originalSort(shapes,store){
 const depth=s=>{let n=0;while(store[s.parentId]?.typeName==='shape'&&n<30){n++;s=store[s.parentId];}return n;};
 return shapes.sort((a,b)=>depth(a)-depth(b)||(String(a.index)<String(b.index)?-1:String(a.index)>String(b.index)?1:0));
}
test('optimized ordering matches existing ordering, including ties and malformed chains',()=>{
 const shapes=Array.from({length:500},(_,i)=>({id:String(i),typeName:'shape',parentId:i%7?String(Math.floor(i/2)):'page',index:i%9?String((i*73)%101):undefined}));
 const store=Object.fromEntries(shapes.map(s=>[s.id,s]));
 assert.deepEqual(sortShapesByDepth([...shapes],store),originalSort([...shapes],store));
});
test('sorting visits each parent chain once, not once per comparison',()=>{
 const shapes=Array.from({length:1000},(_,i)=>({id:String(i),typeName:'shape',parentId:'parent',index:String((i*73)%1000)}));
 let reads=0;const store=new Proxy({parent:{typeName:'shape',parentId:'page'}},{get:(s,k)=>(reads++,s[k])});
 originalSort([...shapes],store);const before=reads;reads=0;
 sortShapesByDepth([...shapes],store);
 assert.ok(reads<before/3,`${reads} reads versus ${before}`);
 console.log(`Parent lookups for 1,000 shapes: ${before} -> ${reads}`);
});
test('drawing bounds retain negative positions, empty defaults and exact extents',()=>{
 assert.deepEqual(drawingBounds([],x=>x),{x:0,y:0,w:1000,h:1000});
 assert.deepEqual(drawingBounds([{x:-25,y:10,w:5,h:2},{x:0,y:-9,w:40,h:50}],x=>x),{x:-25,y:-9,w:65,h:50});
});
test('large whiteboards do not exceed the JavaScript argument limit',()=>{
 const shapes=Array.from({length:200000},(_,i)=>({x:i,y:-i,w:1,h:2}));
 let calls=0;
 assert.deepEqual(drawingBounds(shapes,b=>(calls++,b)),{x:0,y:-199999,w:200000,h:200001});
 assert.equal(calls,shapes.length);
});
