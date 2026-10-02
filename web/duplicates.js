let duplicateOffset=0,duplicatePairs=[],duplicateBusy=false;
async function duplicateCandidates(append=false){
 if(duplicateBusy)return;duplicateBusy=true;$('scanCandidates').disabled=true;
 try{
  if(!append){duplicateOffset=0;duplicatePairs=[];$('duplicatePairs').replaceChildren()}
  const filters=discoveryFilters();filters.mode='text';delete filters.similar;filters.offset=duplicateOffset;filters.threshold=$('duplicateThreshold').value;
  const result=await api('/api/near-duplicates?'+new URLSearchParams(filters));duplicateOffset=result.next_offset;
  $('candidateStatus').textContent=`${result.total_indexed} indexed photos · ${result.message}${result.next_offset===null?' Candidate scan finished.':' More photos remain to check.'}`;
  for(const pair of result.pairs){duplicatePairs.push(pair);const row=document.createElement('div');row.className='duplicatePair';
   const previews=document.createElement('div');previews.className='comparePreviews';
   for(const photo of [pair.left,pair.right]){const figure=document.createElement('figure'),img=document.createElement('img'),caption=document.createElement('figcaption');img.loading='lazy';img.src=`/thumb/${photo.id}?v=${encodeURIComponent(photo.revision)}`;img.alt=photo.name;caption.textContent=`${photo.name} · ${photo.width} × ${photo.height} · ${bytes(photo.size)}`;figure.append(img,caption);previews.append(figure)}
   const score=document.createElement('p');score.textContent=`Content similarity ${pair.score.toFixed(3)}; this is not a duplicate probability.`;
   const review=async decision=>{await api('/api/gallery',{action:'review_duplicate',decision,photos:[pair.left,pair.right].map(p=>({id:p.id,revision:p.revision}))});row.replaceChildren();const status=document.createElement('p');status.textContent=decision==='confirmed'?'Marked as duplicate copies. Both originals remain.':'Marked as different photos.';row.append(status,button('Undo review',async()=>{await api('/api/gallery',{action:'review_duplicate',decision:'reset',photos:[pair.left,pair.right].map(p=>({id:p.id,revision:p.revision}))});await duplicateCandidates()}))};
   row.append(previews,score,button('These are duplicate copies',()=>review('confirmed')),button('These are different photos',()=>review('different')));$('duplicatePairs').append(row);
  }
  $('moreCandidates').hidden=result.next_offset===null;
  if(!duplicatePairs.length){const p=document.createElement('p');p.textContent='No unreviewed pairs in this batch. Continue checking if more photos remain.';$('duplicatePairs').append(p)}
 }finally{duplicateBusy=false;$('scanCandidates').disabled=false}
}
$('openDuplicateReview').onclick=safely(async()=>{$('discoveryDialog').close();$('duplicateDialog').showModal();await duplicateCandidates()});
$('scanCandidates').onclick=safely(()=>duplicateCandidates());$('moreCandidates').onclick=safely(()=>duplicateCandidates(true));
