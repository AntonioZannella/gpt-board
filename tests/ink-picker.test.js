import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeInk,bindInkTap,inkColors} from '../src/ink-picker.js';
test('preset and custom colors normalize without accepting CSS or scripts',()=>{
  for(const [,color] of inkColors) assert.equal(normalizeInk(color),color);
  assert.equal(normalizeInk(' Ab12EF '),'#ab12ef');
  for(const text of ['red','#abc','url(x)','1234567',''])assert.equal(normalizeInk(text),null);
});
test('Pencil release activates once even with a synthesized click',()=>{
  const handlers={};let calls=0,time=100;
  bindInkTap({addEventListener:(type,fn)=>handlers[type]=fn},()=>calls++,()=>time);
  const event={pointerType:'pen',button:0,preventDefault(){}};
  handlers.pointerup(event);handlers.click(event);assert.equal(calls,1);
  time=700;handlers.click(event);assert.equal(calls,2);
});
test('mouse and touch clicks work, while non-primary pen releases do not activate',()=>{
  const handlers={};let calls=0;
  bindInkTap({addEventListener:(type,fn)=>handlers[type]=fn},()=>calls++,()=>100);
  for(const pointerType of ['mouse','touch']) {
    handlers.pointerup({pointerType,button:0});handlers.click({});
  }
  handlers.pointerup({pointerType:'pen',button:2});assert.equal(calls,2);
});
