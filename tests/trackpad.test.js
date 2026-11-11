import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TrackpadPager, wheelPixels, navigationKey } from '../src/trackpad.js';
test('horizontal swipe turns once; momentum cannot skip pages', () => {
  const p = new TrackpadPager(), turns = [];
  for (let t=0;t<1000;t+=16) {
    const r=p.update(25,1,t,()=>true); if(r.turn)turns.push(r.turn);
  }
  assert.deepEqual(turns,[1]);
  assert.equal(p.update(-100,0,1300,()=>true).turn,-1);
});
test('vertical scroll, diagonal scroll, and pan remain scrolls',()=>{
  for(const [x,y,allowed] of [[1,50,true],[50,45,true],[50,1,false]]){
    const p=new TrackpadPager();
    for(let t=0;t<500;t+=16)assert.equal(p.update(x,y,t,()=>allowed).consume,false);
    assert.equal(p.update(100,0,510,()=>true).consume,false);
  }
});
test('small accidental horizontal movement does not turn',()=>{
  const p=new TrackpadPager();
  assert.equal(p.update(35,0,0,()=>true).turn,undefined);
  assert.equal(p.update(35,0,300,()=>true).turn,undefined);
});
test('gesture determines direction and normalizes wheel units',()=>{
  const p=new TrackpadPager();let direction;
  assert.equal(p.update(-100,0,0,d=>(direction=d,true)).turn,-1);
  assert.equal(direction,-1);
  assert.deepEqual(wheelPixels({deltaX:2,deltaY:3,deltaMode:1}),{x:32,y:48});
  assert.deepEqual(wheelPixels({deltaX:0,deltaY:2,deltaMode:0,shiftKey:true}),{x:2,y:0});
});
test('keyboard navigation respects OS shortcuts and key repeat',()=>{
  assert.equal(navigationKey({key:'ArrowRight'}),'next');
  assert.equal(navigationKey({key:'ArrowLeft'}),'previous');
  assert.equal(navigationKey({key:'ArrowLeft',metaKey:true}),null);
  assert.equal(navigationKey({key:'ArrowRight',repeat:true}),null);
  assert.equal(navigationKey({key:'+',shiftKey:true}),'zoom-in');
  assert.equal(navigationKey({key:'0'}),'fit');
});
test('a separate swipe does not wait for the old 400ms cooldown',()=>{
  const p=new TrackpadPager();
  assert.equal(p.update(60,0,0,()=>true).turn,1);
  assert.equal(p.update(60,0,125,()=>true).turn,1);
  assert.equal(p.update(60,0,250,()=>true).turn,1);
});
test('fresh push during an old momentum tail starts another page',()=>{
  const p=new TrackpadPager();
  assert.equal(p.update(60,0,0,()=>true).turn,1);
  for(const [time,x] of [[16,40],[32,25],[48,15],[64,8],[80,4],[96,2],[112,1]])
    assert.equal(p.update(x,0,time,()=>true).turn,undefined);
  assert.equal(p.update(30,0,128,()=>true).turn,undefined);
  assert.equal(p.update(30,0,144,()=>true).turn,1);
});
test('reversing direction responds without moving the pointer or waiting',()=>{
  const p=new TrackpadPager();
  assert.equal(p.update(60,0,0,()=>true).turn,1);
  assert.equal(p.update(-60,0,50,()=>true).turn,-1);
});
test('decaying momentum and small jitter never become extra page turns',()=>{
  const p=new TrackpadPager();
  assert.equal(p.update(60,0,0,()=>true).turn,1);
  let time=16;
  for(const x of [40,30,20,12,9,5,2,3,1,2,1,0.5,1,0.2]){
    assert.equal(p.update(x,0,time,()=>true).turn,undefined);time+=16;
  }
});
test('gradually accelerating second swipe works during uninterrupted wheel delivery',()=>{
  const p=new TrackpadPager();let time=0;const turns=[];
  // The mouse does not move: the browser keeps one wheel stream alive.
  for(const x of [20,25,30,25,18,12,8,5,3,2,1,2,3,5,7,10,14,18,22,25,20,12,6,3,1]){
    const r=p.update(x,0,time,()=>true);if(r.turn)turns.push(r.turn);time+=16;
  }
  assert.deepEqual(turns,[1,1]);
});
test('twenty gradual swipes without a pointer move or quiet gap each turn once',()=>{
  const p=new TrackpadPager();let time=0,count=0;
  for(let swipe=0;swipe<20;swipe++){
    for(const x of [2,3,5,7,10,14,18,22,25,20,12,6,3,1,1,1]){
      if(p.update(x,0,time,()=>true).turn)count++;time+=16;
    }
  }
  assert.equal(count,20);
});
test('recorded physical swipes with a stationary pointer both turn',()=>{
  // Real Chrome wheel samples: first turn on 180054; the next push was swallowed.
  const samples = [[180038,25,-2],[180054,72,-1],[180088,70,3],
    [180104,112,0],[180121,119,0],[180138,119,0],[180154,115,0],
    [180171,111,0],[180187,105,0],[180204,100,0],[180221,96,0],
    [180237,92,0],[180254,87,0],[180271,82,0],[180288,77,0],
    [180309,72,0],[180321,68,0],[180338,63,0],[180355,59,0],
    [180372,55,0],[180388,51,0],[180404,47,0],[180421,43,0],
    [180439,41,0],[180455,37,0],[180471,34,0],[180488,32,0],
    [180505,29,0],[180538,3,0],[180554,25,1],[180571,39,3],
    [180588,53,8],[180604,94,0],[180621,102,0],[180638,102,0],
    [180654,99,0],[180672,96,0],[180688,91,0],[180704,90,0],
    [180721,85,0],[180739,81,0],[180754,76,0],[180771,72,0],
    [180788,66,0],[180805,62,0],[180821,58,0],[180837,55,0],
    [180855,51,0],[180871,47,0],[180888,43,0],[180904,40,0],
    [180921,37,0],[180938,34,0],[180955,31,0],[180971,28,0]];
  for (const direction of [1,-1]) {
    const p=new TrackpadPager(), turns=[];
    for(const [t,x,y] of samples){
      const r=p.update(x*direction,y,t,()=>true);if(r.turn)turns.push([t,r.turn]);
    }
    assert.deepEqual(turns,[[180054,direction],[180571,direction]]);
  }
});
test('one deep low sample can separate rapid swipes without a quiet gap',()=>{
  const p=new TrackpadPager();let time=0,turns=0;
  for(let i=0;i<10;i++){
    // Shape seen in the capture: 29 -> 5 -> 18 -> 22 -> 30.
    for(const x of [5,18,22,30,33,34,34,34,33,36,35,32,29]){
      if(p.update(x, x/5, time,()=>true).turn)turns++;time+=16;
    }
  }
  assert.equal(turns,10);
});
