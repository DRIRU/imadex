const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function page(fetch){
 const nodes=new Map(),events={};
 const $=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,textContent:'private metadata',value:'private tags',removeAttribute(){},replaceChildren(){this.textContent=''}});return nodes.get(id)};
 const state={authLocked:false,authEpoch:0,request:0,items:[{name:'private photo'}],total:1,library:{folders:['private folder']},drive:{connected:true}};
 const context=vm.createContext({$,state,fetch,Error,JSON,selectedPhotos:new Map(),render(){},updateSelection(){},safely:fn=>fn,toast(){},
  window:{addEventListener:(name,fn)=>events[name]=fn},navigator:{onLine:true},document:{querySelectorAll:()=>[]},caches:{keys:async()=>[]}});
 const app=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
 vm.runInContext(app.slice(app.indexOf('async function api('),app.indexOf('const safely')),context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/performance.js'),'utf8'),context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/pwa.js'),'utf8'),context);
 return {context,state,nodes,events,call:path=>vm.runInContext(`api(${JSON.stringify(path)})`,context)};
}

test('401 clears private gallery and blocks earlier successful responses',async()=>{
 let finishOld;
 const oldResponse=new Promise(resolve=>finishOld=resolve);
 const p=page(path=>path==='/api/images'?oldResponse:Promise.resolve(new Response(null,{status:401})));
 const old=p.call('/api/images');
 await assert.rejects(p.call('/api/setup'),/Sign-in required/);
 finishOld(new Response(JSON.stringify({items:[{name:'old private photo'}]}),{headers:{'Content-Type':'application/json'}}));
 await assert.rejects(old,/Sign-in required/);
 assert.equal(p.state.authLocked,true);assert.equal(p.state.items.length,0);assert.equal(p.state.library,null);
 assert.equal(p.nodes.get('folders').textContent,'');assert.equal(p.nodes.get('tags').value,'');
 assert.equal(p.nodes.get('photoOCRText').textContent,'');assert.equal(p.nodes.get('photoOCRStatus').textContent,'');
 assert.equal(p.nodes.get('performanceStages').textContent,'');assert.equal(p.nodes.get('performanceJob').textContent,'');
 assert.equal(p.nodes.get('performanceStatus').textContent,'');
 assert.equal(vm.runInContext('performanceState',p.context),null);
});

test('online event preserves sign-in notice and blocked requests never fetch',async()=>{
 let calls=0;
 const p=page(async()=>{calls++;return new Response(null,{status:401})});
 await assert.rejects(p.call('/api/setup'),/Sign-in required/);
 p.events.online();assert.equal(p.nodes.get('offlineNotice').hidden,false);
 assert.match(p.nodes.get('offlineNotice').textContent,/Authentication required/);
 await assert.rejects(p.call('/api/images'),/Sign-in required/);assert.equal(calls,1);
});
