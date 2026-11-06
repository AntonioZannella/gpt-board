import {decode} from './native/model.js';
const errors=[];window.addEventListener('error',e=>errors.push(e.message));
let fullPaints=0,saveBytes=0,saveHasAssets=false;
const originalFill=CanvasRenderingContext2D.prototype.fillRect;
CanvasRenderingContext2D.prototype.fillRect=function(x,y,w,h){if(this.canvas.id==='canvas'&&x===0&&y===0)fullPaints++;return originalFill.call(this,x,y,w,h)};
const originalFetch=window.fetch;window.fetch=(url,options)=>{if(url==='/api/board'&&options?.method==='POST'){saveBytes=options.body.length;saveHasAssets=options.body.includes('large-fixture')}return originalFetch(url,options)};
await import('./native/app.js');
const result=document.getElementById('audit-result'),button=document.getElementById('run-audit'),canvas=document.getElementById('canvas');
const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const assert=(value,message)=>{if(!value)throw Error(message)};
const read=async()=>{const board=await(await fetch('/api/board?notebook=default')).json();return Object.values(board.store).filter(s=>s.type==='draw'||s.type==='highlight')};
const emit=(type,x,y,id=100,options={})=>{const r=canvas.getBoundingClientRect(),{target=canvas,coalesced,...rest}=options;const e=new PointerEvent(type,{pointerType:'pen',pointerId:id,bubbles:true,cancelable:true,clientX:r.left+x,clientY:r.top+y,button:0,buttons:type==='pointerup'?0:1,pressure:type==='pointerup'?0:.5,...rest});if(coalesced!==undefined)Object.defineProperty(e,'getCoalescedEvents',{value:()=>coalesced});target.dispatchEvent(e)};
button.onclick=async()=>{button.disabled=true;try{
 const before=(await read()).length;assert(before===0,'Run with a fresh test notebook');await frame();
 let expected=0;const startPaints=fullPaints;
 // Hundreds of samples over animation frames must not repaint the PDF layer.
 emit('pointerdown',80,100);for(let i=1;i<=180;i++){emit('pointermove',80+i*2,100+Math.sin(i/12)*30,100,{coalesced:[]});if(i%6===0)await frame()}
 assert(fullPaints===startPaints,'The static page repainted during active ink');emit('pointerup',442,100);expected++;await frame();
 for(let i=0;i<100;i++){const x=70+(i%25)*16,y=170+Math.floor(i/25)*25;emit('pointerdown',x,y,200+i);emit('pointerup',x+6,y+12,200+i);expected++}
 // Tap, canceled contact, capture loss, outside release, lost initial down.
 emit('pointerdown',70,300,400);emit('pointerup',70,300,400);expected++;
 emit('pointerdown',100,300,401);emit('pointermove',120,310,401);emit('pointercancel',0,0,401);expected++;
 emit('pointerdown',140,300,402);emit('lostpointercapture',140,300,402);emit('pointermove',160,310,402,{target:window});emit('pointerup',180,300,402,{target:window});expected++;
 emit('pointermove',200,300,403);emit('pointerup',220,310,403,{target:window});expected++;
 // A later contact releases an interrupted gesture, so future strokes still work.
 emit('pointerdown',240,300,404);emit('pointermove',250,310,404);emit('pointerdown',270,300,405);emit('pointerup',280,310,405);expected+=2;
 emit('pointermove',320,300,406,{pressure:0,buttons:0}); // hover: no ink
 emit('pointerdown',330,300,407);emit('pointerdown',20,20,900,{pointerType:'touch'});emit('pointermove',390,320,900,{pointerType:'touch'});emit('pointerup',350,310,407);expected++;
 await frame();await wait(1600);
 let strokes=await read();assert(strokes.length===expected,`Expected ${expected} strokes, found ${strokes.length}`);
 assert(decode(strokes[0].props.segments[0]).length>=181,'Empty coalesced batches lost samples');
 assert(decode(strokes[1].props.segments[0]).length===2,'Short dash lost its release endpoint');
 assert(decode(strokes[101].props.segments[0]).length===1,'Tap was lost');
 assert(decode(strokes[103].props.segments[0]).at(-1).x===40,'Capture-loss endpoint was lost');
 for(let i=0;i<10;i++)document.getElementById('undo').click();await wait(1400);assert((await read()).length===expected-10,'Undo lost count');
 for(let i=0;i<10;i++)document.getElementById('redo').click();await wait(1400);assert((await read()).length===expected,'Redo lost count');
 document.activeElement.blur();window.dispatchEvent(new KeyboardEvent('keydown',{key:'a',ctrlKey:true,bubbles:true}));document.getElementById('delete').click();await wait(1400);assert((await read()).length===0,'Delete selected left strokes');document.getElementById('undo').click();await wait(1400);assert((await read()).length===expected,'Undo deletion lost strokes');
 assert(!saveHasAssets&&saveBytes<200000,'Saving uploaded embedded PDF data');assert(!errors.length,errors.join('\n'));
 result.textContent=`PASS — ${expected}/${expected} strokes preserved\nEmpty sample batches · short dashes · dots\nCapture loss · cancellation · hover · palm touch\nUndo/redo · select/delete · save/read-back\nActive-stroke page repaints: 0\nLast save: ${(saveBytes/1024).toFixed(1)} KB; PDF assets excluded`;
 }catch(e){result.textContent='FAIL — '+e.message;console.error(e)}
};
