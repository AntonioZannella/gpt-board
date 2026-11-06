import {test} from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs/promises';import os from 'node:os';import path from 'node:path';import {spawn} from 'node:child_process';import {once} from 'node:events';
test('isolated HTTP, persistence, MCP routing and iPad access',async()=>{
 const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'gpt-board-audit-'));const root=new URL('../',import.meta.url);const children=[],controllers=[];
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const start=async(file,env)=>{const child=spawn(process.env.BOARD_PYTHON || 'python3',[path.join(tmp,file)],{env:{...process.env,...env},stdio:['ignore','pipe','pipe']});children.push(child);await Promise.race([once(child.stdout,'data'),once(child,'exit').then(([code])=>{throw Error('Server exited '+code)}),sleep(3000).then(()=>{throw Error('Server startup timeout')})]);return child};
 try{
 await fs.copyFile(new URL('server.py',root),path.join(tmp,'server.py'));await fs.copyFile(new URL('ipad_server.py',root),path.join(tmp,'ipad_server.py'));for(const support of ["board_storage.py", "board_review.py", "mcp_schema.json"]) await fs.copyFile(new URL(support, root),path.join(tmp,support));
 await fs.writeFile(path.join(tmp,'package.json'),'{"type":"module"}');await fs.mkdir(path.join(tmp,'dist'));await fs.copyFile(new URL('src/index.html',root),path.join(tmp,'dist/index.html'));await fs.mkdir(path.join(tmp,'dist/native'));for(const asset of ['app.js','model.js','style.css'])await fs.copyFile(new URL('src/'+asset,root),path.join(tmp,'dist/native',asset));await start('server.py',{PORT:'8793'});
 const base='http://127.0.0.1:8793',post=(url,body,headers={})=>fetch(url,{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
 assert.equal((await fetch(base)).status,200);assert.equal((await post(base+'/api/board',{snapshot:{}})).status,409);
 const snapshot={store:{},schema:{schemaVersion:2,sequences:{}}};
 const save=async(tab,extra={})=>{const r=await post(base+'/api/board',{tab,snapshot,summary:{shapeCount:1},...extra});assert.equal(r.status,200)};
 await save('disconnected-tab');assert.deepEqual(await (await fetch(base+'/api/board')).json(),snapshot);
	const listed=await (await fetch(base+'/api/notebooks')).json();assert.equal(listed.notebooks.length,1);assert.equal(listed.notebooks[0].id,'default');
	// A real notebook snapshot always has document/page records; add them before exercising notebook creation.
	snapshot.store={'document:document':{id:'document:document',typeName:'document',name:'',meta:{}},'page:page':{id:'page:page',typeName:'page',name:'Default',index:'a1',meta:{}}};await save('disconnected-tab');
	const created=await (await post(base+'/api/notebooks',{action:'create',name:'Physics Notes'})).json();assert.equal(created.notebook.id,'physics-notes');
	const second={...snapshot,store:{...snapshot.store,'page:page':{...snapshot.store['page:page'],name:'Physics Notes'}}};
	let r=await post(base+'/api/board',{tab:'physics-tab',notebook:'physics-notes',snapshot:second,summary:{shapeCount:0}});assert.equal(r.status,200);
	assert.deepEqual(await (await fetch(base+'/api/board?notebook=physics-notes')).json(),second);assert.deepEqual(await (await fetch(base+'/api/board')).json(),snapshot,'notebooks must save independently');
 // Whiteboards use separate collections and retain independent snapshots.
 const white=await (await post(base+'/api/notebooks',{action:'create',name:'Physics Notes',kind:'whiteboard'})).json();
 assert.equal(white.notebook.id,'physics-notes-2');assert.equal(white.notebook.kind,'whiteboard');
 const white2=await (await post(base+'/api/notebooks',{action:'create',name:'Ideas',kind:'whiteboard'})).json();
 const whiteSnapshot=await (await fetch(base+'/api/board?notebook='+white.notebook.id)).json();
 assert.ok(Object.values(whiteSnapshot.store).every(r=>['document','page'].includes(r.typeName)));
 const ink={id:'shape:white-ink',typeName:'shape',type:'draw',parentId:'page:page',x:-2400,y:6500,props:{segments:[]}};
 whiteSnapshot.store[ink.id]=ink;
 assert.equal((await post(base+'/api/board',{tab:'white-tab',notebook:white.notebook.id,snapshot:whiteSnapshot,summary:{shapeCount:1}})).status,200);
 assert.deepEqual(await (await fetch(base+'/api/board?notebook='+white.notebook.id)).json(),whiteSnapshot);
 assert.ok(!(await (await fetch(base+'/api/board?notebook='+white2.notebook.id)).json()).store[ink.id]);
 assert.deepEqual(await (await fetch(base+'/api/board?notebook=physics-notes')).json(),second);
 const notebooks=await (await fetch(base+'/api/notebooks')).json(),whites=await (await fetch(base+'/api/notebooks?kind=whiteboard')).json();
 assert.deepEqual(notebooks.notebooks.map(b=>b.id),['default','physics-notes']);
 assert.deepEqual(whites.notebooks.map(b=>b.id),['physics-notes-2','ideas']);
 assert.equal((await post(base+'/api/notebooks',{action:'create',name:'Bad',kind:'invalid'})).status,400);
 assert.equal((await fetch(base+'/api/notebooks?kind=invalid')).status,400);
 assert.equal((await post(base+'/api/board',{tab:'bad',snapshot:{store:{damaged:true}}})).status,400);assert.deepEqual(await (await fetch(base+'/api/board')).json(),snapshot,'invalid save must preserve the last board');
 // Open two simulated clients. They only ACK requests; no real board is touched.
 const received={first:[],second:[]};
 for(const name of ['first','second']){
  const controller=new AbortController();controllers.push(controller);
  const res=await fetch(base+'/api/events?tab='+name,{signal:controller.signal});
  (async()=>{let buffer='';for await(const chunk of res.body){buffer+=new TextDecoder().decode(chunk);let end;while((end=buffer.indexOf('\n\n'))>=0){const event=buffer.slice(0,end);buffer=buffer.slice(end+2);if(!event.startsWith('data: '))continue;const msg=JSON.parse(event.slice(6));received[name].push(msg);await post(base+'/api/ack',{opId:msg.opId,ok:true,ids:[],summary:msg.frame?{frame:'Test'}:{frames:['Test']},...((msg.frame||msg.overview)&&{png:'data:image/png;base64,aGVsbG8='})})}}})().catch(()=>{});
 }
 const token=(await fs.readFile(path.join(tmp,'data/token'),'utf8')).trim();
 const rpc=async(name,args={})=>(await (await post(base+'/mcp/'+token,{jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}})).json()).result;
 await rpc('read_board',{frame:'Test'});assert.equal(received.first.length,1);assert.equal(received.second.length,0,'startup requests must reach just one tab');
 await save('second');for(const name of ['add_shapes','update_shapes','delete_shapes'])await rpc(name,{shapes:[],ids:[]});assert.equal(received.first.length,1);assert.equal(received.second.length,3,'active tab should receive all tools');
 await save('second',{png:'data:image/png;base64,aGVsbG8=',summary:{shapeCount:1,imageStale:true}});
 const listing=await rpc('read_board');assert.ok(!listing.content.some(c=>c.type==='image'),'frame listing must not export an image');assert.ok(listing.content[0].text.includes('Test'));const overview=await rpc('read_board',{overview:true});assert.equal(overview.content[0].type,'image');const bad=await rpc('read_board',{frame:5});assert.ok(bad.isError);
 await start('ipad_server.py',{IPAD_HOST:'127.0.0.1',IPAD_PORT:'8794',BOARD_PORT:'8793'});
 const ipad='http://127.0.0.1:8794';assert.equal((await fetch(ipad)).status,403);
 const key=(await fs.readFile(path.join(tmp,'data/ipad-token'),'utf8')).trim();const pair=await fetch(ipad+'/pair?key='+key,{redirect:'manual'});assert.equal(pair.status,303);const cookie=pair.headers.get('set-cookie').split(';')[0];
 assert.equal((await fetch(ipad,{headers:{cookie}})).status,200);assert.equal((await fetch(ipad+'/api/notebooks',{headers:{cookie}})).status,200);assert.equal((await fetch(ipad+'/mcp/'+token,{headers:{cookie}})).status,404);assert.equal((await fetch(ipad,{headers:{cookie,origin:'https://unrelated.example'}})).status,403);assert.equal((await fetch(ipad,{headers:{cookie,'x-forwarded-for':'1.2.3.4'}})).status,403);
 // Exercise the real frontend assets through the paired iPad bridge, not just its HTML.
 const ipadHtml=await (await fetch(ipad,{headers:{cookie}})).text();
 assert.ok(ipadHtml.includes('/native/app.js'));assert.ok(ipadHtml.includes('/native/style.css'));
 for(const asset of ['app.js','model.js','style.css']){
  assert.equal((await fetch(ipad+'/native/'+asset)).status,403,'assets still require pairing');
  const response=await fetch(ipad+'/native/'+asset,{headers:{cookie}});
  assert.equal(response.status,200,'iPad must load '+asset);
  assert.match(response.headers.get('content-type'),asset.endsWith('.css')?/text\/css/:/(javascript|ecmascript)/);
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(await response.text(),await fs.readFile(new URL('src/'+asset,root),'utf8'));
 }
 assert.equal((await fetch(ipad+'/native/private.json',{headers:{cookie}})).status,404);
 assert.equal((await fetch(ipad+'/data/token',{headers:{cookie}})).status,404);
 const ipadWhiteboards=await (await fetch(ipad+'/api/notebooks?kind=whiteboard',{headers:{cookie}})).json();
 assert.equal(ipadWhiteboards.notebooks.length,2);
 console.log('PASS HTTP saves, legacy-tab rejection, single-tab export, active-tab routing (4 tools), compact frame listing and explicit overview, iPad pairing and origin restrictions');
 }finally{controllers.forEach(c=>c.abort());for(const child of children){child.kill();await once(child,'exit').catch(()=>{})}await fs.rm(tmp,{recursive:true,force:true})}
});
