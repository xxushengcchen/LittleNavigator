/* Real-browser UI regression. API writes are mocked; no microphone, AI service or game actions. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const baseUrl=process.env.LN_UI_URL || 'http://127.0.0.1:8789';
const out=path.resolve(process.env.LN_UI_OUT || 'tmp/ui-qa-v1.4.5');
fs.mkdirSync(out,{recursive:true});
const cases=[],errors=[],writes=[];
async function check(name,fn){await fn();cases.push({name,passed:true});console.log('PASS',name);}
function merge(a,b){for(const[k,v]of Object.entries(b)){if(v && typeof v==='object' && !Array.isArray(v)){a[k]??={};merge(a[k],v);}else a[k]=v;}}
(async()=>{
 const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':undefined),headless:true});
 const context=await browser.newContext({viewport:{width:1180,height:820},reducedMotion:'reduce'});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 const request=context.request;
 const html=await(await request.get(baseUrl)).text();
 const token=html.match(/name="assistant-token" content="([^"]+)"/)[1];
 const original=await(await request.get(baseUrl+'/api/state')).json();
 const fixture=structuredClone(original);fixture.sim=true;fixture.desktop=false;
 Object.assign(fixture.telemetry,{connected:true,source:'sim',fuel_level:45.4,lap:2,position:10,field_size:20,gap_behind_s:.6,lap_last:147.98,time_remain_s:742,pit_sv_flags:117,pit_sv_fuel:10,fast_repairs_available:2});
 fixture.ptt.running=false;fixture.ptt.error=null;fixture.ptt.binding_label='右 Ctrl';fixture.ptt.state='idle';
 let state=fixture,failConfig=false,failState=false,delayPreview=false;
 const previews={};for(const text of ['油还够吗','帮我取消换胎','我是否需要快修']){
  const r=await request.post(baseUrl+'/api/ask',{headers:{'X-Assistant-Token':token},data:{text,play:false}});assert.equal(r.status(),200);previews[text]=await r.json();
 }
 await page.route('**/api/**',async route=>{
  const r=route.request(),u=new URL(r.url());let data;
  const reply=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(u.pathname==='/api/state'){if(failState)return reply({error:'测试服务不可达'},503);return reply(state);}
  if(u.pathname==='/api/devices')return reply({inputs:[{index:0,name:'测试麦克风'}],outputs:[{index:1,name:'测试耳机'}],wheels:[]});
  if(u.pathname==='/api/wheels')return reply({wheels:[]});
  if(u.pathname==='/api/log')return reply({qa:[{q:'分析我的节奏',ai_text:'最近圈数较少，暂不判断趋势。',ai_source:'model',ok:true,layer:'ai',ts:'2026-10-19T20:00:00'}],miss:[],console:[]});
  if(u.pathname==='/api/stats')return reply({total:5,miss:1,avg_first_audio_ms:0,buckets:[3,1,1,0],layers:{phrase:3,l1_keyword:1,refuse:1},top_miss:[]});
  if(u.pathname==='/api/knowledge')return reply(await(await request.get(baseUrl+'/api/knowledge')).json());
  if(u.pathname==='/api/ai/review')return reply({rows:[]});
  if(u.pathname==='/api/config'){
   data=r.postDataJSON();writes.push({path:u.pathname,keys:Object.keys(data.patch)});
   if(failConfig)return reply({error:'测试：配置保存失败'},400);
   merge(state.config,data.patch);if(data.patch.playback)merge(state.config.playback,data.patch.playback);
   return reply({ok:true,restart_hint:[]});
  }
  if(u.pathname.startsWith('/api/ptt/')){writes.push({path:u.pathname});state.ptt.running=u.pathname.endsWith('/start');return reply({running:state.ptt.running});}
  if(u.pathname==='/api/ask'){
   data=r.postDataJSON();writes.push({path:u.pathname,play:data.play,text:data.text});
   if(delayPreview)await new Promise(r=>setTimeout(r,300));
   const tr=structuredClone(previews[data.text] || previews['油还够吗']);tr.text=data.text;
   if(data.play)tr.playback={ok:true,text:'测试播报已完成',kind:tr.kind || 'known'};
   return reply(tr);
  }
  if(u.pathname==='/api/export-logs')return reply({ok:true,path:'测试日志.zip',files:2});
  return reply({ok:true});
 });
 async function nav(id){if(await page.locator('#nav-toggle').isVisible() && await page.locator('#nav-toggle').getAttribute('aria-expanded')!=='true')await page.locator('#nav-toggle').click();await page.locator('#ira-tab-'+id).click();await page.locator('#ira-panel-'+id).waitFor({state:'visible'});}
 await page.goto(baseUrl);await page.waitForFunction(v=>window.__state && window.__state.app.version===v,original.app.version);await page.evaluate(()=>document.fonts.ready);
 await check('Initial state and honest simulation label',async()=>{assert.match(await page.locator('#connection-notice').innerText(),/模拟模式/);assert.equal(await page.locator('[data-binding]').innerText(),'右 Ctrl');});
 await check('Pit service reflects flags exactly',async()=>{const w=page.locator('#ira-panel-dash .pit-wheel');assert.equal(await w.count(),4);assert.deepEqual(await w.evaluateAll(es=>es.map(e=>e.dataset.on)),['true','false','true','false']);assert.match(await page.locator('#ira-panel-dash [data-pit-service]').innerText(),/10.0 L/);});
 await check('All eleven destinations render and every field has a label',async()=>{for(const id of ['asr','pack','route','ai','pit','model','log','stats','debug','knowledge','dash']){await nav(id);if(['asr','pack','route','ai','pit','model'].includes(id))await page.locator('#ira-panel-'+id+' input, #ira-panel-'+id+' select').first().waitFor();assert.equal(await page.locator('h1:visible').count(),1);const unlabeled=await page.locator('#ira-panel-'+id+' input:not([type=hidden]),#ira-panel-'+id+' select').evaluateAll(es=>es.filter(e=>!e.labels?.length && !e.getAttribute('aria-label')).map(e=>e.id));assert.deepEqual(unlabeled,[]);}});
 await check('AI text is visible in history',async()=>{await nav('log');await page.getByText('最近圈数较少，暂不判断趋势。',{exact:true}).waitFor();});
 await check('Zero latency remains zero and histogram has integer ticks',async()=>{await nav('stats');await page.locator('.ira-statgrid .ira-num').first().waitFor();assert.equal((await page.locator('.ira-statgrid .ira-num').allTextContents())[1],'0');await page.locator('.ira-bars svg').waitFor();assert.equal(await page.locator('.ira-bars').getAttribute('role'),'img');});
 await check('Preview never executes and editing invalidates old action',async()=>{await nav('debug');await page.locator('#ira-q').fill('帮我取消换胎');await page.locator('#ira-send').click();await page.locator('[data-audition]:not([disabled])').waitFor();assert.match(await page.locator('[data-audition]').innerText(),/更改进站设置/);assert.equal(writes.filter(w=>w.path==='/api/ask' && w.play).length,0);await page.locator('#ira-q').fill('我是否需要快修');assert.equal(await page.locator('[data-audition]').isDisabled(),true);await page.locator('#ira-send').click();await page.locator('[data-audition]:not([disabled])').waitFor();assert.doesNotMatch(await page.locator('[data-audition]').innerText(),/更改进站/);});
 await check('Slow preview cannot overwrite newly typed text',async()=>{delayPreview=true;await page.locator('#ira-q').fill('帮我取消换胎');await page.locator('#ira-send').click();await page.locator('#ira-q').fill('还剩多少时间');await page.waitForTimeout(450);assert.equal(await page.locator('#ira-q').inputValue(),'还剩多少时间');assert.equal(await page.locator('[data-audition]').isDisabled(),true);delayPreview=false;});
 await check('AI drafts survive navigation without persistent browser storage',async()=>{await nav('ai');await page.locator('#ira-ai-model').fill('draft-model');await page.locator('#ira-ai-key').fill('qa-draft-key');await nav('pack');await nav('ai');await page.waitForFunction(()=>document.querySelector('#ira-ai-model')?.value==='draft-model');assert.equal(await page.locator('#ira-ai-key').inputValue(),'qa-draft-key');assert.equal(await page.evaluate(()=>Object.keys(localStorage).length),0);});
 await check('Saving one AI section keeps other section draft',async()=>{await page.locator('summary').filter({hasText:'语音合成'}).click();await page.locator('#ira-tts-speaker').fill('draft-voice');await page.locator('#ira-ai-save').click();await page.waitForFunction(()=>document.querySelector('#ira-ai-key')?.value==='');assert.equal(await page.locator('#ira-tts-speaker').inputValue(),'draft-voice');assert.equal(await page.locator('#ira-tts-speaker').isVisible(),true);assert.ok(writes.some(w=>w.keys?.includes('ai')));});
 await check('Autosave errors are visible and retain edited value',async()=>{await nav('asr');failConfig=true;await page.locator('#ira-asrthreads2').fill('3');await page.locator('#ira-asrthreads2').blur();await page.locator('#ira-error').waitFor({state:'visible'});assert.match(await page.locator('#ira-error').innerText(),/配置保存失败/);assert.equal(await page.locator('#ira-asrthreads2').inputValue(),'3');await page.locator('#dismiss-error').click();failConfig=false;});
 await check('Microphone control sends only an explicit start/stop',async()=>{await nav('dash');await page.locator('#dashboard-listen').click();await page.waitForFunction(()=>window.__state.ptt.running);assert.equal(await page.locator('#dashboard-listen').innerText(),'暂停监听');await page.locator('#dashboard-listen').click();await page.waitForFunction(()=>!window.__state.ptt.running);});
 await check('Missing data clears old telemetry and zero is not missing',async()=>{state=structuredClone(fixture);state.telemetry.fuel_level=0;await page.waitForFunction(()=>document.querySelector('.ira-tile-num').textContent==='0.0');state.telemetry.connected=false;await page.waitForFunction(()=>document.querySelector('.ira-tile-num').textContent==='—');assert.match(await page.locator('#ira-panel-dash [data-pit-service]').innerText(),/未读到/);state=structuredClone(fixture);});
 await check('Connection error surfaces and retry recovers',async()=>{failState=true;await page.locator('#connection-lost').waitFor({state:'visible'});assert.equal(await page.locator('.ira-tile-num').first().innerText(),'—');failState=false;await page.locator('#retry-connection').click();await page.locator('#connection-lost').waitFor({state:'hidden'});});
 await check('No horizontal overflow at desktop, tablet, mobile and zoom',async()=>{for(const width of [1440,1180,900,768,390,375]){await page.setViewportSize({width,height:width<600?844:900});await nav('dash');await page.waitForTimeout(40);const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);assert.equal(overflow,false,'width '+width);await page.screenshot({path:path.join(out,'dashboard-'+width+'.png')});}await page.setViewportSize({width:590,height:410});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);});
 await check('Mobile drawer closes with Escape and restores focus',async()=>{await page.setViewportSize({width:390,height:844});await page.locator('#nav-toggle').click();assert.equal(await page.locator('#nav-toggle').getAttribute('aria-expanded'),'true');await page.keyboard.press('Escape');assert.equal(await page.locator('#nav-toggle').getAttribute('aria-expanded'),'false');assert.equal(await page.locator('#nav-toggle').evaluate(e=>e===document.activeElement),true);});
 await check('All pages fit the small viewport and screenshot desktop settings',async()=>{for(const id of ['asr','pack','route','ai','pit','model','log','stats','debug','knowledge']){await nav(id);await page.waitForTimeout(50);if(id==='knowledge')await page.locator('[data-kb-stats] .ira-num').first().waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,id);}await page.setViewportSize({width:1440,height:900});for(const id of ['ai','pit','asr','debug','stats','log','knowledge']){await nav(id);await page.waitForTimeout(100);await page.screenshot({path:path.join(out,id+'-1440.png')});}});
 assert.deepEqual(errors,[]);
 const result={passed:true,tests:cases.length,cases,consoleErrors:errors,writeCalls:writes.map(({text,...r})=>r),scope:'Real browser; configuration, microphone and execution writes mocked. Actual backend used for preview only.'};
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(result,null,2));await browser.close();console.log(JSON.stringify({passed:true,tests:cases.length}));
})().catch(e=>{fs.writeFileSync(path.join(out,'failure.txt'),e.stack);console.error(e.stack);process.exit(1);});
