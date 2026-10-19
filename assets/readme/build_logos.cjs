/* Render the editable SVG layouts with the existing development browser tooling. */
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {chromium}=require('playwright');
const out=__dirname;
(async()=>{
 const channel=process.env.PLAYWRIGHT_BROWSER_CHANNEL||(process.platform==='win32'?'msedge':undefined);
 const browser=await chromium.launch({channel,headless:true});
 try{
  const page=await browser.newPage();
  const names=fs.readdirSync(path.join(out,'source')).filter(name=>name.endsWith('.svg')).map(name=>path.basename(name,'.svg')).sort();
  for(const name of names){
   const source=path.join(out,'source',name+'.svg');
   const svg=fs.readFileSync(source,'utf8');
   const size=svg.match(/width="(\d+)" height="(\d+)"/);
   if(!size)throw new Error('Missing SVG canvas dimensions: '+name);
   await page.setViewportSize({width:Number(size[1]),height:Number(size[2])});
   await page.goto(pathToFileURL(source).href);
   await page.screenshot({path:path.join(out,name+'.png'),omitBackground:true});
   console.log(name+'.png',fs.statSync(path.join(out,name+'.png')).size,'bytes');
  }
 }finally{
  await browser.close();
 }
})().catch(e=>{console.error(e);process.exit(1)});
