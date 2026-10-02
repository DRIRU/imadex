let installPrompt;
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;$('installApp').hidden=false});
$('installApp').onclick=safely(async()=>{if(!installPrompt)return;await installPrompt.prompt();await installPrompt.userChoice;installPrompt=null;$('installApp').hidden=true});
function connectionState(){if(state.authLocked)return;const offline=!navigator.onLine;$('offlineNotice').hidden=!offline;if(offline)$('offlineNotice').textContent='Offline. Connect to browse your private library; photos are not stored for offline use.'}
window.addEventListener('offline',connectionState);window.addEventListener('online',connectionState);connectionState();
if('serviceWorker' in navigator){
 navigator.serviceWorker.register('/sw.js').catch(error=>{console.warn('App installation unavailable:',error.message)});
 navigator.serviceWorker.addEventListener('message',event=>{if(event.data?.type==='auth-required')lockGallery()});
}
$('clearShellCache').onclick=safely(async()=>{for(const name of await caches.keys())if(name.startsWith('imadex-shell-'))await caches.delete(name);toast('Installed app shell cache cleared. Private photos are never cached by the app for offline use.')});

function lockGallery(){
 if(!state.authLocked){state.authLocked=true;state.authEpoch++;state.request++}
 state.library=null;state.drive={};state.semanticResult=null;
 state.items=[];state.total=0;render();selectedPhotos.clear();updateSelection();
 for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
 for(const id of ['fullImage','facePreviewImage'])$(id).removeAttribute('src');
 for(const id of ['photoPeople','photoFaces','metadata','recognitionSuggestions','peopleGrid','folders','albumList','savedSearchList','duplicatePairs','jobHistory','setupSummary','syncFailures'])$(id).replaceChildren();
 for(const id of ['viewerName','detailName','photoOCRText','photoOCRStatus'])$(id).textContent='';$('tags').value='';
 $('offlineNotice').hidden=false;$('offlineNotice').textContent='Authentication required. Reload to sign in again.';
}
