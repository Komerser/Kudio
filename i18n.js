/* Local UI language only. User text, voice settings and paths stay untouched. */
(function(){
 'use strict';
 const catalog=window.KUDIO_MESSAGES||{};
 const languages=['zh','en','ja'];
 let language='zh';try{const saved=localStorage.getItem('kudioUiLanguage');if(languages.includes(saved))language=saved}catch{}
 const textRecords=new Map(),attributeRecords=[],rendered=new Map();
 const patterns=Object.keys(catalog).filter(key=>/\{\w+\}/.test(key)).map(key=>{
  const names=[],parts=key.split(/(\{\w+\})/);const source=parts.map(part=>{if(/^\{\w+\}$/.test(part)){names.push(part.slice(1,-1));return '([\\s\\S]*?)'}return part.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}).join('');
  return {key,names,regex:new RegExp('^'+source+'$')};
 });
 function format(value,params){return String(value).replace(/\{(\w+)\}/g,(whole,name)=>Object.hasOwn(params,name)?String(params[name]):whole)}
 function remember(value,descriptor){if(rendered.size>=2500&&!rendered.has(value))rendered.delete(rendered.keys().next().value);rendered.set(value,descriptor);return value}
 function describe(message){
  const key=String(message??'');if(Object.hasOwn(catalog,key))return {key,params:{}};
  if(rendered.has(key))return rendered.get(key);
  for(const pattern of patterns){const match=pattern.regex.exec(key);if(match)return {key:pattern.key,params:Object.fromEntries(pattern.names.map((name,i)=>[name,match[i+1]]))}}
  return {key,params:{}};
 }
 function t(message,params){
  const descriptor=params&&Object.keys(params).length?{key:String(message),params}:describe(message);
  const value=language==='zh'?descriptor.key:catalog[descriptor.key]?.[language]??descriptor.key;
  return remember(format(value,descriptor.params),descriptor);
 }
 function excluded(element){return !!element?.closest('script,style,svg,pre,[data-i18n-skip]')}
 function capture(root){
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;
  while(node=walker.nextNode()){if(excluded(node.parentElement))continue;const key=node.nodeValue.trim();if(Object.hasOwn(catalog,key)){const leading=node.nodeValue.match(/^\s*/)[0],trailing=node.nodeValue.match(/\s*$/)[0];textRecords.set(node,{key,leading,trailing})}}
  for(const element of root.querySelectorAll('*')){if(excluded(element))continue;for(const attribute of ['placeholder','title','aria-label','content']){const key=element.getAttribute(attribute);if(key&&Object.hasOwn(catalog,key))attributeRecords.push({element,attribute,key})}}
 }
 function localize(root=document){
  for(const [node,record] of textRecords){if(node.isConnected&&(root===document||root.contains(node)))node.nodeValue=record.leading+t(record.key)+record.trailing}
  for(const record of attributeRecords){if(record.element.isConnected&&(root===document||root.contains(record.element)))record.element.setAttribute(record.attribute,t(record.key))}
 }
 function setLanguage(next){
  if(!languages.includes(next))return;
  language=next;try{localStorage.setItem('kudioUiLanguage',language)}catch{}
  document.documentElement.lang=language==='zh'?'zh-CN':language;
  document.body.dataset.uiLanguage=language;
  const select=document.getElementById('uiLanguage');if(select){select.value=language;select.setAttribute('aria-label',t('界面语言'))}
  localize();
  for(const [id,key] of [['groups','尚无已确认分组。可新建手动分组，或采纳智能分段建议。'],['suggestedGroups','暂无分组建议，已忽略的建议可以恢复显示。']]){const element=document.getElementById(id);if(element)element.dataset.emptyText=t(key)}
  for(const name of ['refreshBasicLanguage','refreshStudioLanguage','refreshRebuildLanguage','refreshAssetLanguage'])if(typeof window[name]==='function')window[name]();
  document.dispatchEvent(new CustomEvent('kudio:language',{detail:{language}}));
 }
 window.KudioI18n={t,describe,localize,setLanguage,get language(){return language}};
 capture(document);setLanguage(language);
 const select=document.getElementById('uiLanguage');if(select)select.addEventListener('change',()=>setLanguage(select.value));
})();
