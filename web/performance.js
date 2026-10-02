// Diagnostics use the same authenticated, uncached API as the gallery.
let performanceState=null;
let performancePinned=false;
function clearPerformance(){
 performanceState=null;performancePinned=false;
 for(const id of ['performanceStatus','performanceResourcesStatus','performanceJobStatus','performanceStages','performanceJob'])$(id).replaceChildren();
 $('performanceEnabled').checked=false;$('performanceResources').checked=false;
}
function renderPerformance(s){
 performanceState=s;
 $('performanceEnabled').checked=!!s?.enabled;
 $('performanceResources').checked=!!s?.resources;
 $('performanceStart').disabled=!s;$('performanceStop').disabled=!s?.capturing;
 $('performanceExport').disabled=!s;$('performanceClear').disabled=!s;
 $('performanceStatus').textContent=!s?'Diagnostics unavailable':`${s.capturing?`Capturing · ${s.remaining_seconds}s left`:s.enabled?'Job summaries enabled':'Diagnostics off'} · ${s.dropped_events} dropped events · ${s.write_errors} write errors · logs capped at 50 MiB`;
 const resource=s?.resource_state,sample=resource?.samples?.at(-1);
 $('performanceResourcesStatus').textContent=resource?`GPU: ${resource.gpu_status}${sample?.gpus?.map(g=>` · GPU ${g.index}: ${g.utilization_percent}% · ${g.memory_mib} MiB`).join('')||''} · Process: ${resource.process_status}${sample?.rss_bytes?` · RAM ${bytes(sample.rss_bytes)}`:''}${sample?.cpu_percent!=null?` · CPU ${sample.cpu_percent}%`:''}`:'';
 const select=$('performanceJob'),previous=select.value;select.replaceChildren();
 for(const job of [...(s?.active||[]),...(s?.jobs||[])]){
  const option=document.createElement('option');option.value=job.id;option.textContent=`${job.kind} · ${job.outcome} · ${new Date(job.started_utc).toLocaleString()}`;select.append(option);
 }
 if(performancePinned&&[...select.options].some(o=>o.value===previous))select.value=previous;
 else {const embedding=[...(s?.active||[]),...(s?.jobs||[])].find(j=>j.kind==='embeddings');if(embedding)select.value=embedding.id}
 renderPerformanceJob();
}
function renderPerformanceJob(){
 const job=[...(performanceState?.active||[]),...(performanceState?.jobs||[])].find(j=>j.id===$('performanceJob').value),root=$('performanceStages');root.replaceChildren();
 if(!job){$('performanceJobStatus').textContent='Enable summaries or start a capture, then index images.';return}
 const c=job.counts,m=job.metadata;
 $('performanceJobStatus').textContent=`${job.images_per_minute} new images/min · ${c.visited} checked · ${job.queue_wait_ms||0} ms queued · ${(job.wall_ms/1000).toFixed(1)}s${job.capture_partial?' · partial capture':''} · ${c.success} successful (${c.new_success} new, ${c.reused} reused) · ${c.skipped} unchanged · ${c.failed_skipped} previous failures skipped · ${c.failed} failed · ${c.stale} stale · largest stage: ${job.largest_stage||'not measured'} · first inference: ${job.first_inference_ms==null?'not observed':job.first_inference_ms+' ms'} · uninstrumented: ${job.uninstrumented_ms} ms · image providers: ${m.image_providers?.join(', ')||'not observed'} · text providers: ${m.text_providers?.join(', ')||'not observed'}`;
 for(const s of job.stages){const row=document.createElement('tr');for(const value of [s.stage.replaceAll('_',' '),`${s.total_ms} ms`,s.share_percent==null?'inclusive':`${s.share_percent}%`,`${s.p50_ms} ms`,`${s.p95_ms} ms`,`${s.count} (${s.samples} samples)`]){const cell=document.createElement('td');cell.textContent=value;row.append(cell)}root.append(row)}
}
async function performanceAction(payload){await api('/api/performance',payload);await loadSetup()}
$('performanceJob').onchange=()=>{performancePinned=true;renderPerformanceJob()};
for(const id of ['performanceEnabled','performanceResources'])$(id).onchange=safely(()=>performanceAction({action:'settings',enabled:$('performanceEnabled').checked,resources:$('performanceResources').checked}));
$('performanceStart').onclick=safely(async()=>{const minutes=Number($('performanceMinutes').value);if(!Number.isInteger(minutes)||minutes<1||minutes>30)throw Error('Choose 1–30 minutes.');await performanceAction({action:'start',seconds:minutes*60,resources:$('performanceResources').checked})});
$('performanceStop').onclick=safely(()=>performanceAction({action:'stop'}));
$('performanceClear').onclick=safely(async()=>{if(confirm('Clear saved diagnostics and disable logging?')){await performanceAction({action:'clear',confirmed:true});clearPerformance();await loadSetup()}});
$('performanceExport').onclick=safely(async()=>{const data=await api('/api/performance/export'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='imadex-performance.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
