import test from 'node:test';
import assert from 'node:assert/strict';
import {previewPoints} from '../src/model.js';
import {replacementFrames} from '../src/pages.js';
test('bounded preview preserves the previous sample and both endpoints', () => {
  for(const size of [0,1,1199,1200,1201,2400,32000]) {
    const points=Array.from({length:size},(_,x)=>({x,y:x,z:.5}));
    const step=Math.max(1,Math.ceil(size/1200));
    const old=points.filter((_,i)=>i%step===0 || i===size-1);
    assert.deepEqual(previewPoints(points),old);
  }
});
test('replacement list keeps page order and requires exactly one locked image',()=>{
  const frames=['c','a','b','d'].map(id=>({id}));
  const image=(parentId,isLocked=true)=>({type:'image',parentId,isLocked});
  const store={one:image('a'),two:image('b'),three:image('b'),four:image('c'),five:image('c',false),ink:{type:'draw',parentId:'d',isLocked:true}};
  assert.deepEqual(replacementFrames(frames,store),[frames[0],frames[1]]);
});
