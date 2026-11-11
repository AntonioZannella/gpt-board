import {test} from 'node:test';
import assert from 'node:assert/strict';
import {followAction} from '../src/follow.js';
const local={book:'one',kind:'notebook',frame:'a',frames:['a','b'],busy:false};
test('follows another page without a slide animation instruction',()=>{
 assert.deepEqual(followAction({notebook:'one',kind:'notebook',frame:'b'},local),{frame:'b'});
});
test('ignores missing pages and never interrupts local writing or unsaved work',()=>{
 for(const view of [null,{notebook:'one',kind:'notebook',frame:'missing'},{notebook:'one',kind:'notebook',frame:'a'}])assert.equal(followAction(view,local),null);
 assert.equal(followAction({notebook:'two',kind:'notebook',frame:'b'},{...local,busy:true}),null);
});
test('follows notebook and whiteboard switches',()=>{
 assert.deepEqual(followAction({notebook:'two',kind:'notebook'},local),{notebook:'two',white:false});
 assert.deepEqual(followAction({notebook:'white',kind:'whiteboard'},local),{notebook:'white',white:true});
});

import {tabletLaunch} from '../src/follow.js';
test('new Mac launches use the iPad notebook and page',()=>{
 assert.deepEqual(tabletLaunch({notebook:'math',kind:'notebook',frame:'shape:2'},new URLSearchParams()),{notebook:'math',section:'notebooks',page:'shape:2'});
});
test('explicit page, notebook and collection choices are preserved',()=>{
 const view={notebook:'math',kind:'notebook',frame:'shape:2'};
 for(const query of ['page=shape:1','notebook=other','section=whiteboards','open=1'])
   assert.equal(tabletLaunch(view,new URLSearchParams(query)),null);
 assert.equal(tabletLaunch(null,new URLSearchParams()),null);
 assert.equal(tabletLaunch({...view,frame:null},new URLSearchParams()),null);
 assert.equal(tabletLaunch(view,new URLSearchParams('notebook=math')).page,'shape:2');
});
