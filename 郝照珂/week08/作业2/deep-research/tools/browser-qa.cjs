let pw;try{pw=require('playwright');}catch{pw=require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/haozh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');}
const {chromium}=pw;
const path=require('node:path');const fs=require('node:fs');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');const out=path.join(root,'evidence');
(async()=>{const browser=await chromium.launch({headless:true,channel:'msedge'});const page=await browser.newPage({viewport:{width:1440,height:1050},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:8008/');await page.getByRole('button',{name:'开始研究'}).click();await page.getByText('研究完成 · 离线演示',{exact:true}).waitFor();
await page.screenshot({path:path.join(out,'04-app-report.png'),fullPage:true});
await page.getByRole('tab',{name:/资料来源/}).click();await page.locator('.source').first().waitFor();assert.equal(await page.locator('.source').count(),2);await page.screenshot({path:path.join(out,'05-app-sources.png'),fullPage:true});
await page.getByRole('tab',{name:'研究过程'}).click();assert.equal(await page.locator('.step-head').filter({hasText:'判断补检'}).count(),2);await page.screenshot({path:path.join(out,'06-app-process.png'),fullPage:true});
const download=await page.locator('#download-md').getAttribute('href');const response=await page.request.get('http://127.0.0.1:8008'+download);assert.equal(response.status(),200);const md=await response.text();assert.ok(md.includes('模型推断'));fs.writeFileSync(path.join(out,'sample-report.md'),md);
const jsonURL=await page.locator('#download-json').getAttribute('href');const saved=await (await page.request.get('http://127.0.0.1:8008'+jsonURL)).json();fs.writeFileSync(path.join(out,'sample-research.json'),JSON.stringify(saved,null,2));
await page.reload();await page.locator('.history-item').first().click();await page.getByText('研究完成 · 离线演示',{exact:true}).waitFor();
await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'07-app-mobile.png'),fullPage:true});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
const invalid=await page.request.post('http://127.0.0.1:8008/api/research',{data:{topic:'',mode:'demo',max_rounds:3}});assert.equal(invalid.status(),400);
const cross=await page.request.post('http://127.0.0.1:8008/api/research',{headers:{Origin:'https://example.org'},data:{topic:'RAG 与微调如何选择',mode:'demo',max_rounds:3}});assert.equal(cross.status(),403);
await page.setViewportSize({width:1440,height:1080});for(const name of ['01-skill','02-hook','03-mcp']){await page.goto('file:///'+path.join(out,name+'.html').replaceAll('\\','/'));await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'browser-qa.json'),JSON.stringify({time:new Date().toISOString(),passed:['demo_submit_and_complete','two_sources','two_judgment_rounds','markdown_download','json_download','history_after_reload','mobile_no_horizontal_overflow','invalid_input_400','cross_origin_403','no_javascript_errors'],errors},null,2));console.log('Browser QA passed; 7 screenshots saved.');await browser.close();})().catch(e=>{console.error(e);process.exit(1)});
