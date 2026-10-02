const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function worker(){
 const listeners={},stores=new Map(),messages=[];
 const caches={
  async open(name){if(!stores.has(name))stores.set(name,new Map());const entries=stores.get(name);return {put:async(key,response)=>entries.set(key,response.clone())}},
  async keys(){return [...stores.keys()]},async delete(name){return stores.delete(name)},
  async match(key){for(const entries of stores.values())if(entries.has(key))return entries.get(key).clone()}
 };
 let network=async()=>new Response('public UI shell',{status:200});
 const context=vm.createContext({URL,Response,caches,fetch:(...args)=>network(...args),self:{location:{origin:'https://photos.example.com'},
  addEventListener:(name,callback)=>listeners[name]=callback,clients:{claim:async()=>{},matchAll:async()=>[{postMessage:message=>messages.push(message)}]}}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/sw.js'),'utf8'),context);
 async function dispatch(name,extra={}){let pending;listeners[name]({...extra,waitUntil:p=>pending=p,respondWith:p=>pending=p});return pending}
 return {stores,messages,dispatch,setNetwork:callback=>network=callback,request:pathname=>({url:'https://photos.example.com'+pathname,method:'GET'})};
}

test('offline fallback serves the shell but never API or private photos',async()=>{
 const w=worker();await w.dispatch('install');w.setNetwork(async()=>{throw Error('Offline')});
 const shell=await w.dispatch('fetch',{request:w.request('/')});assert.equal(await shell.text(),'public UI shell');
 for(const pathname of ['/api/images','/api/gallery','/api/performance','/api/performance/export','/thumb/1','/image/1'])await assert.rejects(w.dispatch('fetch',{request:w.request(pathname)}),/Offline/);
 for(const entries of w.stores.values())assert.equal([...entries.keys()].some(key=>key.startsWith('/api/')||key.startsWith('/thumb/')||key.startsWith('/image/')),false);
});

test('401 purges the shell and notifies all clients',async()=>{
 const w=worker();await w.dispatch('install');w.setNetwork(async()=>new Response(null,{status:401}));
 const response=await w.dispatch('fetch',{request:w.request('/api/images')});assert.equal(response.status,401);assert.equal(w.stores.size,0);assert.equal(w.messages[0].type,'auth-required');
});

test('private photo responses are not cached even while online',async()=>{
 const w=worker();await w.dispatch('install');w.setNetwork(async()=>new Response('private photo'));
 await w.dispatch('fetch',{request:w.request('/thumb/1')});
 await w.dispatch('fetch',{request:w.request('/api/gallery')});
 for(const entries of w.stores.values()){assert.equal(entries.has('/thumb/1'),false);assert.equal(entries.has('/api/gallery'),false)}
});
