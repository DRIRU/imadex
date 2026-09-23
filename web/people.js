const selectedPhotos = new Map();
let selecting = false, peopleItems = [], peopleTotal = 0, peopleRequest = 0;
let pickerItems = [], pickerTotal = 0, pickerRequest = 0, pickerPhotos = [];
let managedPerson = null, managerPeople = [], confirmation = null, photoRequest = 0;
let detectionTimer, currentPhoto = null;

function button(text, action) {
  const b = document.createElement('button'); b.textContent = text;
  b.onclick = safely(async()=>{b.disabled=true;try{await action()}finally{b.disabled=false}});
  return b;
}
function setPeopleLayout() {
  const people = state.view === 'people';
  $('peoplePanel').hidden = !people;
  for (const el of document.querySelectorAll('.toolbar,.semanticpanel,#embeddingErrors,.resultsline,#gallery,#empty,#more,#peopleTools')) el.hidden = people;
  $('managePerson').hidden = !state.person;
  $('clearPerson').hidden = !state.person;
  if (people) { $('headingCount').textContent=''; safely(detectionStatus)(); safely(recognitionStatus)(); }
  updateSelection();
}
function updateSelection() {
  $('selectPhotos').textContent = selecting?'Finish selection':'Select photos';
  $('tagSelected').hidden = !selecting || !selectedPhotos.size;
  $('selectionCount').textContent = selecting?`${selectedPhotos.size} selected · maximum 100`:'';
}
function decorateSelection(card,item) {
  if(!selecting)return;
  card.classList.toggle('selectedPhoto',selectedPhotos.has(item.id));
  card.setAttribute('aria-pressed',String(selectedPhotos.has(item.id)));
  card.setAttribute('aria-label',`Select ${item.name}`);
}
function photoClick(index) {
  if(!selecting)return view(index);
  const item=state.items[index];
  if(selectedPhotos.has(item.id)) selectedPhotos.delete(item.id);
  else if(selectedPhotos.size<100)selectedPhotos.set(item.id,{id:item.id,revision:item.revision});
  else return toast('Select at most 100 photos at a time.');
  updateSelection();render();
}
$('selectPhotos').onclick=()=>{selecting=!selecting;if(!selecting)selectedPhotos.clear();updateSelection();render()};
$('tagSelected').onclick=safely(()=>openPicker([...selectedPhotos.values()]));
$('clearPerson').onclick=safely(async()=>{state.person='';await refresh()});

async function renderPeople(append=false) {
  const request=++peopleRequest;
  $('peopleStatus').textContent='Loading people…';
  try {
    const result=await api('/api/people?'+new URLSearchParams({q:$('peopleSearch').value,offset:append?peopleItems.length:0}));
    if(request!==peopleRequest||state.view!=='people')return;
    peopleItems=append?[...peopleItems,...result.items]:result.items;peopleTotal=result.total;
    $('peopleGrid').replaceChildren();
    for(const person of peopleItems) {
      const card=button('',async()=>{state.person=String(person.id);state.personName=person.name;state.view='all';await refresh()});
      card.className='personCard';
      if(person.cover_image_id){const img=document.createElement('img');img.src='/thumb/'+person.cover_image_id;img.alt='';img.loading='lazy';img.onerror=()=>img.remove();card.append(img)}
      const name=document.createElement('strong');name.textContent=person.name;
      const count=document.createElement('span');count.textContent=`${person.count} photos · #${person.id}`;
      card.append(name,count);$('peopleGrid').append(card);
    }
    $('peopleStatus').textContent=peopleTotal?`${peopleItems.length} of ${peopleTotal} people`:'No people yet. Create a name, then label photos from your gallery.';
    $('headingCount').textContent=peopleTotal;$('peopleMore').hidden=peopleItems.length>=peopleTotal;
  } catch(error) {$('peopleStatus').textContent=error.message+' Search again to retry.';throw error}
}
let peopleDebounce,pickerDebounce;
$('peopleSearch').oninput=()=>{clearTimeout(peopleDebounce);peopleDebounce=setTimeout(safely(()=>renderPeople()),250)};
$('peopleMore').onclick=safely(()=>renderPeople(true));
$('createPerson').onclick=safely(()=>openPicker([]));

async function openPicker(photos) {
  pickerPhotos=photos;$('pickerSearch').value='';
  $('pickerSummary').textContent=photos.length?`Choose a name to add to ${photos.length} selected photo${photos.length===1?'':'s'}.`:'Create a person. You can add photos afterwards.';
  $('pickerCreate').textContent=photos.length?'Create name and assign':'Create person';
  document.querySelectorAll('.actionError').forEach(e=>e.remove());$('personPicker').showModal();$('pickerSearch').focus();await loadPicker();
}
async function loadPicker(append=false) {
  const request=++pickerRequest;$('pickerStatus').textContent='Loading names…';
  try {
    const result=await api('/api/people?'+new URLSearchParams({q:$('pickerSearch').value,offset:append?pickerItems.length:0}));
    if(request!==pickerRequest)return;
    pickerItems=append?[...pickerItems,...result.items]:result.items;pickerTotal=result.total;
    $('pickerResults').replaceChildren();
    for(const p of pickerItems)$('pickerResults').append(button(`${p.name} · ${p.count} photos · #${p.id}`,async()=>{
      if(pickerPhotos.length)await assignPerson(p.id);else toast('This name already exists. You may create another person with the same name.');
    }));
    $('pickerStatus').textContent=pickerTotal?'Select an existing person, or create a separate person.':'No matching names.';
    $('pickerMore').hidden=pickerItems.length>=pickerTotal;
  }catch(error){$('pickerStatus').textContent=error.message;throw error}
}
async function assignPerson(id) {
  await api('/api/people',{action:'assign',person_id:id,images:pickerPhotos});
  $('personPicker').close();toast(`Name added to ${pickerPhotos.length} photos.`);
  if($('viewer').open&&currentPhoto)await loadPhotoPeople(currentPhoto);
  if(state.view==='people')await renderPeople();
}
$('pickerSearch').oninput=()=>{clearTimeout(pickerDebounce);pickerDebounce=setTimeout(safely(()=>loadPicker()),250)};
$('pickerMore').onclick=safely(()=>loadPicker(true));
$('pickerCreate').onclick=safely(async()=>{
  $('pickerCreate').disabled=true;
  try {
    const p=await api('/api/people',{action:'create',name:$('pickerSearch').value});
    // Keep the newly created name available if assignment fails; do not retry creation automatically.
    await loadPicker();
    if(pickerPhotos.length)await assignPerson(p.id);
    else {$('personPicker').close();await renderPeople()}
  } finally {$('pickerCreate').disabled=false}
});

async function allPeople() {
  let items=[],result;
  do{result=await api('/api/people?offset='+items.length);items.push(...result.items)}while(items.length<result.total&&result.items.length);
  return items;
}
async function openManager() {
  managerPeople=await allPeople();managedPerson=managerPeople.find(p=>String(p.id)===state.person);
  if(!managedPerson)throw Error('Person no longer exists. Clear the filter and refresh.');
  $('personName').value=managedPerson.name;
  $('managerSummary').textContent=`${managedPerson.count} current photos; ${managedPerson.label_count} labels including unavailable or changed photos.`;
  $('mergeTarget').replaceChildren();
  for(const p of managerPeople.filter(p=>p.id!==managedPerson.id)){const o=document.createElement('option');o.value=p.id;o.textContent=`${p.name} · ${p.count} photos · #${p.id}`;$('mergeTarget').append(o)}
  $('mergePerson').disabled=managerPeople.length<2;$('managerStatus').textContent='';
  if(!$('personManager').open)$('personManager').showModal();
}
$('managePerson').onclick=safely(openManager);
$('renamePerson').onclick=safely(async()=>{
  await api('/api/people',{action:'rename',person_id:managedPerson.id,version:managedPerson.version,name:$('personName').value});
  state.personName=$('personName').value.trim();await openManager();await refresh();$('managerStatus').textContent='Name saved.';
});
function confirmChange(description,action) {
  $('confirmDescription').textContent=description;confirmation=action;$('peopleConfirm').showModal();
}
$('confirmPeopleAction').onclick=safely(async()=>{
  $('confirmPeopleAction').disabled=true;
  try {await confirmation();$('peopleConfirm').close()}finally{$('confirmPeopleAction').disabled=false}
});
$('deletePerson').onclick=()=>{
  const p=managedPerson;
  confirmChange(`Delete “${p.name}” and its ${p.label_count} labels (${p.count} current photos)? Original photos remain unchanged.`,async()=>{
    await api('/api/people',{action:'delete',person_id:p.id,version:p.version});$('personManager').close();state.person='';state.view='people';await refresh();
  });
};
$('mergePerson').onclick=()=>{
  const source=managedPerson,target=managerPeople.find(p=>p.id===Number($('mergeTarget').value));if(!target)return;
  confirmChange(`Merge “${source.name}” (${source.count} current photos) into “${target.name}” (${target.count} current photos)? Shared photos appear once. The source person and its ${source.label_count-source.count} stale/unavailable labels will be removed.`,async()=>{
    await api('/api/people',{action:'merge',person_id:source.id,version:source.version,target_id:target.id,target_version:target.version});$('personManager').close();state.person=String(target.id);state.personName=target.name;await refresh();
  });
};

async function loadPhotoPeople(item) {
  currentPhoto=item;const request=++photoRequest;clearTimeout(detectionTimer);
  $('photoPeople').textContent='Loading labels…';$('labelPhoto').disabled=true;$('detectPhoto').disabled=true;$('photoDetectionStatus').textContent='';$('facePreview').hidden=true;$('ignoreDetection').hidden=true;
  $('photoFaces').replaceChildren();$('photoRecognitionStatus').textContent='';
  $('setPersonCover').hidden=true;
  try {
    const result=await api(`/api/images/${item.id}/people`);if(request!==photoRequest)return;
    $('photoPeople').replaceChildren();
    for(const p of result.items){
      const chip=document.createElement('div');chip.className='personChip';
      const name=document.createElement('span');name.textContent=p.name+(p.stale?' · photo changed; review label':'');chip.append(name);
      if(p.stale)chip.append(button('Keep label',async()=>{await api('/api/people',{action:'assign',person_id:p.id,images:[{id:item.id,revision:result.revision}]});await loadPhotoPeople(item)}));
      chip.append(button('Remove '+p.name,async()=>{await api('/api/people',{action:'unassign',person_id:p.id,images:[{id:item.id,revision:result.revision}]});await loadPhotoPeople(item)}));
      $('photoPeople').append(chip);
    }
    if(!result.items.length)$('photoPeople').textContent='No names assigned.';
    $('labelPhoto').disabled=false;
    $('labelPhoto').onclick=safely(()=>openPicker([{id:item.id,revision:result.revision}]));
    const person=result.items.find(p=>String(p.id)===state.person&&!p.stale);
    $('setPersonCover').hidden=!person;
    $('setPersonCover').onclick=safely(async()=>{await api('/api/people',{action:'cover',person_id:person.id,version:person.version,image_id:item.id});toast('Album cover saved.');await loadPhotoPeople(item)});
    await photoDetection(item,request);
    await photoRecognition(item,request);
  }catch(error){if(request===photoRequest)$('photoPeople').textContent=error.message;throw error}
}
async function detectionStatus() {
  const s=await api('/api/detection');$('enableDetection').checked=s.enabled;
  $('detectionStatus').textContent=s.enabled?`${s.pending} pending · ${s.failed} failed${s.running?' · detecting…':''}`:'Disabled. Manual labels work without detection.';
  $('queueDetection').disabled=!s.enabled;
}
$('enableDetection').onchange=safely(async()=>{await api('/api/detection',{action:'enable',enabled:$('enableDetection').checked});await detectionStatus()});
$('queueDetection').onclick=safely(async()=>{await api('/api/detection',{action:'queue'});await detectionStatus();toast('Detection queued. You can keep browsing.')});
$('clearDetection').onclick=()=>confirmChange('Clear all detected regions and pending detection jobs? Your manual people labels remain.',async()=>{await api('/api/detection',{action:'clear'});await detectionStatus()});
$('reviewDetection').onclick=safely(async()=>{state.view='review';state.person='';state.folder='';await refresh()});
async function photoDetection(item,request) {
  const s=await api('/api/detection?image_id='+item.id);if(request!==photoRequest||!$('viewer').open)return;
  const d=s.image;$('detectPhoto').disabled=!s.enabled||d.status==='pending';
  $('photoDetectionStatus').textContent=!s.enabled?'Enable optional face detection from People.':(d.error||({unprocessed:'Detection has not run on this photo.',pending:'Finding face regions…',ready:`${d.regions.length} possible face regions. Names are always manual.`}[d.status]||d.status));
  $('facePreview').hidden=!s.enabled||!d.regions.length;$('ignoreDetection').hidden=$('facePreview').hidden;
  if(d.regions.length&&s.enabled){
    $('facePreviewImage').src='/thumb/'+item.id;$('faceBoxes').replaceChildren();
    for(const r of d.regions){const box=document.createElement('span');box.style.cssText=`left:${r.x*100}%;top:${r.y*100}%;width:${r.width*100}%;height:${r.height*100}%`;box.title='Possible face';$('faceBoxes').append(box)}
  }
  $('detectPhoto').onclick=safely(async()=>{await api('/api/detection',{action:'detect',image_id:item.id,revision:item.revision});await photoDetection(item,request)});
  $('ignoreDetection').onclick=safely(async()=>{await api('/api/detection',{action:'ignore',image_id:item.id,revision:item.revision});await photoDetection(item,request)});
  if(s.enabled&&d.status==='pending')detectionTimer=setTimeout(safely(()=>photoDetection(item,request)),1500);
}
$('viewer').addEventListener('close',()=>{++photoRequest;clearTimeout(detectionTimer)});
setInterval(()=>{if(!document.hidden&&state.view==='people'){safely(detectionStatus)();safely(recognitionStatus)()}},5000);

async function recognitionStatus() {
  const s=await api('/api/recognition');
  $('enableRecognition').checked=s.enabled;
  $('autoThreshold').value=s.auto_threshold;$('reviewThreshold').value=s.review_threshold;
  $('recognitionStatus').textContent=s.available?(s.enabled?`${s.faces} faces recognized · ${s.pending} pending · ${s.failed} failed${s.running?' · working…':''}`:'Disabled. Enable to group matching faces into albums.'):'Model not installed. Run download_arcface.py, then restart.';
  $('queueRecognition').disabled=!s.enabled;
  await loadRecognitionSuggestions();
}
async function loadRecognitionSuggestions() {
  const box=$('recognitionSuggestions');box.replaceChildren();
  const result=await api('/api/recognition/suggestions?offset=0');
  if(!result.total)return;
  const heading=document.createElement('p');heading.textContent=`${result.total} face match${result.total===1?'':'es'} to review:`;
  box.append(heading);
  for(const item of result.items){
    const row=document.createElement('div');row.className='recognitionSuggestion';
    const img=document.createElement('img');img.src='/thumb/'+item.image_id;img.alt='';img.loading='lazy';img.onerror=()=>img.remove();
    const text=document.createElement('span');text.textContent=`${item.person_name} · ${Math.round(item.match_score*100)}%`;
    row.append(img,text,
      button('Confirm',async()=>{await api('/api/recognition',{action:'confirm',face_id:item.id});await recognitionStatus();toast('Added to '+item.person_name+'.')}),
      button('Not them',async()=>{await api('/api/recognition',{action:'reject',face_id:item.id});await recognitionStatus()}));
    box.append(row);
  }
}
$('enableRecognition').onchange=safely(async()=>{await api('/api/recognition',{action:'enable',enabled:$('enableRecognition').checked});await recognitionStatus()});
$('saveThresholds').onclick=safely(async()=>{await api('/api/recognition',{action:'settings',auto_threshold:$('autoThreshold').value,review_threshold:$('reviewThreshold').value});await recognitionStatus();toast('Thresholds saved.')});
$('queueRecognition').onclick=safely(async()=>{await api('/api/recognition',{action:'queue'});await recognitionStatus();toast('Recognition queued. You can keep browsing.')});
$('clearRecognition').onclick=()=>confirmChange('Clear all recognized faces and pending recognition jobs? Your people labels and albums remain.',async()=>{await api('/api/recognition',{action:'clear'});await recognitionStatus()});
async function photoRecognition(item,request) {
  const box=$('photoFaces');box.replaceChildren();$('photoRecognitionStatus').textContent='';
  try {
    const result=await api('/api/recognition/faces?image_id='+item.id);if(request!==photoRequest||!$('viewer').open)return;
    if(!result.items.length){$('photoRecognitionStatus').textContent='No recognized faces.';return}
    for(const f of result.items){
      const row=document.createElement('div');row.className='personChip';
      const label=document.createElement('span');
      label.textContent=(f.person_name||'Unmatched')+(f.status==='suggested'?' · review':(f.status==='rejected'?' · dismissed':(f.current?'':' · photo changed')));
      row.append(label);
      if(f.status==='suggested'){row.append(button('Confirm '+f.person_name,async()=>{await api('/api/recognition',{action:'confirm',face_id:f.id});await photoRecognition(item,request)}));row.append(button('Not them',async()=>{await api('/api/recognition',{action:'reject',face_id:f.id});await photoRecognition(item,request)}))}
      box.append(row);
    }
  } catch(error){if(request===photoRequest)$('photoRecognitionStatus').textContent=error.message}
}
document.querySelectorAll('#personPicker [data-close],#personManager [data-close],#peopleConfirm [data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
