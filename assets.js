/* Local file selection and catalog for voice settings. Paths remain on this PC. */
let assetCatalog={gpt:[],sovits:[],reference:[]};
let assetScanToken=0;
const assetFields={gpt:'gptChoices',sovits:'sovitsChoices',reference:'referenceChoices'};
const assetRoots=['engineRoot','modelRoot','referenceRoot'];

function assetStatus(message){$('assetStatus').textContent=message}
function assetRootValues(){return {engine_root:$('engineRoot').value.trim(),model_root:$('modelRoot').value.trim(),reference_root:$('referenceRoot').value.trim()}}
function setAssetRoots(roots,previous){
 for(const [key,id] of [['engine_root','engineRoot'],['model_root','modelRoot'],['reference_root','referenceRoot']]){
  if(!previous||$(id).value===previous[id])$(id).value=roots?.[key]||'';
 }
}
function syncAssetChoices(){
 for(const [field,selectId] of Object.entries(assetFields)){
  const selected=$(field).value;
  $(selectId).value=assetCatalog[field].some(item=>item.path===selected)?selected:'';
 }
}
window.syncAssetChoices=syncAssetChoices;

function showAssetCatalog(data){
 assetCatalog=data.candidates||{gpt:[],sovits:[],reference:[]};
 const prompts={gpt:'从已扫描文件中选择 GPT 模型',sovits:'从已扫描文件中选择 SoVITS 模型',reference:'从已扫描文件中选择参考音频'};
 for(const [field,selectId] of Object.entries(assetFields)){
  const select=$(selectId);
  select.replaceChildren(new Option(prompts[field],''));
  for(const item of assetCatalog[field]||[]){
   const label=item.label||item.path;
   const prefix=item.group&&!label.startsWith(item.group+'\\')&&!label.startsWith(item.group+'/')?item.group+' · ':'';
   const option=new Option(prefix+label,item.path);
   option.title=item.path;
   select.add(option);
  }
 }
 syncAssetChoices();
 const counts=`找到 ${assetCatalog.gpt.length} 个 GPT 模型、${assetCatalog.sovits.length} 个 SoVITS 模型、${assetCatalog.reference.length} 个参考音频`;
 const restart=data.restart_required?'；GPT-SoVITS 目录已更改，请重启 Kudio 后再生成':'';
 assetStatus(counts+(data.truncated?'；结果较多，仅显示前一部分':'')+restart+'。');
}

async function refreshAssets(saveRoots=false){
 const token=++assetScanToken;
 const before=Object.fromEntries(assetRoots.map(id=>[id,$(id).value]));
 assetStatus('正在扫描本机素材…');
 try{
  if(saveRoots){
   const saved=await api('asset-roots',assetRootValues());
   if(token!==assetScanToken)return;
   setAssetRoots(saved.roots,before);
  }
  const data=await api('assets');
  if(token!==assetScanToken)return;
  setAssetRoots(data.roots,before);
  showAssetCatalog(data);
 }catch(error){if(token!==assetScanToken)return;assetStatus('素材扫描未完成：'+error.message+'。请检查目录后重试。');throw error}
}

function matchingRole(key,group){
 const role=(group||'').toLocaleLowerCase().trim(),model=(key||'').toLocaleLowerCase();
 if(!role||/^(model|models|audio|sounds|reference|reference_audios|参考音频|模型|素材)$/i.test(role))return false;
 return model===role||model.startsWith(role+'-')||model.startsWith(role+'_')||model.startsWith(role+' ');
}
function relatedAssets(kind,item,sourceKind){
 const candidates=assetCatalog[kind]||[];
 const key=(item.key||'').toLocaleLowerCase(),group=(item.group||'').toLocaleLowerCase();
 let matches=[];
 if(sourceKind!=='reference'&&kind!=='reference'){
  matches=key?candidates.filter(candidate=>(candidate.key||'').toLocaleLowerCase()===key):[];
 }else if(sourceKind==='reference'){
  matches=candidates.filter(candidate=>(candidate.group||'').toLocaleLowerCase()===group&&matchingRole(candidate.key,group));
 }else if(matchingRole(item.key,group)){
  matches=candidates.filter(candidate=>(candidate.group||'').toLocaleLowerCase()===group);
 }
 return matches.length===1?matches[0]:null;
}
function fillRelatedAsset(kind,item){
 const filled=[];
 if(kind==='reference'){
  if($('gpt').value.trim()||$('sovits').value.trim())return filled;
  const gpt=relatedAssets('gpt',item,kind),sovits=relatedAssets('sovits',item,kind);
  if(!gpt||!sovits||gpt.key!==sovits.key)return filled;
  $('gpt').value=gpt.path;$('sovits').value=sovits.path;
  return ['GPT','SoVITS'];
 }
 for(const target of [kind==='gpt'?'sovits':'gpt','reference']){
  if($(target).value.trim())continue;
  const match=relatedAssets(target,item,kind);
  if(match){$(target).value=match.path;filled.push(target==='gpt'?'GPT':target==='sovits'?'SoVITS':'参考音频')}
 }
 return filled;
}
function chooseCatalogAsset(kind){
 const path=$(assetFields[kind]).value;
 if(!path||!project)return;
 const item=(assetCatalog[kind]||[]).find(candidate=>candidate.path===path);
 if(!item)return;
 $(kind).value=path;
 const paired=fillRelatedAsset(kind,item);
 rememberDrafts();
 syncAssetChoices();
 assetStatus((kind==='gpt'?'GPT 模型':kind==='sovits'?'SoVITS 模型':'参考音频')+'已填入'+(paired.length?'，同时找到 '+paired.join('、'):'')+'。核对后点击“应用配置到当前作品”。');
}

async function chooseLocalFile(kind,field){
 const owner=project?.id,turn=navigation,editor=editing;
 const result=await api('pick-path',{kind,initial:$(field).value});
 if(result.cancelled||!result.path)return;
 if(field==='editReference'){
  if(!editor||editing!==editor||navigation!==turn||!$('segmentEditor').open)return;
  $(field).value=result.path;
  return;
 }
 if(!owner||project?.id!==owner||navigation!==turn)return;
 $(field).value=result.path;
 rememberDrafts();
 syncAssetChoices();
 assetStatus('已选择文件。核对后点击“应用配置到当前作品”。');
}
async function chooseLocalRoot(kind,field){
 const result=await api('pick-path',{kind,initial:$(field).value});
 if(result.cancelled||!result.path)return;
 $(field).value=result.path;
 assetScanToken++;
 assetStatus('目录已选择，点击“保存目录并扫描”查看候选文件。');
}

for(const [buttonId,kind,field] of [
 ['pickGpt','gpt','gpt'],['pickSovits','sovits','sovits'],
 ['pickReference','reference','reference'],['pickEditReference','reference','editReference']
])$(buttonId).onclick=()=>safe(()=>chooseLocalFile(kind,field));
for(const [buttonId,kind,field] of [
 ['pickEngineRoot','engine_root','engineRoot'],['pickModelRoot','model_root','modelRoot'],
 ['pickReferenceRoot','reference_root','referenceRoot']
])$(buttonId).onclick=()=>safe(()=>chooseLocalRoot(kind,field));
$('scanAssets').onclick=()=>safe(()=>refreshAssets(true));
for(const id of assetRoots)$(id).addEventListener('input',()=>{assetScanToken++;assetStatus('目录已更改，点击“保存目录并扫描”查看候选文件。')});
for(const kind of Object.keys(assetFields)){
 $(assetFields[kind]).onchange=()=>chooseCatalogAsset(kind);
 $(kind).addEventListener('input',()=>{rememberDrafts();syncAssetChoices()});
}
refreshAssets().catch(error=>assetStatus('素材扫描未完成：'+error.message+'。可指定目录后重试。'));
