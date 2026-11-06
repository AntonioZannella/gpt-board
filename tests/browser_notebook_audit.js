// Disposable browser fixture: no real notebook or review data is used.
const key='gpt-board:native-view:default',phaseKey='gpt-board:notebook-audit-phase';
const phase=JSON.parse(sessionStorage.getItem(phaseKey)||'null');
if(phase?.kind==='storage-blocked'){
 const originalSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k===key)throw new DOMException('Storage unavailable','QuotaExceededError');return originalSet.call(this,k,v)};
}
if(!phase)localStorage.setItem(key,JSON.stringify({center:500,top:1600,zoom:1.4,frame:'removed-legacy-frame'}));
const errors=[];window.addEventListener('error',e=>errors.push(e.message));
await import('./native/app.js');
const canvas=document.getElementById('canvas'),result=document.getElementById('audit-result'),button=document.getElementById('run-audit');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const tick=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
const assert=(value,message)=>{if(!value)throw Error(message)};
const view=()=>JSON.parse(localStorage.getItem(key));
const read=async()=>Object.values((await(await fetch('/api/board?notebook=default')).json()).store).filter(s=>s.type==='draw');
const emit=(type,x,y,id=100,options={})=>{const r=canvas.parentElement.getBoundingClientRect();canvas.dispatchEvent(new PointerEvent(type,{pointerType:'pen',pointerId:id,bubbles:true,cancelable:true,clientX:r.left+x,clientY:r.top+y,button:0,buttons:type==='pointerup'?0:1,pressure:type==='pointerup'?0:.5,...options}))};
function stylus(type,x,y,id=700){const r=canvas.parentElement.getBoundingClientRect(),e=new Event(type,{bubbles:true,cancelable:true});Object.defineProperty(e,'changedTouches',{value:[{identifier:id,touchType:'stylus',clientX:r.left+x,clientY:r.top+y,force:.5}]});canvas.dispatchEvent(e);assert(e.defaultPrevented,'Safari touch default was not prevented')}
function swipe(dx,dy=0,id=800){const x=canvas.clientWidth/2,y=canvas.clientHeight/2;emit('pointerdown',x,y,id,{pointerType:'touch'});emit('pointermove',x+dx,y+dy,id,{pointerType:'touch'});emit('pointerup',x+dx,y+dy,id,{pointerType:'touch'})}
function wheel(dx,dy){canvas.dispatchEvent(new WheelEvent('wheel',{deltaX:dx,deltaY:dy,bubbles:true,cancelable:true}))}
function sameView(a,b,message){assert(a.frame===b.frame&&Math.abs(a.top-b.top)<.01&&Math.abs(a.center-b.center)<.01&&Math.abs(a.zoom-b.zoom)<.001,message)}
async function afterReload(){
 await tick();
 if(phase.kind==='complete'){result.textContent=phase.result;button.disabled=true;return}
 if(phase.kind==='storage-blocked'){
  assert(document.getElementById('pages').value===phase.page,'Reload lost page when browser storage failed');
  assert(new URL(location.href).searchParams.get('page')===phase.page,'Address lost the saved page');
  assert((await read()).length===phase.count,'Storage fallback changed handwriting');
  assert(!errors.length,errors.join('\n'));
  result.textContent=`PASS — ${phase.count}/${phase.count} strokes saved and restored\nReload with normal and blocked browser storage\nAddress page overrides stale local storage\nFinger-following slide, animated turn, and canceled swipe\nPinch/pen interruptions remove animation layers\nPage/zoom/pan restore, palm rejection, and single-page bounds`;
  button.disabled=true;sessionStorage.setItem(phaseKey,JSON.stringify({kind:'complete',result:result.textContent}));return;
 }
 sameView(view(),phase.view,'Reload lost notebook page/position/zoom');
 assert((await read()).length===phase.count,'Reload lost handwriting');
 assert(!errors.length,errors.join('\n'));
 // Reproduce the failure mode: persistence silently fails, leaving an older
 // page in storage. The current address must still restore the correct page.
 localStorage.setItem(key,JSON.stringify({...view(),frame:'shape:sheet-1',top:0}));
 const originalSet=Storage.prototype.setItem;
 Storage.prototype.setItem=function(k,v){if(k===key)throw new DOMException('Storage unavailable','QuotaExceededError');return originalSet.call(this,k,v)};
 document.getElementById('next').click();
 assert(new URL(location.href).searchParams.get('page')==='shape:sheet-3','Page was not saved to the address');
 sessionStorage.setItem(phaseKey,JSON.stringify({kind:'storage-blocked',page:'shape:sheet-3',count:phase.count}));
 location.reload();
}
if(phase){afterReload().catch(e=>{result.textContent='FAIL — '+e.message;console.error(e)})}
else button.onclick=async()=>{button.disabled=true;try{
 await tick();assert(view().frame==='shape:sheet-2','Legacy saved position opened the wrong page');
 assert(getComputedStyle(document.getElementById('status')).webkitUserSelect==='none','Status permits Safari text selection');
 assert((await read()).length===0,'Use a fresh fixture');
 // Text selected by an earlier browser gesture must be released on pen down.
 window.getSelection().selectAllChildren(document.getElementById('status'));
 const x=canvas.clientWidth/2,y=canvas.clientHeight*.4;
 emit('pointerdown',x,y,100);assert(window.getSelection().isCollapsed,'Selection survived pen contact');emit('pointerup',x+10,y+8,100);
 for(let i=0;i<100;i++){emit('pointerdown',x+(i%10)*2,y+20+Math.floor(i/10)*3,200+i);emit('pointerup',x+5+(i%10)*2,y+25+Math.floor(i/10)*3,200+i)}
 // Safari contact recovery must not require a reliable buttons bit.
 emit('pointermove',x,y+80,500,{buttons:0,pressure:.6});emit('pointerup',x+20,y+85,500);
 emit('pointermove',x,y+90,501,{buttons:0,pressure:0}); // hover stays ignored
 stylus('touchstart',x,y+100);stylus('touchmove',x+12,y+108);stylus('touchend',x+20,y+110);
 // Pointer first: touch events must not duplicate ink.
 emit('pointerdown',x,y+120,502);stylus('touchstart',x,y+120,701);emit('pointermove',x+10,y+128,502);stylus('touchmove',x+10,y+128,701);emit('pointerup',x+20,y+130,502);stylus('touchend',x+20,y+130,701);
 // Touch first: transfer the existing gesture to the pointer stream.
 stylus('touchstart',x,y+140,702);emit('pointerdown',x,y+140,503);emit('pointermove',x+10,y+148,503);emit('pointerup',x+20,y+150,503);stylus('touchend',x+20,y+150,702);
 await wait(1500);let count=105;assert((await read()).length===count,'Missed or duplicate pen strokes');
 // A finger that was panning before Pencil contact must not turn the page
 // when the palm finally lifts. The pen commits once on this contact.
 document.getElementById('fit').click();await wait(150);
 const palmX=canvas.clientWidth/2,palmY=canvas.clientHeight/2;
 emit('pointerdown',palmX,palmY,950,{pointerType:'touch'});
 emit('pointermove',palmX-130,palmY,950,{pointerType:'touch'});
 emit('pointerdown',palmX,palmY,951);
 emit('pointerup',palmX-130,palmY,950,{pointerType:'touch'});
 assert(view().frame==='shape:sheet-2','Palm release turned the page during writing');
 emit('pointerup',palmX+10,palmY+10,951);
 await wait(1300);assert((await read()).length===count+1,'Palm interruption lost the pen stroke');count++;

 document.getElementById('fit').click();await tick();
 // Move the finger without releasing: both sheets should visibly translate.
 const sx=canvas.clientWidth/2,sy=canvas.clientHeight/2;
 emit('pointerdown',sx,sy,810,{pointerType:'touch'});emit('pointermove',sx-140,sy,810,{pointerType:'touch'});await tick();
 assert(canvas.style.transform.includes('-140'),'Page did not follow finger');
 assert(document.querySelectorAll('.page-motion').length===1,'Adjacent preview missing');
 assert(view().frame==='shape:sheet-2','Drag committed before release');
 emit('pointerup',sx-140,sy,810,{pointerType:'touch'});
 assert(view().frame==='shape:sheet-3','Swipe left did not turn one page');
 assert(canvas.getAnimations().length>0,'Page turn did not animate');
 await wait(320);assert(!document.querySelector('.page-motion')&&!canvas.style.transform,'Completed animation left a layer');
 swipe(-140);assert(view().frame==='shape:sheet-3','Swiping last page wrapped');
 swipe(140);assert(view().frame==='shape:sheet-2','Swipe right did not return');await wait(320);
 // Short drag cancels and snaps back. A pinch cancels the slide immediately.
 swipe(-35);assert(view().frame==='shape:sheet-2','Short drag turned a page');await wait(260);
 assert(!document.querySelector('.page-motion')&&!canvas.style.transform,'Canceled swipe left a transformed canvas');
 emit('pointerdown',sx,sy,820,{pointerType:'touch'});emit('pointermove',sx-90,sy,820,{pointerType:'touch'});await tick();
 emit('pointerdown',sx+80,sy,821,{pointerType:'touch'});
 assert(!document.querySelector('.page-motion')&&!canvas.style.transform,'Pinch did not interrupt page slide');
 emit('pointerup',sx-90,sy,820,{pointerType:'touch'});emit('pointerup',sx+80,sy,821,{pointerType:'touch'});
 document.getElementById('fit').click();
 // A vertical finger drag must not leave the fitted page between sheets.
 const fitted=view();swipe(0,-180);sameView(fitted,view(),'Vertical drag exposed a gap between pages');
 // Pinch out is navigation, never a page turn.
 const px=canvas.clientWidth/2,py=canvas.clientHeight/2;
 emit('pointerdown',px-50,py,900,{pointerType:'touch'});emit('pointerdown',px+50,py,901,{pointerType:'touch'});
 emit('pointermove',px-100,py,900,{pointerType:'touch'});emit('pointermove',px+100,py,901,{pointerType:'touch'});
 emit('pointerup',px-100,py,900,{pointerType:'touch'});emit('pointerup',px+100,py,901,{pointerType:'touch'});
 assert(view().frame==='shape:sheet-2'&&view().zoom>1.5,'Pinch failed or changed page');
 document.getElementById('plus').click();wheel(0,160);const moved=view();assert(moved.top!==fitted.top,'Zoomed page did not pan');
 const main=document.querySelector('main');main.style.display='none';await tick();sameView(moved,view(),'Hidden canvas overwrote view');main.style.display='';await tick();sameView(moved,view(),'Showing canvas lost view');
 const beforeResize=view();document.body.style.height='90vh';await tick();document.body.style.height='';await tick();sameView(beforeResize,view(),'Viewport resize lost position');
 assert(!errors.length,errors.join('\n'));
 sessionStorage.setItem(phaseKey,JSON.stringify({kind:'normal',view:view(),count}));
 result.textContent='Checking saved view after reload…';location.reload();
 }catch(e){result.textContent='FAIL — '+e.message;console.error(e)}};

const demo=document.createElement('button');demo.textContent='Preview page slide';result.after(demo);
demo.onclick=async()=>{
 document.getElementById('fit').click();await tick();
 const x=canvas.clientWidth/2,y=canvas.clientHeight/2;
 emit('pointerdown',x,y,990,{pointerType:'touch'});emit('pointermove',x+canvas.clientWidth*.45,y,990,{pointerType:'touch'});
 await wait(4000);emit('pointerup',x+canvas.clientWidth*.45,y,990,{pointerType:'touch'});
};
