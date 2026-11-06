const phaseKey='resume-audit-phase',phase=JSON.parse(sessionStorage.getItem(phaseKey)||'null');
if(phase?.standalone)Object.defineProperty(navigator,'standalone',{value:true,configurable:true});
if(phase?.blocked){
 const get=Storage.prototype.getItem,set=Storage.prototype.setItem;
 Storage.prototype.getItem=function(key){if(this===localStorage)throw Error('Storage disabled for test');return get.call(this,key)};
 Storage.prototype.setItem=function(key,value){if(this===localStorage)throw Error('Storage disabled for test');return set.call(this,key,value)};
}
await import('/native/app.js');
const button=document.getElementById('run-resume'),result=document.getElementById('resume-result');
const assert=(value,message)=>{if(!value)throw Error(message)};
const step=(number,blocked=false,standalone=false)=>sessionStorage.setItem(phaseKey,JSON.stringify({number,blocked,standalone}));
const choose=id=>{const el=document.getElementById('books');el.value=id;el.dispatchEvent(new Event('change',{bubbles:true}))};
async function run(){
 const selected=document.getElementById('books').value;
 button.disabled=true;
 if(!phase){assert(selected==='alpha','Fresh start did not open Alpha');step(1);choose('beta');return}
 if(phase.number===1){assert(selected==='beta','Picker did not open Beta');assert(new URL(location.href).searchParams.get('notebook')==='beta','Blank notebook URL lost selection');step(2);location.reload();return}
 if(phase.number===2){assert(selected==='beta','Refresh lost Beta');step(3);location.assign('/');return}
 if(phase.number===3){assert(selected==='beta','Reopening main address lost Beta');step(4);document.getElementById('whiteboards').click();return}
 if(phase.number===4){assert(selected==='ideas','Whiteboard picker failed');step(5);location.assign('/');return}
 if(phase.number===5){assert(selected==='ideas','Main address forgot last collection');step(6,true);location.assign('/?notebook=alpha');return}
 if(phase.number===6){assert(selected==='alpha','Explicit link did not open Alpha');step(7,true);choose('beta');return}
 if(phase.number===7){assert(selected==='beta','Storage failure blocked notebook switch');step(8,true);location.assign('/');return}
 if(phase.number===8){assert(selected==='beta','Cookie backup failed to restore Beta');step(9,true);location.reload();return}
 if(phase.number===9){assert(selected==='beta','Reload failed with browser storage disabled');step(10,true);location.assign('/?notebook=alpha');return}
 if(phase.number===10){assert(selected==='alpha','Saved selection overrode an explicit notebook link');step(11,true,true);location.assign('/?notebook=beta');return}
 if(phase.number===11){assert(selected==='alpha','Home Screen launch reopened the icon notebook');step(12,true,true);choose('beta');return}
 if(phase.number===12){assert(selected==='beta','App resume blocked an explicit picker selection');assert(!new URL(location.href).searchParams.has('open'),'One-shot navigation flag was not removed');step(13,true,true);location.assign('/?notebook=alpha');return}
 if(phase.number===13){assert(selected==='beta','Second Home Screen launch forgot Beta');step(14,true,true);document.getElementById('whiteboards').click();return}
 if(phase.number===14){assert(selected==='ideas','App resume blocked switching collections');step(15,true,true);location.assign('/?notebook=alpha');return}
 if(phase.number===15){assert(selected==='ideas','Home Screen launch forgot Whiteboards');step(16,true,true);document.getElementById('notebooks').click();return}
 if(phase.number===16){assert(['alpha','beta'].includes(selected),'Switching back to Notebooks failed');step(17,true,false);location.assign('/?notebook=alpha');return}
 if(phase.number===17){assert(selected==='alpha','Normal browser link behavior changed');step(18,true)}
 result.textContent='PASS — Home Screen launch resumes last notebook/whiteboard\nOld icon URLs no longer override the last session\nStorage-disabled reload and reopen use cookie backup\nIn-app picker and ordinary browser links still work\nBlank notebook address retains its ID';
}
if(phase)run().catch(e=>{result.textContent='FAIL — '+e.message});
else button.onclick=()=>run().catch(e=>{result.textContent='FAIL — '+e.message});
