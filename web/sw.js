// Cache only the empty UI shell. APIs, originals and previews always use the network.
const SHELL='imadex-shell-v5';
const assets=['/','/app.js','/people.js','/setup.js','/performance.js','/performance.css','/discovery.js','/duplicates.js','/pwa.js','/style.css','/mobile.css','/semantic.css','/people.css','/setup.css','/discovery.css','/favicon.svg','/manifest.webmanifest','/icon-192.png','/icon-512.png'];
self.addEventListener('install',event=>event.waitUntil((async()=>{
 const cache=await caches.open(SHELL);
 // Install can occur behind Basic authentication; never cache a failed sign-in page.
 for(const path of assets){const response=await fetch(path,{cache:'no-store',credentials:'same-origin'});if(!response.ok)throw Error('Shell authentication or network unavailable');await cache.put(path,response)}
})()));
self.addEventListener('activate',event=>event.waitUntil((async()=>{for(const name of await caches.keys())if(name.startsWith('imadex-shell-')&&name!==SHELL)await caches.delete(name);await self.clients.claim()})()));
async function lockClients(){
 await caches.delete(SHELL);
 for(const client of await self.clients.matchAll())client.postMessage({type:'auth-required'});
}
self.addEventListener('fetch',event=>{
 const url=new URL(event.request.url);if(url.origin!==self.location.origin||event.request.method!=='GET')return;
 event.respondWith((async()=>{
  try{const response=await fetch(event.request);if(response.status===401){await lockClients();return response}
   if(assets.includes(url.pathname)&&response.ok){const cache=await caches.open(SHELL);await cache.put(url.pathname,response.clone())}return response;
  }catch(error){
   if(assets.includes(url.pathname)){const cached=await caches.match(url.pathname);if(cached)return cached}
   throw error;
  }
 })());
});
self.addEventListener('message',event=>{if(event.data?.type==='clear-shell')event.waitUntil(caches.delete(SHELL))});
