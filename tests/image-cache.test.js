import test from 'node:test';
import assert from 'node:assert/strict';
import {createImageCache} from '../src/image-cache.js';
function fixture(limit=12) {
  const timers=[], images=[]; let changes=0;
  const cache=createImageCache({limit, create:()=>{const im={}; images.push(im);return im;},
    changed:()=>changes++, failed:()=>{}, later:fn=>timers.push(fn)});
  return {cache,timers,images,changes:()=>changes};
}
test('failed page retries, redraws on success, and keeps requests bounded',()=>{
  const f=fixture(); f.cache.get('page','/one');
  f.images[0].onerror(); f.timers.shift()();
  assert.equal(f.images.length,2); f.images[1].onload();assert.equal(f.changes(),1);
  for(let i=0;i<4;i++){f.images.at(-1).onerror();f.timers.shift()?.();}
  assert.equal(f.timers.length,0);assert.equal(f.images.length,5);
});
test('replaced asset reloads, and an old retry cannot overwrite it',()=>{
  const f=fixture(); const old=f.cache.get('page','/old'); old.onerror();
  const next=f.cache.get('page','/new'); f.timers.shift()();
  assert.equal(f.images.length,2);assert.equal(next.src,'/new');
  assert.equal(f.cache.get('page','/new'),next);
});
test('evicted images do not retry in the background',()=>{
  const f=fixture(1);f.cache.get('one','/one').onerror();f.cache.get('two','/two');
  f.timers.shift()();assert.equal(f.images.length,2);
});
