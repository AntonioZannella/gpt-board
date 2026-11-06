import {clone,palette,decode,encode,plain,rich,transform,apply,inverse,strokePoints,bounds,intersects,distance,cut,penSamples,inkPath} from './model.js';
const $=id=>document.getElementById(id),canvas=$('canvas'),ctx=canvas.getContext('2d'),params=new URLSearchParams(location.search),tab=crypto.randomUUID?.()||Math.random().toString(36).slice(2);
const readLocal=key=>{try{return localStorage.getItem(key)}catch{return null}};
const writeLocal=(key,value)=>{try{localStorage.setItem(key,value)}catch{}};
const resumeKey='gpt-board:last-session:v1',resumeCookie='gpt_board_resume_'+(location.port||location.protocol.replace(':',''));
let rememberedBoard=null;
function readResume(){
 let cookie=null;try{cookie=document.cookie.split('; ').find(row=>row.startsWith(resumeCookie+'='))?.slice(resumeCookie.length+1)}catch{}
 for(const value of [cookie?decodeCookie(cookie):null,readLocal(resumeKey)]){
  try{const record=JSON.parse(value);if(record&&['notebook','whiteboard'].includes(record.kind)&&typeof record.id==='string'&&/^[a-z0-9][a-z0-9-]{0,63}$/.test(record.id))return record}catch{}
 }
 return null;
}
function decodeCookie(value){try{return decodeURIComponent(value)}catch{return null}}
const resume=readResume();
const standalone=navigator.standalone===true||window.matchMedia('(display-mode: standalone)').matches;
const navigationType=performance.getEntriesByType('navigation')[0]?.type;
const resumeLaunch=standalone&&resume&&params.get('open')!=='1'&&!['reload','back_forward'].includes(navigationType);
// A Home Screen icon keeps its original launch URL. On a new app launch use
// the last session instead; in-app selections carry an explicit one-shot flag.
if(resumeLaunch){params.delete('notebook');params.delete('section');params.delete('page')}
const explicitLocation=params.has('notebook')||params.has('section');
const isWhiteboard=params.get('section')==='whiteboards'||(!explicitLocation&&resume?.kind==='whiteboard'),kind=isWhiteboard?'whiteboard':'notebook';

const inkCanvas=$('ink-canvas'),inkCtx=inkCanvas.getContext('2d'),pathCache=new WeakMap();
const ERASER_RADIUS=12,eraserCursor=document.createElement('div');
eraserCursor.id='eraser-cursor';eraserCursor.hidden=true;eraserCursor.setAttribute('aria-hidden','true');
eraserCursor.style.width=eraserCursor.style.height=`${ERASER_RADIUS*2}px`;
canvas.parentElement.append(eraserCursor);
function hideEraserCursor(){eraserCursor.hidden=true}
function updateEraserCursor(e){
 if(tool!=='eraser'||e.pointerType==='touch'||pageMotion){hideEraserCursor();return}
 const p=screen(e);eraserCursor.hidden=p.x<0||p.y<0||p.x>W||p.y>H;
 eraserCursor.style.left=`${p.x}px`;eraserCursor.style.top=`${p.y}px`;
}

let sceneDirty=true,pendingInk=[],penQuietUntil=0,persistedShapes=new Map();
let nativePenContact=false,stylusTouch=null;
let book=params.get('notebook'),viewKey,viewReady=false,activeFrameId=null,pageSwipe=null,pageMotion=null;
const rememberKey='gpt-board:last:'+kind;

function rememberBoard(force=false){
 if(!viewReady||!book)return;
 const value=JSON.stringify({id:book,kind,page:current()?.id||null});
 if(!force&&rememberedBoard===value)return;rememberedBoard=value;
 writeLocal(rememberKey,book);writeLocal(resumeKey,value);
 // A tiny first-party cookie backs up the selection when Safari storage fails.
 // It contains only a board ID and collection, never notebook content or tokens.
 try{document.cookie=resumeCookie+'='+encodeURIComponent(value)+'; Path=/; Max-Age=31536000; SameSite=Strict'+(location.protocol==='https:'?'; Secure':'')}catch{}
}
const boardURL=(id,white=isWhiteboard,explicit=true)=>'?'+new URLSearchParams({... (id?{notebook:id}:{}),...(white?{section:'whiteboards'}:explicit?{section:'notebooks'}:{}),...(explicit?{open:'1'}:{})});
let snapshot,store,shapes=[],frames=[],cache=new Map(),images=new Map(),selected=new Set(),history=[],future=[],tool='pen',camera={x:0,y:0,z:1},W=0,H=0,base=1,raf=0,gesture=null,touches=new Map(),touchStart=null,touchRaf=0,penActive=false,dirty=false,saveTimer,saveChain=Promise.resolve(),connected=false,revision=0;
const say=text=>$('status').textContent=text;
const uid=()=> 'shape:'+(crypto.randomUUID?.()||Math.random().toString(36).slice(2));
const record=s=>({id:uid(),typeName:'shape',type:'draw',parentId:Object.values(store).find(r=>r.typeName==='page').id,index:'b'+Date.now().toString(36)+Math.random().toString(36).slice(2,6),x:0,y:0,rotation:0,isLocked:false,opacity:1,meta:{},...s});
const getBounds=s=>{let b=cache.get(s.id);if(!b){b=bounds(s,store);cache.set(s.id,b)}return b};
const locked=s=>{let seen=new Set();while(s&&!seen.has(s.id)){if(s.isLocked)return true;seen.add(s.id);s=store[s.parentId]}return false};
const editable=s=>!['frame','image'].includes(s.type)&&!locked(s);
function rebuild(){cache.clear();shapes=Object.values(store).filter(s=>s.typeName==='shape');const depth=s=>{let n=0;while(store[s.parentId]?.typeName==='shape'&&n<30){n++;s=store[s.parentId]}return n};shapes.sort((a,b)=>depth(a)-depth(b)||(String(a.index)<String(b.index)?-1:String(a.index)>String(b.index)?1:0));frames=shapes.filter(s=>s.type==='frame').sort((a,b)=>getBounds(a).y-getBounds(b).y);$('pages').replaceChildren(...frames.map(f=>new Option(f.props.name,f.id)));for(const id of selected)if(!store[id])selected.delete(id);updateUI();schedule()}
function checkpoint(){history.push(shapes.map(clone));if(history.length>60)history.shift();future=[]}
function restore(list){for(const s of shapes)delete store[s.id];for(const s of list)store[s.id]=s;selected.clear();changed()}
function undo(){finishActive();if(!history.length)return;const entry=history.pop();if(entry.kind==='ink'){future.push(entry);delete store[entry.shape.id];changed()}else{future.push(shapes.map(clone));restore(entry)}}
function redo(){finishActive();if(!future.length)return;const entry=future.pop();if(entry.kind==='ink'){history.push(entry);store[entry.shape.id]=clone(entry.shape);changed()}else{history.push(shapes.map(clone));restore(entry)}}
function markDirty(){dirty=true;revision++;clearTimeout(saveTimer);saveTimer=setTimeout(()=>save().catch(()=>{}),1000)}
function changed(){markDirty();rebuild()}
function current(){return frames.find(f=>f.id===activeFrameId)||frames[0]}
function updateUI(){const f=current(),i=frames.indexOf(f);if(f)$('pages').value=f.id;$('count').textContent=frames.length?`${i+1} / ${frames.length}`:'Scratch paper';$('page-indicator').textContent=!isWhiteboard&&frames.length?`Page ${i+1} of ${frames.length} · Swipe left or right`:'';$('zoom').textContent=Math.round(camera.z/base*100)+'%';$('delete').disabled=!selected.size;$('delete').textContent=selected.size?'Delete selected ('+selected.size+')':'Delete selected';$('undo').disabled=!history.length;$('redo').disabled=!future.length;$('prev').disabled=i<=0;$('next').disabled=i>=frames.length-1}
function clampCamera(){
 if(isWhiteboard||!frames.length){base=1;camera.z=Math.max(.1,Math.min(8,camera.z));return}
 const b=getBounds(current()),margin=20;
 base=Math.max(.05,Math.min((W-margin*2)/b.w,(H-margin*2)/b.h));
 camera.z=Math.max(base,Math.min(base*6,camera.z));
 const z=camera.z;
 camera.x=b.w<(W-margin*2)/z?(W/z-b.w)/2-b.x:Math.max((W-margin)/z-b.x-b.w,Math.min(margin/z-b.x,camera.x));
 camera.y=b.h<(H-margin*2)/z?(H/z-b.h)/2-b.y:Math.max((H-margin)/z-b.y-b.h,Math.min(margin/z-b.y,camera.y));
}
// Page transitions are visual only; frame records and ink coordinates stay put.
function resetPageMotion(){
 const old=pageMotion;pageMotion=null;
 if(old){for(const animation of old.animations||[])animation.cancel();old.layer?.remove()}
 canvas.style.transform='';inkCanvas.style.transform='';
}
function motionLayer(){
 const layer=document.createElement('canvas');layer.className='page-motion';
 layer.width=canvas.width;layer.height=canvas.height;
 layer.setAttribute('aria-hidden','true');canvas.parentElement.append(layer);
 return layer;
}
function fittedCamera(frame){
 const b=getBounds(frame),z=Math.max(.05,Math.min((W-40)/b.w,(H-40)/b.h));
 return {x:(W/z-b.w)/2-b.x,y:(H/z-b.h)/2-b.y,z};
}
function drawPagePreview(motion){
 if(!motion?.target||motion.phase!=='drag')return;
 const cam=fittedCamera(motion.target);
 render(motion.layer.getContext('2d'),{x:-cam.x,y:-cam.y,w:W/cam.z,h:H/cam.z},cam.z,canvas.width/W,true,motion.target,true);
}
function pageDistance(next){
 if(!next)return W;
 return Math.min(W,(getBounds(current()).w*camera.z+getBounds(next).w*fittedCamera(next).z)/2+24);
}
function swipeAllowed(swipe,dx){
 if(isWhiteboard||!swipe||!frames.length)return false;
 const b=getBounds(current()),z=swipe.camera.z;
 return swipe.zoom<=1.02||(dx<0?(b.x+b.w+swipe.camera.x)*z<=W+2:(b.x+swipe.camera.x)*z>=-2);
}
function dragPage(swipe){
 const dx=swipe.last.x-swipe.start.x,dy=swipe.last.y-swipe.start.y;
 if(!pageMotion&&(Math.abs(dx)<12||Math.abs(dx)<Math.abs(dy)*1.4||!swipeAllowed(swipe,dx)))return false;
 const direction=dx<0?1:-1;
 if(pageMotion?.phase!=='drag'||pageMotion.direction!==direction){
  resetPageMotion();
  const target=frames[frames.indexOf(current())+direction];
  pageMotion={phase:'drag',direction,target,distance:pageDistance(target),layer:target?motionLayer():null,offset:0};
  drawPagePreview(pageMotion);
 }
 const motion=pageMotion;
 motion.offset=motion.target?Math.max(-motion.distance,Math.min(motion.distance,dx)):Math.max(-70,Math.min(70,dx*.22));
 canvas.style.transform=inkCanvas.style.transform=`translateX(${motion.offset}px)`;
 if(motion.layer)motion.layer.style.transform=`translateX(${direction*motion.distance+motion.offset}px)`;
 return true;
}
function settleMotion(motion,committed){
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 if(reduced||typeof canvas.animate!=='function'){resetPageMotion();return}
 const duration=committed?240:180,options={duration,easing:'cubic-bezier(.22,.7,.25,1)',fill:'forwards'};
 const start=committed?motion.direction*motion.distance+motion.offset:motion.offset;
 motion.animations=[canvas.animate([{transform:`translateX(${start}px)`},{transform:'translateX(0)'}],options)];
 if(motion.layer){
  const from=committed?motion.offset:motion.direction*motion.distance+motion.offset;
  const to=committed?-motion.direction*motion.distance:motion.direction*motion.distance;
  motion.animations.push(motion.layer.animate([{transform:`translateX(${from}px)`},{transform:`translateX(${to}px)`}],options));
 }
 motion.phase='settle';
 Promise.all(motion.animations.map(a=>a.finished)).catch(()=>{}).then(()=>{if(pageMotion===motion)resetPageMotion()});
}
function showPage(id,animate=true){
 const next=frames.find(f=>f.id===id);if(!next)return;
 const direction=Math.sign(frames.indexOf(next)-frames.indexOf(current()));
 if(!animate||!direction){resetPageMotion();fit(id);return}
 finishActive();
 const offset=pageMotion?.phase==='drag'&&pageMotion.target===next?pageMotion.offset:0;
 // Copy the outgoing sheet before changing the selected page. The main canvas
 // renders the destination immediately, so pen input can safely interrupt motion.
 const distance=pageDistance(next),layer=motionLayer(),dpr=canvas.width/W,b=getBounds(current());
 render(ctx,{x:-camera.x,y:-camera.y,w:W/camera.z,h:H/camera.z},camera.z,dpr,true);
 const x=Math.max(0,((b.x+camera.x)*camera.z-16)*dpr),y=Math.max(0,((b.y+camera.y)*camera.z-32)*dpr);
 const right=Math.min(canvas.width,((b.x+b.w+camera.x)*camera.z+16)*dpr),bottom=Math.min(canvas.height,((b.y+b.h+camera.y)*camera.z+16)*dpr);
 if(right>x&&bottom>y)layer.getContext('2d').drawImage(canvas,x,y,right-x,bottom-y,x,y,right-x,bottom-y);
 resetPageMotion();
 const motion={phase:'settle',direction,target:next,layer,offset,distance};pageMotion=motion;
 fit(id);
 render(ctx,{x:-camera.x,y:-camera.y,w:W/camera.z,h:H/camera.z},camera.z,canvas.width/W,true);
 inkCtx.clearRect(0,0,inkCanvas.width,inkCanvas.height);
 layer.style.transform=`translateX(${offset}px)`;
 canvas.style.transform=`translateX(${direction*distance+offset}px)`;
 settleMotion(motion,true);
}
function turnPage(delta){
 if(isWhiteboard)return;
 const next=frames[frames.indexOf(current())+delta];
 if(next)showPage(next.id);
 else if(pageMotion)settleMotion(pageMotion,false);
}
function viewChanged(){
 hideEraserCursor();
 if(!W||!H)return;
 clampCamera();updateUI();schedule();saveView();
}
// Save at each camera change: iPad Safari may suspend a tab before a timer runs.
function readView(){
 try{
  const saved=JSON.parse(readLocal(viewKey));
  if(saved)return saved;
  const old=JSON.parse(readLocal('gpt-board:view:v1:'+encodeURIComponent(book)));
  return old?{center:old.centerX,top:old.topY,zoom:old.zoom,frame:old.frameId}:null;
 }catch{return null}
}
function saveView(){
 if(!viewReady||!store||!viewKey||!W||!H)return;
 rememberBoard();
 const view={center:W/(2*camera.z)-camera.x,top:-camera.y,zoom:camera.z/base,scale:camera.z,mode:'single-page',frame:current()?.id};
 if([view.center,view.top,view.zoom].every(Number.isFinite))writeLocal(viewKey,JSON.stringify(view));
 if(!isWhiteboard){
  const url=new URL(location.href);
  if(url.searchParams.get('page')!==(view.frame||null)||url.searchParams.get('notebook')!==book||url.searchParams.has('open')||url.searchParams.has('section')){
   url.searchParams.delete('open');url.searchParams.delete('section');url.searchParams.set('notebook',book);if(view.frame)url.searchParams.set('page',view.frame);else url.searchParams.delete('page');
   try{window.history.replaceState(null,'',url)}catch{}
  }
 }

}
function fit(id){if(isWhiteboard){const b=allBounds(),z=shapes.length?Math.max(.1,Math.min(2,(W-80)/Math.max(1,b.w),(H-80)/Math.max(1,b.h))):1;camera={x:W/(2*z)-b.x-b.w/2,y:H/(2*z)-b.y-b.h/2,z};return viewChanged()}const f=store[id]||frames[0];activeFrameId=f?.id||null;clampCamera();if(!f){camera={x:0,y:0,z:1};return viewChanged()}const b=getBounds(f);camera={x:(W/base-b.w)/2-b.x,y:24/base-b.y,z:base};viewChanged()}
function zoom(factor,at={x:W/2,y:H/2}){const z=Math.max((!isWhiteboard&&frames.length)?base:.1,Math.min((!isWhiteboard&&frames.length)?base*6:8,camera.z*factor));camera={x:camera.x+at.x/z-at.x/camera.z,y:camera.y+at.y/z-at.y/camera.z,z};viewChanged()}
function screen(e){const r=canvas.parentElement.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top,z:e.pressure||.5}}
const world=p=>({x:p.x/camera.z-camera.x,y:p.y/camera.z-camera.y,z:p.z});
function resize(){
 resetPageMotion();
 const width=canvas.clientWidth,height=canvas.clientHeight;
 // Hidden/background tabs can temporarily report a zero-size canvas. Never
 // replace the last usable camera with that layout's coordinates.
 if(!width||!height)return;
 const center=W?W/(2*camera.z)-camera.x:null,top=-camera.y,relative=camera.z/base;
 W=width;H=height;
 const dpr=Math.min(3,devicePixelRatio||1);
 canvas.width=Math.round(W*dpr);canvas.height=Math.round(H*dpr);
 inkCanvas.width=canvas.width;inkCanvas.height=canvas.height;
 clampCamera();camera.z=relative*base;
 if(center!==null){camera.x=W/(2*camera.z)-center;camera.y=-top}
 viewChanged();
}
function canvasTransform(c){const dpr=canvas.width/W;c.setTransform(dpr*camera.z,0,0,dpr*camera.z,dpr*camera.z*camera.x,dpr*camera.z*camera.y)}
function schedule(full=true){sceneDirty=sceneDirty||full!==false;if(!raf)raf=requestAnimationFrame(()=>{
 raf=0;if(!W||!H)return;
 if(sceneDirty){render(ctx,{x:-camera.x,y:-camera.y,w:W/camera.z,h:H/camera.z},camera.z,canvas.width/W,true);sceneDirty=false}
 else if(pendingInk.length){canvasTransform(ctx);for(const s of pendingInk)if(store[s.id])paintWorld(ctx,s)}
 pendingInk=[];inkCtx.setTransform(1,0,0,1,0,0);inkCtx.clearRect(0,0,inkCanvas.width,inkCanvas.height);
 if(gesture?.kind==='ink'){canvasTransform(inkCtx);paintWorld(inkCtx,gesture.shape,gesture.pts)}
})}
function imageFor(s){const asset=store[s.props.assetId],src=asset?.props?.src;if(!src)return null;if(!images.has(s.props.assetId)){const im=new Image();im.onload=()=>{schedule();drawPagePreview(pageMotion)};im.onerror=()=>say('An image could not load');im.src=src;images.set(s.props.assetId,im);while(images.size>12)images.delete(images.keys().next().value)}return images.get(s.props.assetId)}
function paintShape(c,s,livePoints){const p=s.props,m=transform(s,store);c.save();c.transform(...m);c.globalAlpha=s.opacity??1;c.lineJoin='round';c.lineCap='round';c.strokeStyle=s.meta?.nativeColor||palette[p.color]||p.color||'#171923';c.fillStyle=c.strokeStyle;
 if(s.type==='frame'){c.fillStyle='#fff';c.fillRect(0,0,p.w,p.h)}
 else if(s.type==='image'){const im=imageFor(s);if(im?.complete&&im.naturalWidth){if(p.flipX){c.translate(p.w,0);c.scale(-1,1)}if(p.flipY){c.translate(0,p.h);c.scale(1,-1)}const crop=p.crop;if(crop){const a=crop.topLeft,b=crop.bottomRight;c.drawImage(im,a.x*im.naturalWidth,a.y*im.naturalHeight,(b.x-a.x)*im.naturalWidth,(b.y-a.y)*im.naturalHeight,0,0,p.w,p.h)}else c.drawImage(im,0,0,p.w,p.h)}}
 else if(['draw','highlight'].includes(s.type)){const highlight=s.type==='highlight',width=(p.nativeWidth||({s:2,m:4,l:7,xl:11}[p.size]||2))*(highlight?5:1);if(highlight)c.globalAlpha*=.3;
  if(livePoints)c.fill(inkPath(livePoints,width,p.isPen&&!highlight));
  else {const key=[width,p.isPen,p.scaleX,p.scaleY].join(':');let cached=pathCache.get(p.segments);if(!cached||cached.key!==key){cached={key,paths:strokePoints(s).map(pts=>inkPath(pts,width,p.isPen&&!highlight))};pathCache.set(p.segments,cached)}for(const path of cached.paths)c.fill(path)}
 }
 else if(s.type==='arrow'){let a=p.start,b=p.end;c.lineWidth=2;c.beginPath();c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);c.stroke();let ang=Math.atan2(b.y-a.y,b.x-a.x);c.beginPath();c.moveTo(b.x,b.y);c.lineTo(b.x-12*Math.cos(ang-.45),b.y-12*Math.sin(ang-.45));c.lineTo(b.x-12*Math.cos(ang+.45),b.y-12*Math.sin(ang+.45));c.closePath();c.fill()}
 else {const w=p.w||200,h=p.h||100;if(s.type!=='text'){c.lineWidth=2;c.beginPath();if(p.geo==='ellipse')c.ellipse(w/2,h/2,w/2,h/2,0,0,Math.PI*2);else if(p.geo==='triangle'){c.moveTo(w/2,0);c.lineTo(w,h);c.lineTo(0,h);c.closePath()}else if(p.geo==='diamond'){c.moveTo(w/2,0);c.lineTo(w,h/2);c.lineTo(w/2,h);c.lineTo(0,h/2);c.closePath()}else if(['star','hexagon'].includes(p.geo)){const n=p.geo==='star'?10:6;for(let i=0;i<n;i++){const r=p.geo==='star'&&i%2?.45:1,angle=-Math.PI/2+i*Math.PI*2/n,x=w/2+Math.cos(angle)*w/2*r,y=h/2+Math.sin(angle)*h/2*r;i?c.lineTo(x,y):c.moveTo(x,y)}c.closePath()}else if(p.geo==='cloud'){c.moveTo(0,h*.6);c.bezierCurveTo(-w*.1,h*.2,w*.2,0,w*.35,h*.2);c.bezierCurveTo(w*.5,-h*.2,w*.85,0,w*.85,h*.3);c.bezierCurveTo(w*1.1,h*.3,w*1.1,h*.9,w*.8,h*.9);c.bezierCurveTo(w*.5,h*1.1,0,h,0,h*.6)}else c.rect(0,0,w,h);if(s.type==='note'){c.fillStyle='#fff1a8';c.fill();c.fillStyle='#171923'}else if(p.fill&&p.fill!=='none'){c.save();c.globalAlpha*=.15;c.fill();c.restore()}c.stroke()}const size={s:18,m:24,l:32,xl:44}[p.size]||18;c.font=`${size}px system-ui`;c.textBaseline='top';let y=8;for(const paragraph of plain(p.richText||p.text).split('\n')){let line='';for(const word of paragraph.split(' ')){if(line&&c.measureText(line+' '+word).width>w-16){c.fillText(line,8,y);y+=size*1.3;line=word}else line+=(line?' ':'')+word}c.fillText(line,8,y);y+=size*1.3}}
 c.restore()}
function paintWorld(c,s,livePoints){c.save();let parent=store[s.parentId];while(parent?.typeName==='shape'){if(parent.type==='frame'){let b=getBounds(parent);c.beginPath();c.rect(b.x,b.y,b.w,b.h);c.clip()}parent=store[parent.parentId]}paintShape(c,s,livePoints);c.restore()}
function render(c,box,scale,dpr=1,overlay=false,pageTarget=current(),transparent=false){c.setTransform(dpr,0,0,dpr,0,0);c.fillStyle=overlay&&!isWhiteboard?'#cbd2dc':'white';if(transparent)c.clearRect(0,0,box.w*scale,box.h*scale);else c.fillRect(0,0,box.w*scale,box.h*scale);c.scale(scale,scale);c.translate(-box.x,-box.y);if(overlay&&isWhiteboard){const spacing=32*2**Math.max(0,Math.ceil(Math.log2(16/scale/32)));c.fillStyle='#d6dfe9';for(let x=Math.ceil(box.x/spacing)*spacing;x<box.x+box.w;x+=spacing)for(let y=Math.ceil(box.y/spacing)*spacing;y<box.y+box.h;y+=spacing){c.beginPath();c.arc(x,y,.8/scale,0,Math.PI*2);c.fill()}}if(overlay&&!isWhiteboard){
 for(const [index,f] of frames.entries()){
  if(f!==pageTarget)continue;
  const b=getBounds(f);
  if(!intersects({x:b.x-20/scale,y:b.y-35/scale,w:b.w+40/scale,h:b.h+65/scale},box))continue;
  c.save();c.shadowColor='#1c2b4540';c.shadowBlur=12*scale;c.shadowOffsetY=3*scale;
  c.fillStyle='#fff';c.fillRect(b.x,b.y,b.w,b.h);c.restore();
  c.strokeStyle='#8d99aa';c.lineWidth=1/scale;c.strokeRect(b.x-.5/scale,b.y-.5/scale,b.w+1/scale,b.h+1/scale);
  c.fillStyle='#39485c';c.font=`${12/scale}px system-ui`;c.textBaseline='bottom';
  c.fillText(`Page ${index+1} of ${frames.length}`,b.x+4/scale,b.y-8/scale);
 }
}
const page=overlay&&!isWhiteboard?pageTarget:null;
if(page){const b=getBounds(page);c.save();c.beginPath();c.rect(b.x,b.y,b.w,b.h);c.clip()}
for(const s of shapes){
 if(page){
  if(s.type==='frame'&&s!==page)continue;
  let parent=store[s.parentId],belongs=true;
  while(parent?.typeName==='shape'){if(parent.type==='frame'&&parent!==page){belongs=false;break}parent=store[parent.parentId]}
  if(!belongs)continue;
 }
 const b=getBounds(s);if(!intersects({...b,x:b.x-15,y:b.y-15,w:b.w+30,h:b.h+30},box))continue;paintWorld(c,s)}if(page)c.restore();if(overlay){c.strokeStyle='#2688dc';c.lineWidth=1.5/scale;c.setLineDash([5/scale,4/scale]);for(const id of selected){const s=store[id];if(s){const b=getBounds(s);c.strokeRect(b.x-4/scale,b.y-4/scale,b.w+8/scale,b.h+8/scale)}}if(gesture?.kind==='marquee'){const a=gesture.start,b=gesture.last;c.strokeRect(Math.min(a.x,b.x),Math.min(a.y,b.y),Math.abs(a.x-b.x),Math.abs(a.y-b.y))}c.setLineDash([])}}
function hit(p){return [...shapes].reverse().find(s=>{if(!editable(s))return false;const b=getBounds(s),r=7/camera.z;if(!intersects({x:p.x-r,y:p.y-r,w:2*r,h:2*r},b))return false;if(['draw','highlight'].includes(s.type)){const m=transform(s,store);return strokePoints(s).some(pts=>pts.some((q,i)=>distance(p,apply(m,q),apply(m,pts[Math.max(0,i-1)]))<r+4))}return true})}
function deleteSelected(){if(!selected.size)return;checkpoint();for(const id of selected){const s=store[id];if(s&&editable(s))delete store[id]}selected.clear();changed()}
function erase(p,prev){const radius=ERASER_RADIUS/camera.z,steps=Math.max(1,Math.ceil(Math.hypot(p.x-prev.x,p.y-prev.y)/radius));for(let k=1;k<=steps;k++){const center={x:prev.x+(p.x-prev.x)*k/steps,y:prev.y+(p.y-prev.y)*k/steps};for(const s of [...shapes]){if(!store[s.id]||!editable(s)||!['draw','highlight'].includes(s.type))continue;const m=transform(s,store),runs=strokePoints(s).map(pts=>pts.map(p=>apply(m,p)));if(!runs.some(pts=>pts.some((q,i)=>distance(center,q,pts[Math.max(0,i-1)])<radius)))continue;if($('eraseMode').value==='stroke')delete store[s.id];else{delete store[s.id];for(const pts of runs.flatMap(pts=>cut(pts,center,radius))){const local=pts.map(p=>inverse(m,p));const copy=clone(s);copy.id=uid();copy.props.scaleX=copy.props.scaleY=1;copy.props.segments=[{type:'free',path:encode(local),dim:3}];store[copy.id]=copy}}cache.clear()}shapes=Object.values(store).filter(s=>s.typeName==='shape')}rebuild()}
function flushTouch(){if(touchRaf){cancelAnimationFrame(touchRaf);touchRaf=0;touchMove()}}
function touchReset(){const pts=[...touches.values()].slice(0,2);touchStart=pts.length?{pts,camera:{...camera}}:null;if(pts.length!==1)pageSwipe=null}
function touchMove(){if(!touchStart)return;if(touches.size===1&&pageSwipe&&dragPage(pageSwipe)){touchReset();return}const pts=[...touches.values()].slice(0,2),old=touchStart.pts;if(pts.length!==old.length){touchReset();return}const mid=p=>p.length===2?{x:(p[0].x+p[1].x)/2,y:(p[0].y+p[1].y)/2}:p[0],a=mid(old),b=mid(pts),dist=p=>Math.max(16,Math.hypot(p[0].x-p[1].x,p[0].y-p[1].y));const z=Math.max((!isWhiteboard&&frames.length)?base:.1,Math.min((!isWhiteboard&&frames.length)?base*6:8,touchStart.camera.z*(pts.length===2?dist(pts)/dist(old):1)));camera={x:b.x/z-a.x/touchStart.camera.z+touchStart.camera.x,y:b.y/z-a.y/touchStart.camera.z+touchStart.camera.y,z};viewChanged();touchReset()}
// Store raw samples during contact. Packing and persistence happen after release.
function appendInk(e){const g=gesture;if(g?.kind!=='ink')return;const m=transform(g.shape,store),r=canvas.parentElement.getBoundingClientRect();
 for(const ev of penSamples(e)){const point=inverse(m,world({x:ev.clientX-r.left,y:ev.clientY-r.top,z:ev.pressure>0?ev.pressure:g.pts.at(-1).z}));
  if(Number.isFinite(point.x)&&Number.isFinite(point.y)&&Math.hypot(point.x-g.pts.at(-1).x,point.y-g.pts.at(-1).y)>.01/camera.z)g.pts.push(point)}
 schedule(false);
}
function startPointer(e){if(!store)return;e.preventDefault();rememberBoard();resetPageMotion();
 // Safari can leave a text-selection gesture active after a Pencil contact.
 // The workspace has no selectable text; clear only selections inside the app.
 const selection=window.getSelection();if(selection?.rangeCount)selection.removeAllRanges();
 if(e.pointerType==='touch'){if(penActive||performance.now()<penQuietUntil)return;flushTouch();touches.set(e.pointerId,screen(e));touchReset();
 if(!isWhiteboard&&touches.size===1){const p=screen(e);pageSwipe={id:e.pointerId,start:p,last:p,camera:{...camera},zoom:camera.z/base}}
 try{canvas.setPointerCapture(e.pointerId)}catch{}return}
 if(e.button!==0&&e.button!==1&&e.button!==-1)return;
 if(gesture){if(e.pointerType!=='pen')return;finishActive()}
 if(e.pointerType==='mouse')canvas.focus({preventScroll:true});
 try{canvas.setPointerCapture(e.pointerId)}catch{}
 penActive=e.pointerType==='pen';if(penActive){flushTouch();touches.clear();touchStart=null;pageSwipe=null;resetPageMotion()}
 const sp=screen(e),p=world(sp);
 if(tool==='hand'||e.button===1){gesture={kind:'pan',start:sp,cam:{...camera},pointer:e.pointerId};return}
 if(tool==='select'){const s=hit(p);if(s){if(e.shiftKey){selected.has(s.id)?selected.delete(s.id):selected.add(s.id)}else if(!selected.has(s.id))selected=new Set([s.id]);checkpoint();gesture={kind:'move',start:p,original:new Map([...selected].map(id=>[id,{x:store[id].x,y:store[id].y}])),pointer:e.pointerId}}else{if(!e.shiftKey)selected.clear();gesture={kind:'marquee',start:p,last:p,pointer:e.pointerId}}updateUI();schedule();return}
 if(tool==='eraser'){updateEraserCursor(e);checkpoint();gesture={kind:'erase',last:p,pointer:e.pointerId};erase(p,p);return}
 const frame=isWhiteboard?frames.find(f=>intersects({x:p.x,y:p.y,w:0,h:0},getBounds(f))):current();
 if(frame&&!intersects({x:p.x,y:p.y,w:0,h:0},getBounds(frame))){penActive=false;return}
 const local=frame?inverse(transform(frame,store),p):p;
 const s=record({type:tool==='highlight'?'highlight':'draw',...(frame?{parentId:frame.id}:{}),x:local.x,y:local.y,meta:{nativeColor:$('color').value},props:{segments:[],color:Object.keys(palette).find(k=>palette[k]===$('color').value)||'black',fill:'none',dash:'solid',size:{2:'s',4:'m',7:'l'}[$('width').value],isComplete:true,isClosed:false,isPen:penActive,scale:1,scaleX:1,scaleY:1}});
 gesture={kind:'ink',shape:s,pts:[{x:0,y:0,z:p.z}],pointer:e.pointerId};schedule(false);
}
canvas.addEventListener('pointerdown',e=>{
 if(e.pointerType==='pen'){
  nativePenContact=true;
  // Some WebKit versions deliver touchstart before pointerdown. Hand the
  // existing stroke to the pointer stream instead of creating a duplicate.
  if(stylusTouch!==null&&gesture?.pointer===stylusTouch){gesture.pointer=e.pointerId;return}
 }
 startPointer(e);
});
function movePointer(e){
 if(e.target===canvas||gesture?.pointer===e.pointerId)updateEraserCursor(e);else hideEraserCursor();
 if(e.pointerType==='touch'){if(!touches.has(e.pointerId)||penActive)return;if(e.cancelable)e.preventDefault();touches.set(e.pointerId,screen(e));if(pageSwipe?.id===e.pointerId)pageSwipe.last=screen(e);if(!touchRaf)touchRaf=requestAnimationFrame(()=>{touchRaf=0;touchMove()});return}
 // Recover a contact whose initial down was lost, but never turn hover into ink.
 if(!gesture&&e.pointerType==='pen'&&e.pressure>0&&e.target===canvas)startPointer(e);
 if(!gesture||gesture.pointer!==e.pointerId)return;
 if(e.cancelable)e.preventDefault();
 const sp=screen(e),p=world(sp);
 if(gesture.kind==='pan'){camera={...gesture.cam,x:gesture.cam.x+(sp.x-gesture.start.x)/camera.z,y:gesture.cam.y+(sp.y-gesture.start.y)/camera.z};viewChanged()}
 else if(gesture.kind==='ink')appendInk(e);
 else if(gesture.kind==='erase'){erase(p,gesture.last);gesture.last=p}
 else if(gesture.kind==='move'){for(const [id,old] of gesture.original){const s=store[id],parent=store[s.parentId],m=parent?transform(parent,store):[1,0,0,1,0,0],a=inverse(m,gesture.start),b=inverse(m,p);s.x=old.x+b.x-a.x;s.y=old.y+b.y-a.y}cache.clear();schedule()}
 else{gesture.last=p;schedule()}
}
window.addEventListener('pointermove',movePointer,{passive:false});
function finish(e){if(e.pointerType==='touch'){
 if(!touches.has(e.pointerId))return;
 flushTouch();const swipe=pageSwipe;
 touches.delete(e.pointerId);touchReset();
 if(e.type==='pointerup'&&swipe?.id===e.pointerId&&frames.length){
  const dx=swipe.last.x-swipe.start.x,dy=swipe.last.y-swipe.start.y;
  if(Math.abs(dx)>70&&Math.abs(dx)>Math.abs(dy)*1.4&&swipeAllowed(swipe,dx))turnPage(dx<0?1:-1);
  else if(pageMotion)settleMotion(pageMotion,false);
 }else if(pageMotion)settleMotion(pageMotion,false);
 return;
}
 if(!gesture||gesture.pointer!==e.pointerId)return;
 if(e.type==='pointerup'&&gesture.kind==='ink')appendInk(e);
 hideEraserCursor();
 const g=gesture;gesture=null;if(penActive)penQuietUntil=performance.now()+120;penActive=false;
 if(g.kind==='ink'){g.shape.props.segments=[{type:'free',path:encode(g.pts),dim:3}];store[g.shape.id]=g.shape;shapes.push(g.shape);history.push({kind:'ink',shape:clone(g.shape)});if(history.length>60)history.shift();future=[];pendingInk.push(g.shape);markDirty();updateUI();schedule(false)}
 else if(g.kind==='marquee'){const b={x:Math.min(g.start.x,g.last.x),y:Math.min(g.start.y,g.last.y),w:Math.abs(g.start.x-g.last.x),h:Math.abs(g.start.y-g.last.y)};for(const s of shapes)if(editable(s)&&intersects(b,getBounds(s)))selected.add(s.id);updateUI();schedule()}
 else if(g.kind!=='pan')changed();
 try{if(canvas.hasPointerCapture(g.pointer))canvas.releasePointerCapture(g.pointer)}catch{}
}
function finishActive(){if(gesture)finish({pointerId:gesture.pointer,type:'interrupted'})}
// Capture can be lost while the Pencil is still touching: window listeners keep
// that stroke alive until release, cancellation, blur, or the next contact.
for(const type of ['pointerup','pointercancel'])window.addEventListener(type,e=>{
 finish(e);if(e.pointerType==='pen')nativePenContact=false;
});
// Prevent Safari's text selection/scroll recognizer from taking Pencil input.
// Touch Events also provide a fallback on WebKit if the pen pointer stream is
// absent. touchType distinguishes a stylus from fingers; do not guess by force.
function stylusEvent(t,type){return {
 type,pointerType:'pen',pointerId:'stylus:'+t.identifier,button:0,buttons:type==='pointerup'?0:1,
 clientX:t.clientX,clientY:t.clientY,pressure:t.force||.5,preventDefault(){}
}}
for(const type of ['touchstart','touchmove','touchend','touchcancel'])canvas.addEventListener(type,e=>{
 if(e.cancelable)e.preventDefault();
 for(const t of e.changedTouches){
  if(t.touchType!=='stylus')continue;
  const id='stylus:'+t.identifier;
  if(type==='touchstart'){
   stylusTouch=id;
   if(!nativePenContact&&!gesture)startPointer(stylusEvent(t,'pointerdown'));
  }else if(type==='touchmove'){
   if(!nativePenContact&&!gesture){stylusTouch=id;startPointer(stylusEvent(t,'pointerdown'))}
   if(!nativePenContact&&gesture?.pointer===id)movePointer(stylusEvent(t,'pointermove'));
  }else{
   if(gesture?.pointer===id)finish(stylusEvent(t,type==='touchend'?'pointerup':'pointercancel'));
   stylusTouch=null;
  }
 }
},{passive:false});
window.addEventListener('blur',()=>{hideEraserCursor();resetPageMotion();finishActive();flushTouch();touches.clear();touchStart=null;pageSwipe=null;nativePenContact=false;stylusTouch=null;saveView()});
canvas.addEventListener('pointerleave',hideEraserCursor);
canvas.addEventListener('pointerenter',updateEraserCursor);
canvas.addEventListener('contextmenu',e=>e.preventDefault());
canvas.addEventListener('wheel',e=>{if(!store)return;e.preventDefault();if(e.ctrlKey||e.metaKey)zoom(Math.exp(-e.deltaY*.008),screen(e));else{camera.x-=e.deltaX/camera.z;camera.y-=e.deltaY/camera.z;viewChanged()}},{passive:false});
for(const name of ['gesturestart','gesturechange','gestureend'])canvas.addEventListener(name,e=>{e.preventDefault()},{passive:false});
function summary(){return {shapeCount:shapes.length,handwritingStrokes:shapes.filter(s=>['draw','highlight'].includes(s.type)).length,frames:frames.map(s=>s.props.name),shapes:shapes.map(s=>({id:s.id.replace('shape:',''),type:s.type,...getBounds(s),...(s.type==='frame'?{name:s.props.name}:{}),text:plain(s.props.richText)||s.props.altText||''})),imageStale:true}}
function shapeState(){return new Map(shapes.map(s=>[s.id,JSON.stringify(s)]))}
function savePayload(state){const put=[],remove=[];for(const [id,json] of state)if(persistedShapes.get(id)!==json)put.push(JSON.parse(json));for(const id of persistedShapes.keys())if(!state.has(id))remove.push(id);return {tab,notebook:book,changes:{put,remove},summary:summary()}}
function save(){clearTimeout(saveTimer);saveChain=saveChain.catch(()=>{}).then(async()=>{
 if(!dirty)return;if(gesture){saveTimer=setTimeout(()=>save().catch(()=>{}),1000);return}
 const rev=revision,state=shapeState();say('Saving…');
 const response=await fetch('/api/board',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(savePayload(state))});
 if(!response.ok)throw Error('Save failed');persistedShapes=state;if(rev===revision)dirty=false;say(connected?'Saved · GPT connected':'Saved');
}).catch(err=>{say('Save failed — retrying');saveTimer=setTimeout(()=>save().catch(()=>{}),2000);throw err});return saveChain}
async function exportImage(box){const scale=Math.min(2,1600/Math.max(box.w,box.h)),out=document.createElement('canvas');out.width=Math.max(1,Math.ceil(box.w*scale));out.height=Math.max(1,Math.ceil(box.h*scale));const c=out.getContext('2d');c.fillStyle='white';c.fillRect(0,0,out.width,out.height);c.scale(scale,scale);c.translate(-box.x,-box.y);for(const s of shapes){if(!intersects(getBounds(s),box))continue;if(s.type==='image'){const im=imageFor(s);if(im&&!im.complete)await im.decode();if(im&&!im.naturalWidth)throw Error('A page image failed to load')}paintWorld(c,s)}return {png:out.toDataURL(),scale}}

function allBounds(){const bs=shapes.map(getBounds);if(!bs.length)return {x:0,y:0,w:1000,h:1000};const x=Math.min(...bs.map(b=>b.x)),y=Math.min(...bs.map(b=>b.y));return {x,y,w:Math.max(...bs.map(b=>b.x+b.w))-x,h:Math.max(...bs.map(b=>b.y+b.h))-y}}
async function operation(msg){finishActive();if(msg.op==='export'){if(!msg.frame&&frames.length&&!msg.overview)return {ok:true,summary:{frames:frames.map(s=>s.props.name),instruction:'Read an exact frame name; use region slices for long pages.'}};let f;if(msg.frame){const matches=frames.filter(s=>s.props.name.toLowerCase()===msg.frame.toLowerCase());f=matches[0];if(!f)return {ok:false,error:'Frame not found. Use an exact frame name.'}}let b=f?{...getBounds(f)}:allBounds(),fullHeight=b.h,region;if(msg.region){const {top,height}=msg.region;if(top>=b.h||top<0||height<=0)throw Error('Invalid region');b.y+=top;b.h=Math.min(height,b.h-top);region={top,height:b.h}}const {png,scale}=await exportImage(b);return {ok:true,png,summary:{...(f?{frame:f.props.name,fullHeight,region:region||{top:0,height:b.h}}:{frames:frames.map(s=>s.props.name)}),pageX:b.x,pageY:b.y,w:b.w,h:b.h,scale,handwritingStrokes:shapes.filter(s=>['draw','highlight'].includes(s.type)&&intersects(getBounds(s),b)).length,...(msg.include_details?{shapes:summary().shapes.filter(s=>intersects(s,b))}:{})}}}
 checkpoint();const ids=[],errors=[];if(msg.op==='delete'){for(const id of msg.ids||[]){const key='shape:'+id;if(store[key]&&editable(store[key])){delete store[key];ids.push(id)}else errors.push('Cannot delete '+id)}}else for(const item of msg.shapes||[]){try{const id=item.id?'shape:'+item.id:uid();let s=store[id];if(msg.op==='update'){if(!s)throw Error('Shape not found');if(locked(s))throw Error('Shape is locked');if(item.x!==undefined)s.x=item.x;if(item.y!==undefined)s.y=item.y;for(const key of ['w','h','color','size'])if(item[key]!==undefined)s.props[key]=item[key];if(item.text!==undefined)s.props.richText=rich(item.text)}else{if(s)throw Error('ID exists');const b=allBounds(),type=['text','note','arrow'].includes(item.type)?item.type:'geo';s=record({id,type,x:item.x??b.x+b.w+80,y:item.y??40,props:{w:item.w??200,h:item.h??100,geo:item.type||'rectangle',color:item.color||'black',size:item.size||'m',fill:'none',richText:rich(item.text||'')}});if(type==='arrow'){let a={x:s.x,y:s.y},b={x:s.x+(item.w??150),y:s.y+(item.h??0)};if(item.from&&item.to){const first=store['shape:'+item.from],last=store['shape:'+item.to];if(!first||!last)throw Error('Arrow target missing');const f=getBounds(first),l=getBounds(last);a={x:f.x+f.w/2,y:f.y+f.h/2};b={x:l.x+l.w/2,y:l.y+l.h/2}}s.x=a.x;s.y=a.y;s.props.start={x:0,y:0};s.props.end={x:b.x-a.x,y:b.y-a.y}}store[id]=s}ids.push(id.replace('shape:',''))}catch(e){errors.push(e.message)}}changed();await save();return {ok:true,ids,errors}}
for(const el of document.querySelectorAll('[data-tool]'))el.onclick=()=>{finishActive();hideEraserCursor();tool=el.dataset.tool;for(const b of document.querySelectorAll('[data-tool]'))b.setAttribute('aria-pressed',String(b===el));selected.clear();updateUI();schedule()};
function setMenusMinimized(minimized){document.body.classList.toggle('menus-minimized',minimized);const button=$('toggle-menus');button.textContent=minimized?'↓ Show menus':'↑ Minimize';button.setAttribute('aria-expanded',String(!minimized));button.title=minimized?'Show notebooks, pages, and zoom controls':'Hide navigation to make more room for drawing';writeLocal('gpt-board:menus-minimized',String(minimized))}
$('toggle-menus').onclick=()=>setMenusMinimized(!document.body.classList.contains('menus-minimized'));
setMenusMinimized(readLocal('gpt-board:menus-minimized')==='true');
$('delete').onclick=deleteSelected;$('undo').onclick=undo;$('redo').onclick=redo;$('plus').onclick=()=>zoom(1.25);$('minus').onclick=()=>zoom(.8);$('fit').onclick=()=>{resetPageMotion();fit(current()?.id)};$('pages').onchange=e=>showPage(e.target.value);$('prev').onclick=()=>turnPage(-1);$('next').onclick=()=>turnPage(1);
window.addEventListener('keydown',e=>{if(!store)return;if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='PageDown'||e.key==='PageUp'){e.preventDefault();turnPage(e.key==='PageDown'?1:-1)}if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();deleteSelected()}if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo()}if((e.metaKey||e.ctrlKey)&&e.key==='a'){e.preventDefault();selected=new Set(shapes.filter(editable).map(s=>s.id));updateUI();schedule()}});
async function navigate(id,white=isWhiteboard){try{resetPageMotion();finishActive();await save();saveView();location.assign(boardURL(id,white))}catch{say('Could not save. Please retry before switching.')}}
$('books').onchange=e=>navigate(e.target.value);
$('notebooks').onclick=()=>{if(isWhiteboard)navigate(null,false)};
$('whiteboards').onclick=()=>{if(!isWhiteboard)navigate(null,true)};
function openCreate(){ $('create-title').textContent=isWhiteboard?'New whiteboard':'New notebook';$('board-name').value='';$('create-error').textContent='';$('create-dialog').showModal();$('board-name').focus() }
$('newbook').onclick=openCreate;$('firstboard').onclick=openCreate;$('cancel-create').onclick=()=>$('create-dialog').close();
$('create-form').onsubmit=async e=>{e.preventDefault();const name=$('board-name').value.trim();if(!name)return;$('confirm-create').disabled=true;try{await save();saveView();const response=await fetch('/api/notebooks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'create',name,kind})});const result=await response.json();if(!response.ok)throw Error(result.error||'Could not create board');location.assign(boardURL(result.notebook.id))}catch(err){$('create-error').textContent=err.message;$('confirm-create').disabled=false}};
$('home').onclick=()=>{camera={x:0,y:0,z:1};viewChanged()};
$('notebooks').setAttribute('aria-pressed',String(!isWhiteboard));$('whiteboards').setAttribute('aria-pressed',String(isWhiteboard));$('books').setAttribute('aria-label',isWhiteboard?'Whiteboard':'Notebook');$('newbook').setAttribute('aria-label',isWhiteboard?'Create whiteboard':'Create notebook');$('page-controls').hidden=isWhiteboard;$('board-hint').hidden=!isWhiteboard;$('home').hidden=!isWhiteboard;$('fit').textContent=isWhiteboard?'Fit drawing':'Fit page';canvas.setAttribute('aria-label',isWhiteboard?'Whiteboard drawing canvas':'Notebook drawing canvas');document.querySelector('[data-tool="hand"]').textContent=isWhiteboard?'Move canvas':'Move page';
window.addEventListener('focus',()=>rememberBoard(true));
window.addEventListener('pagehide',()=>{finishActive();saveView();if(dirty){const data=JSON.stringify(savePayload(shapeState()));fetch('/api/board',{method:'POST',headers:{'Content-Type':'application/json'},body:data,keepalive:true}).catch(()=>{})}});document.addEventListener('visibilitychange',()=>{if(!document.hidden)rememberBoard(true);if(document.hidden){finishActive();saveView();save().catch(()=>{})}});window.addEventListener('beforeunload',e=>{if(dirty||gesture?.kind==='ink'){e.preventDefault();e.returnValue=''}});
try{const listing=await fetch('/api/notebooks?kind='+kind);if(!listing.ok)throw Error('Could not load your boards');const books=await listing.json();$('books').replaceChildren(...books.notebooks.map(b=>new Option(b.name,b.id)));if(book&&!books.notebooks.some(b=>b.id===book))throw Error('This board is not in this collection');book=book||books.notebooks.find(b=>b.id===(resume?.kind===kind?resume.id:readLocal(rememberKey)))?.id||books.notebooks[0]?.id;if(!book){$('empty').hidden=false;$('books').hidden=true;for(const el of document.querySelectorAll('.tool-items button,.tool-items input,.tool-items select,nav button,#undo,#redo'))el.disabled=true;say('Create your first whiteboard');}else{viewKey='gpt-board:native-view:'+book;$('books').value=book;document.title=books.notebooks.find(b=>b.id===book).name+' · GPT Board';const response=await fetch('/api/board?notebook='+encodeURIComponent(book));if(!response.ok)throw Error('Notebook could not load');snapshot=await response.json();if(!snapshot?.store)throw Error('Notebook is empty or unavailable');store=snapshot.store;rebuild();persistedShapes=shapeState();const saved=readView();
const resumedPage=!explicitLocation&&resume?.id===book?resume.page:null;
const addressPage=frames.find(f=>f.id===(resumedPage||params.get('page')));
activeFrameId=addressPage?.id||frames.find(f=>f.id===saved?.frame)?.id||frames.find(f=>saved&&getBounds(f).y+getBounds(f).h>saved.top)?.id||frames[0]?.id;
resize();fit(activeFrameId);
if(saved&&(!addressPage||saved.frame===addressPage.id)&&[saved.center,saved.top,saved.zoom].every(Number.isFinite)&&saved.zoom>0){const page=current(),legacyBase=page?Math.max(.05,(W-48)/getBounds(page).w):1;
const z=saved.mode==='single-page'?base*saved.zoom:legacyBase*saved.zoom;
camera={x:W/(2*z)-saved.center,y:-saved.top,z};clampCamera()}
if(isWhiteboard)window.history.replaceState(null,'',boardURL(book,true,false));
viewReady=true;rememberBoard();viewChanged();new ResizeObserver(resize).observe(canvas);const es=new EventSource(`/api/events?tab=${tab}&notebook=${encodeURIComponent(book)}`);es.onopen=()=>{connected=true;say('Saved · GPT connected')};es.onerror=()=>{connected=false;say('Reconnecting…')};let operations=Promise.resolve();es.onmessage=e=>{operations=operations.then(async()=>{const msg=JSON.parse(e.data);let result;try{result=await operation(msg)}catch(err){result={ok:false,error:err.message}}await fetch('/api/ack',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({opId:msg.opId,...result})});});};}}catch(err){say(err.message);console.error(err)}
