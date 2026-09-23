// Regression tests for asynchronous project navigation, without a live engine.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.value=''; this.children=[]; this.textContent=''; this.hidden=false; this.style={};
    this.dataset={};this.classes=new Set(); this.classList={add:c=>this.classes.add(c),remove:c=>this.classes.delete(c),toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c)}; }
  setAttribute(k,v) { this[k]=v; }
  scrollIntoView() {}
  replaceChildren(...children) { this.children=children; }
  append(...children) { this.children.push(...children); }
  querySelectorAll() { return []; }
  pause() { this.pauseCount=(this.pauseCount||0)+1; }
  play() { this.playCount=(this.playCount||0)+1; return Promise.resolve(); }
  load() {}
  close() {}
  after() {}
  removeAttribute() {}
  add(item) { this.children.push(item); }
  click() { this.onclick?.(); }
}
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const storage=new Map();
const context=vm.createContext({
  document:{getElementById:element,createElement:()=>new Element(),querySelector:()=>null,querySelectorAll:()=>[]},
  localStorage:{setItem:(k,v)=>storage.set(k,v),getItem:k=>storage.get(k),removeItem:k=>storage.delete(k)},
  fetch:async()=>({ok:true,blob:async()=>({})}),URL:{createObjectURL:()=>Math.random().toString(),revokeObjectURL:()=>{}},Option: class {constructor(text,value){this.text=text;this.value=value}},setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>{},console,Map,encodeURIComponent,
});
const html=fs.readFileSync(__dirname+'/index.html','utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
// Skip initial page boot; all navigation/render code and handlers remain unchanged.
vm.runInContext(script,context);
vm.runInContext(fs.readFileSync(__dirname+'/studio.js','utf8').split('// Boot')[0].replace('},4000);monitor();','},4000);'),context);
vm.runInContext(fs.readFileSync(__dirname+'/library.js','utf8'),context);
vm.runInContext(fs.readFileSync(__dirname+'/segmentation.js','utf8').split('// Boot')[0],context);
const pending=[];
context.request=path=>new Promise(resolve=>pending.push({path,resolve}));
vm.runInContext('api=request',context);
const run=code=>vm.runInContext(code,context);
const project=id=>({project:{id,title:id,voice:{name:id,speed:1,gap:0.3},segments:[{id:id+'1',chapter:'正文',text:'正文'+id,role:'旁白',status:'pending',duration:0}],exports:[]},active:null});
const resolve=(id)=>pending.shift().resolve(project(id));

(async()=>{
  // Late responses must not replace the user's latest selection.
  const a=run("load('A')"),b=run("load('B')");
  pending[1].resolve(project('B'));await b;
  pending[0].resolve(project('A'));await a;pending.length=0;
  assert.equal(element('bookTitle').textContent,'B');
  assert.equal(element('projects').value,'B');

  // A placeholder selection must open the new-work form, including during loading.
  const c=run("load('C')");await run("load('')");resolve('C');await c;
  assert.equal(run('project'),null);
  assert.equal(element('workspace').classes.has('hidden'),true);
  assert.equal(element('importCard').classes.has('hidden'),false);

  let p=run("load('A')");resolve('A');await p;
  element('name').value='未保存的音色名';element('filter').value='done';
  p=run("load('B')");resolve('B');await p;
  assert.equal(element('filter').value,'all');
  assert.equal(element('segments').children.length,1);
  p=run("load('A')");resolve('A');await p;
  assert.equal(element('name').value,'未保存的音色名');

  // A poll for A cannot overwrite a new visit to A after A -> B -> A.
  const poll=run('refresh()');
  const old=pending.shift();
  p=run("load('B')");resolve('B');await p;
  p=run("load('A')");resolve('A');await p;
  const stale=project('A');stale.project.title='STALE';old.resolve(stale);await poll;
  assert.equal(element('bookTitle').textContent,'A');
  // Simulate a new segment finishing while a different segment is playing.
  run("project.segments[0].status='done';project.segments[0].duration=5;project.segments.push({id:'A2',text:'下一段',chapter:'正文',role:'旁白',status:'pending',duration:0});");
  await run("playSegment('A1')");
  const audio=element('continuousAudio'),src=audio.src,pauses=audio.pauseCount;
  audio.currentTime=2.5;
  const update=run('refresh()');const fresh=project('A');fresh.project.segments[0].status='done';fresh.project.segments[0].duration=5;fresh.project.segments.push({id:'A2',text:'下一段',chapter:'正文',role:'旁白',status:'done',duration:3});pending.shift().resolve(fresh);await update;
  assert.equal(audio.src,src);assert.equal(audio.pauseCount,pauses);assert.equal(audio.currentTime,2.5);
  await run('advance(1)');assert.equal(run('currentPlay.id'),'A2');
  element('segmentSearch').value='不存在的文字';run('render()');assert.equal(element('searchCount').textContent,'匹配 0 / 2 段');
  console.log('PASS: uninterrupted playback during generation, next segment, search');
  run("project.groups=[{id:'g1',name:'我的分段',start:'A1',end:'A2',color:'#547cc2'},{id:'g2',name:'重叠段',start:'A2',end:'A2',color:'#287f78'}];project.suggestions=[{id:'s1',name:'原文建议',start:'A1',end:'A2'}];$('segmentSearch').value='';render()");
  assert.equal(run("segmentMembership(project.segments).get('A2').length"),3);
  assert.equal(run("effectiveGroups(segmentMembership(project.segments).get('A2')).length"),2);
  assert.equal(element('groups').children.length,2);
  assert.equal(element('suggestedGroups').children.length,1);
  assert.equal(element('timelineStrip').children.length,2);
  assert.match(element('segments').children[0].children[0].children[1].textContent,/我的分段/);
  assert.equal(audio.src,run('currentPlay.url'));
  run("selectBoundary('start',1);selectBoundary('end',2)");
  assert.match(element('rangePreview').textContent,/包含 2 个片段/);
  let release;context.delayed=()=>new Promise(resolve=>release=resolve);
  context.testButton=new Element();context.testButton.textContent='保存测试';
  const operation=run('runUI(testButton,delayed)');
  assert.equal(context.testButton.disabled,true);
  assert.equal(context.testButton.dataset.busy,'1');
  await run("runUI(testButton,()=>{throw Error('duplicate')})");
  release();await operation;
  assert.equal(context.testButton.dataset.busy,undefined);
  await run("runUI(testButton,()=>{throw Error('测试失败')})");
  assert.match(element('toast').textContent,/测试失败/);
  console.log('PASS: inclusive membership, overlap colors, suggestion separation, boundary preview, busy and error feedback');
  console.log('PASS: latest selection, blank selection, filter reset, draft retention, stale polling');
})().catch(e=>{console.error(e);process.exitCode=1});
