// Setup and background-job controls share the gallery's authenticated API.
let setupTimer;
async function loadSetup(){
 const s=await api('/api/setup'), root=$('setupSummary');root.replaceChildren();
 const lines=[['Google Drive',s.drive.connected?'Connected':s.drive.configured?'Sign-in required':'OAuth client required'],
 ['Encoder',s.embedding?.model||'Unavailable'],['Vector storage',s.embedding?`${s.embedding.database} · ${s.embedding.collection}`:'Unavailable'],
 ['Inference provider',s.embedding?.provider||'Not loaded'],['Requested runtime',s.requested_provider],
 ['Phone access',s.access.tunnel_configured&&s.access.authenticated?'Authenticated tunnel configured':'Configure authenticated Cloudflare access']];
 for(const [label,value] of lines){const term=document.createElement('dt'),detail=document.createElement('dd');term.textContent=label;detail.textContent=value;root.append(term,detail)}
 $('modelTowers').replaceChildren();
 for(const t of s.models?.towers||[]){const p=document.createElement('p');p.textContent=`${t.kind==='image'?'Image':'Text'} model · ${t.loaded?'loaded':t.cached?'cached':'download required'} · approximately ${bytes(t.estimated_bytes)} · ${t.provider}`;$('modelTowers').append(p)}
 const d=s.models?.download;$('prepareModels').disabled=!s.models||d?.running;
 $('modelDownload').textContent=d?.error|| (d?.running?`Preparing models… ${bytes(d.bytes)} added to local cache`:'Models are downloaded to this PC. Preparation also checks inference sessions.');
 renderPerformance(s.performance);
 const o=s.ocr;
 $('ocrEnabled').checked=!!o?.enabled;$('ocrEnabled').disabled=!o?.available;
 $('retryOCR').disabled=!o?.available;
 $('ocrStatus').textContent=!o?.available?'Run .venv/Scripts/python.exe setup_ocr.py on the PC to install optional text extraction.':`${o.ready} / ${o.total} images extracted · ${o.failed} failed · ${o.pending} pending · CPU${o.running?' · Extracting…':o.enabled?' · Watching for new images':' · Paused'}${o.error?' · '+o.error:''}`;
 const sync=s.sync;
 // Preserve an interval being edited while polling updates the status.
 if(document.activeElement!==$('syncInterval'))$('syncInterval').value=sync?.interval||300;
 $('syncEnabled').checked=!!sync?.enabled;$('syncEnabled').disabled=!sync;
 $('syncMessage').textContent=sync?.message||'Sync service unavailable';
 $('syncFailures').replaceChildren();
 for(const source of sync?.sources||[]){if(!source.error)continue;const p=document.createElement('p');p.textContent=`Folder ${source.folder_id}: ${source.error} · retry ${new Date(source.next_run*1000).toLocaleTimeString()}`;$('syncFailures').append(p)}
 $('jobHistory').replaceChildren();
 for(const job of sync?.jobs||[]){const li=document.createElement('li');const duration=job.finished?`${Math.round(job.finished-job.started)}s`:'in progress';li.textContent=`${new Date(job.started*1000).toLocaleString()} · ${job.kind} · ${job.status} · ${job.processed} processed · ${job.failed_count||0} failed · ${duration}${job.error?' · '+job.error:''}`;$('jobHistory').append(li)}
 if(!sync?.jobs.length){const li=document.createElement('li');li.textContent='No synchronization jobs yet';$('jobHistory').append(li)}
 const cache=s.thumbnail_cache;
 if(document.activeElement!==$('cacheBudget'))$('cacheBudget').value=Math.round((cache?.budget||0)/1024**2);
 $('cacheStatus').textContent=cache?`${bytes(cache.used)} used of ${bytes(cache.budget)}. Only previews are cached; originals remain in Drive.`:'Thumbnail cache unavailable';
 clearTimeout(setupTimer);if($('systemDialog').open)setupTimer=setTimeout(()=>safely(loadSetup)(),3000);
}
$('openSystem').onclick=safely(async()=>{$('systemDialog').showModal();await loadSetup()});
$('systemDialog').addEventListener('close',()=>clearTimeout(setupTimer));
$('prepareModels').onclick=safely(async()=>{await api('/api/models/prepare',{});await loadSetup()});
async function saveSync(){await api('/api/sync',{action:'settings',enabled:$('syncEnabled').checked,interval:Number($('syncInterval').value)});await loadSetup()}
$('syncEnabled').onchange=safely(saveSync);$('saveSync').onclick=safely(saveSync);
$('retrySync').onclick=safely(async()=>{await api('/api/sync',{action:'retry'});await loadSetup();toast('Sync retry queued. Enable automatic sync to run it.')});
$('saveCache').onclick=safely(async()=>{await api('/api/thumbnails',{action:'budget',budget:Number($('cacheBudget').value)*1024**2});await loadSetup()});
$('clearCache').onclick=safely(async()=>{await api('/api/thumbnails',{action:'clear'});await loadSetup();toast('Private thumbnail cache cleared')});

$('ocrEnabled').onchange=safely(async()=>{await api('/api/ocr',{action:'settings',enabled:$('ocrEnabled').checked});await loadSetup()});
$('retryOCR').onclick=safely(async()=>{await api('/api/ocr',{action:'retry'});await loadSetup();toast('Failures reset. Enable automatic extraction to process them.')});
$('clearOCR').onclick=safely(async()=>{if(!confirm('Clear extracted text for every image and pause automatic OCR? Originals, albums and tags stay unchanged.'))return;await api('/api/ocr',{action:'clear'});$('photoOCRText').textContent='';await loadSetup();await images()});
let photoOCRTimer,photoOCREpoch=0;
async function loadPhotoOCR(item){
 const epoch=++photoOCREpoch;clearTimeout(photoOCRTimer);$('photoOCRText').textContent='';$('photoOCRStatus').textContent='Checking extracted text…';
 const s=await api('/api/ocr?image_id='+item.id);
 if(epoch!==photoOCREpoch||!$('viewer').open||state.items[state.selected]?.id!==item.id)return;
 $('photoOCRText').textContent=s.image.text||'';$('extractOCR').disabled=!s.available;
 $('photoOCRStatus').textContent=!s.available?'Install optional OCR from setup on the PC.':s.image.error|| (s.image.status==='ready'?(s.image.text?'Extracted locally. Recognition may contain errors.':'No printed text found.'):'Not extracted yet.');
 if(s.running||s.queued?.includes(item.id))photoOCRTimer=setTimeout(()=>safely(loadPhotoOCR)(item),2500);
}
$('extractOCR').onclick=safely(async()=>{const item=state.items[state.selected];if(!item)return;await api('/api/ocr',{action:'image',image_id:item.id,revision:item.revision});$('photoOCRStatus').textContent='Text extraction queued…';clearTimeout(photoOCRTimer);photoOCRTimer=setTimeout(()=>safely(loadPhotoOCR)(item),2000)});
$('viewer').addEventListener('close',()=>{++photoOCREpoch;clearTimeout(photoOCRTimer);$('photoOCRText').textContent='';$('photoOCRStatus').textContent=''});
