/* Workspace navigation, reusable character library and project casting.
   Source parsing and voice resolution remain authoritative on the server. */
let studioPage='text', selectedRoleId='', roleEditorDirty=false, roleSaveBusy=false;
let roleEditorDraft=null, roleCardsKey='', bookCardsKey='', textPreviewKey='', castingKey='';
let roleNavigation=0,roleRevision=0,currentProcessKind='';
const roleDrafts=new Map(), castingDrafts=new Map(), castingSaves=new Map(), workspaceScroll=new Map();
const roleDefaults={name:'',gpt:'',sovits:'',reference:'',prompt:'',prompt_lang:'zh',text_lang:'zh',speed:1,seed:42,gap:.3};
const roleProfileFields={description:'roleDescription',tags:'roleTags',color:'roleColor',avatar:'roleAvatar',portrait:'rolePortrait'};
const pageCopy={
 engine:['配置 / 引擎','连接本地的声音引擎','管理引擎、模型素材与运行状态。'],
 roles:['配置 / 角色','让每个声音，都有自己的角色','收藏声音与形象，组成你的专属配音阵容。'],
 text:['文本','把故事，交给合适的声音','整理正文、预览分段，为每种声线安排角色。'],
 inference:['推理','从文字，到听得见的作品','逐段生成，随时试听，完成后导出作品。']
};
function node(tag,className,text){const e=document.createElement(tag);if(className)e.className=className;if(text!==undefined)e.textContent=text;return e}
function put(id,text){if($(id)&&$(id).textContent!==String(text))$(id).textContent=text}
function roleProfile(){return {description:$('roleDescription').value.trim(),tags:$('roleTags').value.split(/[,，、\n]/).map(t=>t.trim()).filter(Boolean),color:$('roleColor').value,avatar:$('roleAvatar').value.trim(),portrait:$('rolePortrait').value.trim()}}
function editorSnapshot(){return {voice:currentVoice(),profile:roleProfile()}}
function captureRoleEditor(){roleEditorDraft={...editorSnapshot(),dirty:roleEditorDirty};roleDrafts.set(selectedRoleId,roleEditorDraft)}
// Character editing belongs to the library, independently of the selected book.
rememberDrafts=function(){};
function restoreRoleEditor(){
 const draft=roleEditorDraft||{voice:roleDefaults,profile:{}};
 fields.forEach(f=>$(f).value=draft.voice[f]??roleDefaults[f]??'');
 for(const [key,id] of Object.entries(roleProfileFields))$(id).value=key==='tags'?(draft.profile.tags||[]).join('，'):draft.profile[key]||(key==='color'?'#3978ba':'');
 $('presets').value=selectedRoleId;syncAssetChoices();renderRolePreview();updateRoleActions();
}
function updateRoleActions(){
 const locked=roleSaveBusy||terminating||exited;
 $('voiceCard').classList.remove('hidden');
 $('voiceForm').querySelectorAll('input,textarea,select,button').forEach(e=>e.disabled=locked);
 $('updatePreset').disabled=locked||!selectedRoleId;$('deleteRole').disabled=locked||!selectedRoleId;
 $('applyRoleToBook').disabled=locked||!project||!!active||scriptIsDirty();
 put('applyRoleToBook',uiText('保存并设为默认角色'));
 put('roleEditorTitle',selectedRoleId?(presets.find(p=>p.id===selectedRoleId)?.name||uiText('编辑角色')):uiText('新建角色'));
}
function roleImage(role,kind){return role?.profile?.[kind]?'/api/role-image?preset_id='+encodeURIComponent(role.id)+'&kind='+kind:''}
function roleArtwork(role,portrait=false){
 const art=node('div','role-artwork');const profile=role?.profile||{};art.style.setProperty('--role-color',/^#[\da-f]{6}$/i.test(profile.color||'')?profile.color:'#3978ba');
 const src=roleImage(role,portrait&&profile.portrait?'portrait':'avatar');
 const initial=node('span','role-initial',(role?.name||uiText('声')).slice(0,1));art.append(initial);
 if(src){const img=node('img','role-image');img.src=src;img.alt=portrait?uiText('{name}的形象',{name:role.name}):uiText('{name}的头像',{name:role.name});img.loading='lazy';img.onerror=()=>img.remove();art.append(img)}
 return art;
}
function renderRolePreview(){
 const snapshot=editorSnapshot(),saved=presets.find(p=>p.id===selectedRoleId),role={id:selectedRoleId,name:snapshot.voice.name||uiText('未命名角色'),profile:{...snapshot.profile}};
 // Unsaved images are read only after their path is registered by saving.
 if(role.profile.avatar!==saved?.profile?.avatar)role.profile.avatar='';if(role.profile.portrait!==saved?.profile?.portrait)role.profile.portrait='';
 const preview=$('rolePreview');preview.replaceChildren(roleArtwork(role,true));
 const info=node('div','role-preview-info');info.append(node('strong','',role.name),node('p','muted',snapshot.profile.description||uiText('为这个声音写一段介绍。')));
 const tags=node('div','role-tags');for(const tag of snapshot.profile.tags)tags.append(node('span','role-tag',tag));info.append(tags);preview.append(info);
}
function selectRole(id,remember=true){
 if(remember)captureRoleEditor();selectedRoleId=id;roleNavigation++;roleRevision++;
 const role=presets.find(p=>p.id===id);roleEditorDraft=roleDrafts.get(id)||{voice:{...roleDefaults,...role?.voice},profile:{...(role?.profile||{})}};
 roleEditorDirty=!!roleEditorDraft.dirty;restoreRoleEditor();roleCardsKey='';renderRoleCards();
 put('presetNotice',roleEditorDirty?uiText('此角色有未保存的修改。'):id?uiText('角色已载入。修改后保存，可在文本页安排配音。'):uiText('选择模型和参考音频，保存你的第一个角色。'));
}
function renderRoleCards(){
 const query=$('roleSearch').value.trim().toLowerCase(),shown=presets.filter(p=>[p.name,p.profile?.description,...(p.profile?.tags||[])].join(' ').toLowerCase().includes(query));
 const key=JSON.stringify([shown,selectedRoleId]);if(key===roleCardsKey)return;roleCardsKey=key;put('roleCount',uiText('{count} 位角色',{count:presets.length}));
 const cards=$('roleCards');cards.replaceChildren();
 if(!shown.length){const empty=node('div','role-empty');empty.append(node('span','empty-symbol','♫'),node('h3','',presets.length?uiText('没有找到匹配的角色'):uiText('你的声音阵容，从这里开始')),node('p','muted',presets.length?uiText('试试其他名称或声线标签。'):uiText('保存模型、参考台词和角色形象，让声音在不同作品间复用。')));cards.append(empty)}
 for(const role of shown){const card=node('button','role-card'+(role.id===selectedRoleId?' selected':''));card.type='button';card.setAttribute('aria-pressed',String(role.id===selectedRoleId));card.append(roleArtwork(role,true));
  const body=node('div','role-card-body');body.append(node('strong','',role.name),node('p','muted',role.profile?.description||uiText('本地声音角色')));
  const tags=node('div','role-tags');for(const tag of role.profile?.tags||[])tags.append(node('span','role-tag',tag));body.append(tags,node('small','role-card-meta',({zh:uiText('中文'),ja:uiText('日语'),en:uiText('英语'),auto:uiText('多语种')})[role.voice.text_lang]+' · '+Number(role.voice.speed||1).toFixed(2)+'×'));card.append(body);card.onclick=()=>{selectRole(role.id);$('voiceCard').scrollIntoView({behavior:'smooth',block:'start'})};cards.append(card)
 }
}
const loadPresetsBeforeRebuild=loadPresets;
loadPresets=async function(selected){await loadPresetsBeforeRebuild(selected??selectedRoleId);renderRoleCards();castingKey='';renderCasting();updateRoleActions()};
persistPreset=async function(update){
 if(roleSaveBusy)return null;if(update&&!selectedRoleId)throw Error(uiText('请先选择要更新的角色'));
 roleSaveBusy=true;updateRoleActions();const draft=editorSnapshot(),roleId=selectedRoleId,view=roleNavigation,revision=roleRevision;
 try{const saved=await api('preset',{voice:draft.voice,profile:draft.profile,preset_id:update?roleId:null});
  await loadPresets(view===roleNavigation?saved.id:selectedRoleId);
  if(view===roleNavigation&&revision===roleRevision){selectedRoleId=saved.id;roleEditorDraft=draft;roleEditorDirty=false;roleDrafts.delete(roleId);roleDrafts.delete(saved.id);roleCardsKey='';restoreRoleEditor();renderRoleCards();put('presetNotice',uiText('“{name}”已保存。到文本页为作品安排配音。',{name:saved.name}))}
  notify(uiText('角色“{name}”已保存',{name:saved.name}));return saved;
 }finally{roleSaveBusy=false;updateRoleActions()}
};
$('savePreset').onclick=()=>safe(()=>persistPreset(false));$('updatePreset').onclick=()=>safe(()=>persistPreset(true));
$('newRole').onclick=()=>selectRole('');$('resetRole').onclick=()=>{roleDrafts.delete(selectedRoleId);selectRole(selectedRoleId,false)};
$('loadPreset').onclick=()=>selectRole($('presets').value);$('presets').onchange=()=>selectRole($('presets').value);
$('roleSearch').oninput=renderRoleCards;
for(const id of [...fields,...Object.values(roleProfileFields)])$(id).addEventListener('input',()=>{roleRevision++;roleEditorDirty=true;captureRoleEditor();renderRolePreview();put('presetNotice',uiText('修改尚未保存。保存后可在文本页为作品安排配音。'))});
for(const [id,kind,field] of [['pickRoleAvatar','avatar','roleAvatar'],['pickRolePortrait','portrait','rolePortrait']])$(id).onclick=()=>safe(async()=>{
 const owner=selectedRoleId,view=roleNavigation,revision=roleRevision,result=await api('pick-path',{kind,initial:$(field).value});if(result.cancelled||!result.path||owner!==selectedRoleId||view!==roleNavigation||revision!==roleRevision)return;$(field).value=result.path;roleRevision++;roleEditorDirty=true;captureRoleEditor();renderRolePreview();put('presetNotice',uiText('形象已选择，保存角色后显示图片。'))
});
$('deleteRole').onclick=()=>safe(async()=>{const role=presets.find(p=>p.id===selectedRoleId);if(!role)return;if(!confirm(uiText('删除角色“{name}”？已绑定的作品会保留声音配置。',{name:role.name})))return;const view=roleNavigation;await api('delete-preset',{preset_id:role.id});roleDrafts.delete(role.id);await loadPresets();if(view===roleNavigation)selectRole('',false);notify(uiText('角色已从角色库移除，已绑定作品的配音保留'))});
$('voiceForm').onsubmit=e=>{e.preventDefault();return safe(async()=>{
 if(!project)throw Error(uiText('请先创建或选择作品'));const owner=project.id,turn=navigation,draft=castingDraft(),revision=draft.revision,previousDefault=draft.default_role_id,bindings={...draft.voice_bindings};
 const saved=await persistPreset(!!selectedRoleId);if(!saved||project?.id!==owner||turn!==navigation)return;
 const result=await api('project/voices',{id:owner,default_role_id:saved.id,voice_bindings:bindings,refresh_role_ids:[saved.id]});
 if(draft.revision===revision&&castingDrafts.get(owner)===draft)castingDrafts.delete(owner);else if(draft.default_role_id===previousDefault){draft.default_role_id=saved.id;draft.revision++}
 if(project?.id!==owner||turn!==navigation)return;project=result.project||result;signature='';render();notify(uiText('已将“{name}”设为当前作品默认角色',{name:saved.name}));navigateStudio('text')
})};
// Local asset selection also works before any book exists.
chooseCatalogAsset=function(kind){const path=$(assetFields[kind]).value,item=(assetCatalog[kind]||[]).find(c=>c.path===path);if(!item)return;$(kind).value=path;const paired=fillRelatedAsset(kind,item);roleRevision++;roleEditorDirty=true;captureRoleEditor();syncAssetChoices();assetStatus(paired.length?uiText('文件已填入，同时找到 {names}。保存角色后生效。',{names:paired.join(' · ')}):uiText('文件已填入，保存角色后生效。'))};
chooseLocalFile=async function(kind,field){
 const owner=selectedRoleId,view=roleNavigation,revision=roleRevision,editor=editing,turn=navigation,result=await api('pick-path',{kind,initial:$(field).value});if(result.cancelled||!result.path)return;
 if(field==='editReference'){if(editor!==editing||turn!==navigation||!$('segmentEditor').open)return;$(field).value=result.path;return}
 if(owner!==selectedRoleId||view!==roleNavigation||revision!==roleRevision)return;$(field).value=result.path;roleRevision++;roleEditorDirty=true;captureRoleEditor();syncAssetChoices();assetStatus(uiText('文件已填入，保存角色后生效。'));
};
function castingDraft(){if(!project)return null;const baseline=JSON.stringify([project.default_role_id||'',project.voice_bindings||{}]);let draft=castingDrafts.get(project.id);if(!draft||!draft.dirty&&draft.baseline!==baseline){draft={default_role_id:project.default_role_id||'',voice_bindings:{...(project.voice_bindings||{})},dirty:false,revision:(draft?.revision||0)+1,baseline};castingDrafts.set(project.id,draft)}return draft}
function voiceLabels(){
 const s=scriptDraft();if(s.format!=='pcs')return [];
 if(s.compiled)return [...new Set((s.compiled.segments||[]).map(n=>n.voice_label).filter(Boolean))];
 if(s.parse){const labels=[];let label=null;for(const n of s.parse.ast||[]){if(n.type==='control'&&n.command==='voice')label=String(n.value);if(n.type==='text'&&n.text?.trim()&&label&&!labels.includes(label))labels.push(label)}return labels}
 return s.dirty?[]:(project?.voice_labels||[...new Set((project?.segments||[]).map(s=>s.voice_label).filter(Boolean))]);
}
function roleOptions(selected,empty){
 const options=[new Option(empty,'')];presets.forEach(p=>options.push(new Option(p.name,p.id)));
 if(selected&&!presets.some(p=>p.id===selected)){const snapshot=project?.role_snapshots?.[selected];options.push(new Option(uiText('{name} · 作品保留',{name:snapshot?.name||snapshot?.voice?.name||uiText('已保存角色')}),selected))}return options;
}
function defaultVoiceReady(){return !project||!project.segments.some(s=>!s.voice_label)||['gpt','sovits','reference','prompt'].every(key=>String(project.voice?.[key]||'').trim())}
function renderCasting(){
 if(!$('voiceBindings'))return;const draft=castingDraft(),labels=voiceLabels(),blocked=!project||!!active||scriptIsDirty();
 const key=JSON.stringify([project?.id,labels,presets,project?.role_snapshots,draft,blocked,castingSaves.has(project?.id)]);if(key!==castingKey){castingKey=key;
  $('defaultRole').replaceChildren(...roleOptions(draft?.default_role_id||'',defaultVoiceReady()?uiText('沿用作品当前音色'):uiText('请选择默认角色')));$('defaultRole').value=draft?.default_role_id||'';$('defaultRole').disabled=blocked;
  const bindings=$('voiceBindings');bindings.replaceChildren();
  for(const label of labels){const row=node('div','voice-binding-row');const copy=node('div','binding-feature');copy.append(node('strong','',label),node('small','muted',uiText('脚本声线')));const select=node('select');select.setAttribute('aria-label',uiText('为 {label} 绑定角色',{label}));select.dataset.voiceLabel=label;const selected=draft?.voice_bindings[label]||'';select.append(...roleOptions(selected,uiText('请选择配音角色')));select.value=selected;select.disabled=blocked;select.onchange=()=>{draft.voice_bindings[label]=select.value;draft.dirty=true;draft.revision++;castingKey='';renderCasting();renderRebuild()};row.append(copy,node('span','binding-arrow','→'),select);bindings.append(row)}
  if(!labels.length)bindings.append(node('p','muted',scriptDraft().format==='txt'?uiText('TXT 使用上方默认角色，全文由同一个声音朗读。'):uiText('没有指定额外声线，全文使用默认角色。')));
 }
 $('saveBindings').disabled=blocked||!draft?.dirty||castingSaves.has(project?.id);put('saveBindings',castingSaves.has(project?.id)?uiText('正在保存…'):uiText('保存角色安排'));
 bindingSyncButton.disabled=blocked||castingSaves.has(project?.id)||![draft?.default_role_id,...Object.values(draft?.voice_bindings||{})].some(id=>presets.some(p=>p.id===id));
 const errors=project?.voice_binding_errors||[],missing=labels.filter(label=>!draft?.voice_bindings[label]);
 put('bindingNotice',!project?uiText('保存文本后，即可为这部作品安排角色。'):scriptIsDirty()?uiText('先应用文本修改，再保存角色安排。'):draft?.dirty?uiText('角色安排有未保存的修改。保存后仅受影响的片段需要重新生成。'):!defaultVoiceReady()?uiText('请选择并保存默认角色，让旁白与普通文本拥有自己的声音。'):missing.length?uiText('还有 {count} 种声线等待绑定角色。',{count:missing.length}):errors.length?uiText('配音尚未准备好：{errors}',{errors:errors.map(message=>uiText(message)).join(' · ')}):uiText('角色安排已保存。每段会使用对应角色的模型与参考音频。'));
}
$('defaultRole').onchange=()=>{const draft=castingDraft();if(!draft)return;draft.default_role_id=$('defaultRole').value;draft.dirty=true;draft.revision++;castingKey='';renderCasting();renderRebuild()};
async function saveCasting(refresh=false){const draft=castingDraft();if(!draft)return;if(scriptIsDirty())throw Error(uiText('请先应用文本修改'));const owner=project.id,turn=navigation;
 if(castingSaves.has(owner))return;const labels=voiceLabels();if(labels.some(label=>!draft.voice_bindings[label]))throw Error(uiText('请为每种脚本声线选择角色'));
 const revision=draft.revision;castingSaves.set(owner,true);renderCasting();
 const refreshIds=refresh?[...new Set([draft.default_role_id,...labels.map(label=>draft.voice_bindings[label])])].filter(id=>presets.some(p=>p.id===id)):[];
 try{const result=await api('project/voices',{id:owner,default_role_id:draft.default_role_id,voice_bindings:Object.fromEntries(labels.map(label=>[label,draft.voice_bindings[label]])),...(refresh?{refresh_role_ids:refreshIds}:{})});
  if(draft.revision===revision&&castingDrafts.get(owner)===draft)castingDrafts.delete(owner);if(project?.id!==owner||turn!==navigation)return;project=result.project||result;castingKey='';signature='';render();notify(refresh?uiText('已同步角色库配置；未改变的片段保留音频'):uiText('角色安排已保存；未改变的片段保留音频'))
 }finally{castingSaves.delete(owner);castingKey='';if(project?.id===owner)renderRebuild()}
}
$('saveBindings').onclick=()=>safe(()=>saveCasting());
const bindingSyncButton=node('button','',uiText('同步角色库配置'));bindingSyncButton.type='button';bindingSyncButton.title=uiText('主动更新作品内已保存的声音配置，只同步仍在角色库中的角色');bindingSyncButton.onclick=()=>safe(()=>saveCasting(true));$('saveBindings').after(bindingSyncButton);
function castingIsDirty(){return !!project&&!!castingDrafts.get(project.id)?.dirty}
function navigateStudio(next){
 if(!pageCopy[next])return;const previous=studioPage;if(previous!==next&&typeof window!=='undefined')workspaceScroll.set(previous,window.scrollY||0);studioPage=next;
 document.querySelectorAll('[data-studio-page]').forEach(e=>{const visible=e.dataset.studioPage===next;e.classList.toggle('hidden',!visible);e.classList.toggle('active',visible)});
 document.querySelectorAll('[data-page]').forEach(e=>{const selected=e.dataset.page===next||(e.dataset.page==='roles'&&next==='engine'&&e.classList.contains('nav-item'));e.classList.toggle('active',selected);e.setAttribute('aria-current',selected?'page':'false')});
 const copy=pageCopy[next];put('pageTitle',uiText(copy[1]));put('pageSubtitle',uiText(copy[2]));document.body.dataset.page=next;
 try{sessionStorage.setItem('kudioWorkspace',next)}catch{}renderRebuild();if(previous!==next&&typeof window!=='undefined')window.scrollTo?.({top:workspaceScroll.get(next)||0,left:0,behavior:'instant'});
}
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>navigateStudio(b.dataset.page));
const mobileLibraryToggle=node('button','mobile-library-toggle',uiText('展开作品目录 ＋'));mobileLibraryToggle.type='button';mobileLibraryToggle.setAttribute('aria-expanded','false');document.body.classList.add('mobile-library-collapsed');document.querySelector('.main-nav')?.after(mobileLibraryToggle);mobileLibraryToggle.onclick=()=>{const collapsed=document.body.classList.toggle('mobile-library-collapsed');mobileLibraryToggle.textContent=collapsed?uiText('展开作品目录 ＋'):uiText('收起作品目录 −');mobileLibraryToggle.setAttribute('aria-expanded',String(!collapsed))};
const renderLibraryBeforeRebuild=renderLibrary;
renderLibrary=function(){if(project){const current=library.find(p=>p.id===project.id);if(current)Object.assign(current,{title:project.title,count:project.segments.length,done:project.segments.filter(s=>s.status==='done').length,archived:!!project.archived})}renderLibraryBeforeRebuild();if(!$('bookCards'))return;const query=$('bookSearch').value.trim().toLowerCase(),shown=library.filter(p=>(!p.archived||$('showArchived').checked||p.id===project?.id)&&(!query||p.title.toLowerCase().includes(query)));
 const key=JSON.stringify([shown,project?.id,loading]);if(key===bookCardsKey)return;bookCardsKey=key;const cards=$('bookCards');cards.replaceChildren();
 for(const book of shown){const b=node('button','book-card'+(project?.id===book.id?' selected':''));b.type='button';b.setAttribute('aria-current',project?.id===book.id?'true':'false');b.append(node('span','book-icon','▤'),node('strong','book-name',book.title),node('small','book-progress',book.archived?uiText('已归档 · {done}/{count} 段',{done:book.done,count:book.count}):uiText('{done}/{count} 段',{done:book.done,count:book.count})));b.onclick=()=>safe(()=>load(book.id));cards.append(b)}
 if(!shown.length)cards.append(node('p','library-empty',query?uiText('没有匹配作品'):uiText('还没有作品。新建一部，开始让文字发声。')));
};
function renderTextSegments(){
 const draft=scriptDraft(),preview=draft.compiled;const segments=preview?.segments||(!scriptIsDirty()?project?.segments:[])||[];
 const key=JSON.stringify([project?.id,draft.format,draft.revision,preview,project?.compilation_stale,segments.map(s=>[s.id,s.text,s.status,s.page,s.section,s.chapter,s.resolved_role_name,s.voice_label])]);if(key===textPreviewKey)return;textPreviewKey=key;
 put('textSegmentCount',uiText('{count} 个片段',{count:segments.length}));const list=$('textSegments');list.replaceChildren();
 if(!segments.length){list.append(node('p','empty',project?.compilation_stale?uiText('需重新编译 · 应用脚本后更新计划和时间轴'):draft.dirty?uiText('点击“预览分段”查看修改后的朗读片段。'):uiText('导入或输入文本后，预览你的朗读片段。')));return}
 const shown=segments.slice(0,60);for(const [index,s] of shown.entries()){
  const row=node('article','text-segment');const head=node('div','text-segment-head');head.append(node('span','segment-number',String(index+1).padStart(2,'0')),node('span','segment-role',s.resolved_role_name||s.voice_label||project?.voice?.name||uiText('默认角色')));
  if(s.page!==null&&s.page!==undefined)head.append(node('span','muted',uiText('第 {page} 页',{page:s.page})));if(draft.format==='pcs'&&s.section)head.append(node('span','muted',uiText('SECTION {value}',{value:s.section})));else if(draft.format==='txt'&&s.chapter&&s.chapter!=='正文')head.append(node('span','muted',uiText('章节 · {name}',{name:s.chapter})));row.append(head,node('p','',s.text));
  if(project&&project.segments.some(x=>x.id===s.id)&&s.status==='done')row.append(button(uiText('试听'),()=>playSegment(s.id)));list.append(row)
 }if(segments.length>shown.length)list.append(node('p','muted',uiText('这里只显示前 60 段，推理页可搜索并查看全部 {count} 段。',{count:segments.length})));
}
function processSummary(kind){
 currentProcessKind=kind;
 const draft=scriptDraft(),segments=project?.segments||[],done=segments.filter(s=>s.status==='done').length;
 const text={source:[uiText('文本准备'),draft.format==='pcs'?uiText('控制脚本已选择。正文与页码、停顿、语速、章节、声线指令在这里共同编排。'):uiText('普通文本会按自然段、标点与长度整理为朗读片段。')],parse:[uiText('文本整理'),uiText('按顺序整理正文与控制指令，发现问题时可点击提示回到原文。')],voices:[uiText('角色安排'),uiText('默认角色朗读普通文本和未指定声线的内容。脚本指定的声线在文本页绑定到具体角色。')],synthesis:[uiText('语音生成'),active===project?.id?uiText('正在按顺序生成音频。暂停会等待当前片段完成。'):uiText('每个片段使用对应角色的模型和参考台词，可以试听后单独重做。')],timeline:[uiText('声音时间轴'),project?.timeline?.timing_status==='exact'?uiText('所有音频已经完成，时长来自实际音频。'):uiText('生成完成的片段使用真实时长，其余时长为估算。')],export:[uiText('作品导出'),uiText('全部音频生成完成后，可导出 WAV、字幕和时间轴。分组导出在推理页的高级工具中。')]}[kind];
 put('processTitle',text[0]);put('processDescription',text[1]);
 const body=$('processBody');body.replaceChildren();
 document.querySelectorAll('[data-process-panel]').forEach(e=>e.classList.toggle('hidden',!(kind==='parse'&&e.id==='parsePanel'||kind==='synthesis'&&e.id==='planPanel')));
 if(kind==='source'){body.append(node('p','process-metric',uiText('{format} · {count} 字',{format:draft.format==='pcs'?'PCS':'TXT',count:Array.from(draft.text).length.toLocaleString()})),node('p','muted',draft.dirty?uiText('文字有未应用的修改。'):uiText('文字已与当前作品同步。')))}
 if(kind==='parse'){const count=draft.compiled?.segments?.length??(!scriptIsDirty()?segments.length:0);body.append(node('p','process-metric',count?uiText('{count} 个朗读片段',{count}):uiText('预览后查看朗读片段')));renderScriptParse(draft)}
 if(kind==='voices'){body.append(node('p','process-metric',voiceLabels().length?uiText('{count} 种脚本声线',{count:voiceLabels().length}):uiText('单角色配音')),node('p','muted',$('bindingNotice').textContent))}
 if(kind==='synthesis'){body.append(node('p','process-metric',uiText('{done} / {count} 段已完成',{done,count:segments.length})));$('planPanel').open=true}
 if(kind==='timeline'){body.append(node('p','process-metric',project?.compilation_stale?uiText('需重新编译'):timecode((project?.timeline?.duration_ms||0)/1000)),node('p','muted',project?.compilation_stale?uiText('应用脚本后，重新建立权威时间轴。'):project?.timeline?.timing_status==='exact'?uiText('实测时长'):uiText('估算时长')));if(!project?.compilation_stale)for(const event of (project?.timeline?.events||[]).slice(0,30))body.append(node('div','process-event',timecode((event.time_ms??event.start_ms??0)/1000)+' · '+controlLabel(event)))}
 if(kind==='export'){for(const file of project?.exports||[])body.append(download(file));if(!project?.exports?.length)body.append(node('p','muted',uiText('尚未导出作品。完成生成后，在推理页选择导出格式。')))}
 const destination=['source','parse','voices'].includes(kind)?'text':'inference';body.append(button(destination==='text'?uiText('回到文本工作区'):uiText('前往推理工作区'),()=>{$('processDialog').close();navigateStudio(destination)}));
}
document.querySelectorAll('[data-process]').forEach(b=>b.onclick=()=>{processSummary(b.dataset.process);$('processDialog').showModal()});
$('closeProcess').onclick=()=>$('processDialog').close();$('processDialog').addEventListener('click',e=>{if(e.target!==$('processDialog'))return;const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close()});
const selectScriptSpanBeforeRebuild=selectScriptSpan;selectScriptSpan=function(n){$('processDialog').close();navigateStudio('text');selectScriptSpanBeforeRebuild(n)};
function renderContext(){
 const ss=project?.segments||[],done=ss.filter(s=>s.status==='done').length;
 put('contextTitle',studioPage==='engine'?uiText('本地运行状态'):studioPage==='roles'?uiText('你的配音阵容'):studioPage==='text'?uiText('创作流程'):uiText('生成与交付'));
 put('contextCopy',studioPage==='roles'?uiText('角色保存声音与形象，在文本工作区决定谁来朗读。'):studioPage==='engine'?uiText('连接引擎后，回到文本工作区准备作品。'):uiText('点击任一步骤，查看简洁的处理详情。'));
 const stats=$('contextStats');stats.replaceChildren();for(const [value,label] of studioPage==='roles'?[[presets.length,uiText('已保存角色')]]:[[ss.length,uiText('朗读片段')],[done,uiText('生成完成')]]){const item=node('div','context-stat');item.append(node('strong','',value),node('span','',label));stats.append(item)}
 put('contextAction',studioPage==='roles'||studioPage==='engine'?uiText('去安排文本与角色'):studioPage==='text'?uiText('进入推理工作区'):uiText('回到文本工作区'));
 const draft=scriptDraft(),valid=scriptParsed()&&!scriptHasErrors();const status={source:draft.text?'complete':'waiting',parse:scriptHasErrors()?'error':valid?'complete':'waiting',voices:project?.voice_binding_errors?.length||castingIsDirty()||!defaultVoiceReady()?'unbound':project?'ready':'waiting',synthesis:active===project?.id?'running':done&&done===ss.length?'complete':'waiting',timeline:project?.compilation_stale?'stale':project?.timeline?.timing_status==='exact'?'exact':'estimated',export:project?.compilation_stale?'stale':project?.exports?.length?'exported':'waiting'};
 const statusLabels={complete:uiText('完成'),ready:uiText('已安排'),exact:uiText('实测'),error:uiText('需修正'),unbound:uiText('待安排'),running:uiText('生成中'),waiting:uiText('等待'),estimated:uiText('估算'),exported:uiText('已导出'),stale:uiText('需重新编译')};
 document.querySelectorAll('[data-process]').forEach(b=>{b.dataset.status=status[b.dataset.process];const badge=b.querySelector('.process-status');if(badge)badge.textContent=statusLabels[status[b.dataset.process]]});
}
$('contextAction').onclick=()=>navigateStudio(studioPage==='text'?'inference':'text');
function renderRebuild(){
 if(loading||terminating||exited)return;updateRoleActions();renderRoleCards();renderCasting();renderTextSegments();renderContext();renderLibrary();
 $('inferenceEmpty').classList.toggle('hidden',!!project);$('workspace').classList.toggle('hidden',!project);
 const castBlocked=castingIsDirty()||(project?.voice_binding_errors||[]).length>0||!defaultVoiceReady();
 if(castBlocked)for(const id of ['start','retry','export','exportAll','exportSrt','exportKson','exportKsonPreview'])$(id).disabled=true;
 if(project)put('bookTitle',project.title);put('scriptState',project?.compilation_stale?uiText('需重新编译'):scriptIsDirty()?uiText('文字待应用'):scriptHasErrors()?uiText('文字需要修正'):scriptDraft().parseBusy?uiText('正在整理'):project?uiText('已保存'):uiText('新作品'));
 if(project?.compilation_stale)put('timelineSummary',uiText('需重新编译 · 应用脚本后更新计划和时间轴'));
 if(project?.timeline&&!project.compilation_stale)put('timelineSummary',(scriptIsDirty()?uiText('文字修改待应用 · '):'')+(project.timeline.timing_status==='exact'?uiText('实测总时长 {duration} · 包含段间停顿',{duration:timecode((project.timeline.duration_ms||0)/1000)}):uiText('预计总时长 {duration} · 包含段间停顿，未生成的片段使用估算时长',{duration:timecode((project.timeline.duration_ms||0)/1000)})));
 put('rawMode',uiText('编辑文本'));put('visualMode',uiText('阅读预览'));put('parseScript',uiText('预览分段'));$('previewScript').classList.add('hidden');put('applyScript',project?.compilation_stale?uiText('重新编译脚本'):uiText('应用文本修改'));
 put('groupPanelTitle',project?.source_format==='pcs'?uiText('兼容分组与导出'):uiText('章节分组与导出'));put('groupPanelNotice',project?.source_format==='pcs'?uiText('PCS 的正式章节由 section 控制；下列手动分组与旧建议仅用于兼容导出，不改变 PCS 章节。'):uiText('自动章节作为建议，确认后成为正式分组。可调整名称、边界与颜色；整本导出不受分组影响。'));put('showSuggestions',project?.source_format==='pcs'?uiText('旧分组建议（兼容）'):uiText('智能分段建议'));
 put('sourceNotice',project?.compilation_stale?uiText('编译快照已失效。原有音频保留；请解析并应用脚本，重新建立计划和时间轴。'):scriptIsDirty()?uiText('文字修改尚未应用，原有音频会保留到应用时。'):scriptDraft().format==='pcs'?uiText('控制指令会整理为页码、停顿、语速、章节和声线，不参与朗读。'):uiText('按自然段、标点与每段字数整理文本，全文使用默认角色。'));
}
const renderBeforeRebuild=render;render=function(stopping=false){renderBeforeRebuild(stopping);renderRebuild()};
const renderScriptWorkspaceBeforeRebuild=renderScriptWorkspace;renderScriptWorkspace=function(){renderScriptWorkspaceBeforeRebuild();renderRebuild()};
const loadBeforeRebuild=load;load=async function(id){await loadBeforeRebuild(id);if(loading||id&&project?.id!==id)return;restoreRoleEditor();renderRebuild()};
const showNewBeforeRebuild=showNew;showNew=function(){showNewBeforeRebuild();navigateStudio('text');restoreRoleEditor()};
const openEditorBeforeRebuild=openEditor;openEditor=function(id){openEditorBeforeRebuild(id);const s=project?.segments.find(s=>s.id===id);if(!s)return;const roleId=s.resolved_role_id||project.default_role_id,role=project.role_snapshots?.[roleId];const v={...project.voice,...role?.voice,...s.overrides};if(s.rate!==null&&s.rate!==undefined)v.speed=s.rate;for(const [field,key] of [['editSpeed','speed'],['editSeed','seed'],['editLanguage','text_lang'],['editReference','reference'],['editPrompt','prompt'],['editPromptLang','prompt_lang']])$(field).value=v[key]};
const segmentMetadataBeforeRebuild=segmentMetadata;segmentMetadata=function(s){const row=segmentMetadataBeforeRebuild(s);if(s.resolved_role_name||s.voice_label)row.prepend(node('span','pcs-chip pcs-voice',s.resolved_role_name||s.voice_label));return row};
$('parseScript').onclick=()=>safe(async()=>{const parsed=await parseScript();if(parsed?.valid!==false&&scriptParsed())await previewScript();renderRebuild();if(scriptHasErrors()){processSummary('parse');$('processDialog').showModal()}});
$('previewScript').onclick=$('parseScript').onclick;
for(const id of ['start','retry','export','exportAll','exportSrt','exportKson','exportKsonPreview']){const before=$(id).onclick;$(id).onclick=e=>{if(castingIsDirty()){notify(uiText('请先在文本页保存角色安排'));return}if(project?.voice_binding_errors?.length||!defaultVoiceReady()){notify(uiText('请先在文本页完成默认角色和声线绑定'));return}return before?.(e)}}
function refreshRebuildLanguage(){
 roleCardsKey='';bookCardsKey='';textPreviewKey='';castingKey='';
 const copy=pageCopy[studioPage];if(copy){put('pageTitle',uiText(copy[1]));put('pageSubtitle',uiText(copy[2]))}
 mobileLibraryToggle.textContent=document.body.classList.contains('mobile-library-collapsed')?uiText('展开作品目录 ＋'):uiText('收起作品目录 −');
 bindingSyncButton.textContent=uiText('同步角色库配置');bindingSyncButton.title=uiText('主动更新作品内已保存的声音配置，只同步仍在角色库中的角色');
 renderRolePreview();
 put('presetNotice',roleEditorDirty?uiText('此角色有未保存的修改。'):selectedRoleId?uiText('角色已载入。修改后保存，可在文本页安排配音。'):uiText('选择模型和参考音频，保存你的第一个角色。'));
 renderTrash(trashedBooks);renderRebuild();
 if($('processDialog').open&&currentProcessKind)processSummary(currentProcessKind);
}
let initialPage='text';try{initialPage=sessionStorage.getItem('kudioWorkspace')||'text'}catch{}
restoreRoleEditor();navigateStudio(initialPage);
