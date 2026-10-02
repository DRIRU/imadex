let organization=null;
function discoveryFilters(){return {q:$('search').value,mode:$('searchMode').value,sort:$('sort').value,format:$('format').value,view:state.view,folder:state.folder,person:state.person,similar:state.similar,album:state.album||'',date_basis:$('dateBasis').value,from:$('dateFrom').value,to:$('dateTo').value,undated:state.undated||''}}
function applySaved(filters){
 state.view=filters.view||'all';state.folder=filters.folder||'';state.person=filters.person||'';state.similar=filters.similar||'';state.album=filters.album||'';state.undated=filters.undated||'';
 for(const [id,key] of [['search','q'],['searchMode','mode'],['sort','sort'],['format','format'],['dateBasis','date_basis'],['dateFrom','from'],['dateTo','to']])$(id).value=filters[key]||({mode:'semantic',sort:'newest',date_basis:'best'}[key]||'');
}
async function loadOrganization(){
 organization=await api('/api/gallery');$('albumList').replaceChildren();$('savedSearchList').replaceChildren();
 $('bulkAlbum').replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent='Choose album';$('bulkAlbum').append(empty);
 for(const album of organization.albums){
  const row=document.createElement('div');row.className='organizationRow';
  row.append(button(`${album.name} (${album.count})`,async()=>{state.album=String(album.id);state.view='all';$('discoveryDialog').close();await refresh()}));
  row.append(button('Rename',async()=>{const name=prompt('Album name',album.name);if(name===null)return;await api('/api/gallery',{action:'rename_album',id:album.id,name});await loadOrganization()}));
  row.append(button('Delete album',async()=>{if(!confirm(`Delete the album “${album.name}”? Photos remain in your library.`))return;await api('/api/gallery',{action:'delete_album',id:album.id});if(state.album===String(album.id))state.album='';await loadOrganization();await images()}));
  $('albumList').append(row);const option=document.createElement('option');option.value=album.id;option.textContent=album.name;$('bulkAlbum').append(option);
 }
 for(const search of organization.searches){const row=document.createElement('div');row.className='organizationRow';row.append(button(search.name,async()=>{await library();applySaved(search.filters);$('discoveryDialog').close();await refresh()}));row.append(button('Delete search',async()=>{await api('/api/gallery',{action:'delete_search',id:search.id});await loadOrganization()}));$('savedSearchList').append(row)}
 $('undoBulk').hidden=!organization.undo_id;$('undoBulk').dataset.edit=organization.undo_id||'';
}
async function loadTimeline(){
 const filters=discoveryFilters();filters.mode='text';delete filters.similar;delete filters.from;delete filters.to;delete filters.undated;
 const timeline=await api('/api/timeline?'+new URLSearchParams(filters));$('timelineMonths').replaceChildren();
 for(const group of timeline.months){$('timelineMonths').append(button(`${group.month||'Unknown date'} · ${group.count}`,async()=>{
  if(group.month){const [year,month]=group.month.split('-').map(Number);$('dateFrom').value=group.month+'-01';$('dateTo').value=group.month+'-'+new Date(year,month,0).getDate();state.undated=''}else{$('dateFrom').value='';$('dateTo').value='';state.undated='1'}
  state.similar='';$('searchMode').value='text';$('sort').value='date';$('discoveryDialog').close();await images();
 }))}
}
$('openDiscovery').onclick=safely(async()=>{$('discoveryDialog').showModal();await loadOrganization();await loadTimeline()});
$('dateBasis').onchange=safely(loadTimeline);
$('applyDates').onclick=safely(async()=>{state.undated='';$('discoveryDialog').close();await images()});
$('clearDiscovery').onclick=safely(async()=>{state.album='';state.undated='';$('dateFrom').value='';$('dateTo').value='';$('discoveryDialog').close();await images()});
$('createAlbum').onclick=safely(async()=>{await api('/api/gallery',{action:'create_album',name:$('newAlbumName').value});$('newAlbumName').value='';await loadOrganization()});
$('saveSearch').onclick=safely(async()=>{await api('/api/gallery',{action:'save_search',name:$('newSearchName').value,filters:discoveryFilters()});$('newSearchName').value='';await loadOrganization();toast('Current search saved')});
$('bulkEdit').onclick=safely(async()=>{if(!selectedPhotos.size)return toast('Select photos first');await loadOrganization();$('bulkCount').textContent=`Edit ${selectedPhotos.size} selected photos`;$('bulkDialog').showModal()});
$('applyBulk').onclick=safely(async()=>{
 const payload={action:'bulk',photos:[...selectedPhotos.values()]};
 if($('bulkReplaceTags').checked)payload.tags=$('bulkTags').value;
 if($('bulkFavorite').value!=='')payload.favorite=$('bulkFavorite').value==='1';
 if($('bulkAlbum').value){payload.album_id=Number($('bulkAlbum').value);payload.album_action=$('bulkAlbumAction').value}
 const result=await api('/api/gallery',payload);$('bulkDialog').close();selectedPhotos.clear();selecting=false;updateSelection();await refresh();await loadOrganization();toast(`Updated ${result.count} photos. Undo is available.`);
});
$('undoBulk').onclick=safely(async()=>{await api('/api/gallery',{action:'undo',id:Number($('undoBulk').dataset.edit)});await refresh();await loadOrganization();toast('Metadata edit undone')});
