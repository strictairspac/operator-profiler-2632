import {chromium} from 'playwright';
const base='http://127.0.0.1:18244';
const browser=await chromium.launch();
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
async function login(role){
 await page.goto(base);
 await page.fill('input[name=email]',role+'@operator.local');
 await page.fill('input[name=password]','Trace2026!');
 await page.click('button[type=submit]');
 await page.waitForSelector('.sidebar');
}
await login('operator');
// create dataset distinct for UI run via API
const tok=await page.evaluate(()=>sessionStorage.getItem('token'));
const sensors=[1,2,3].map(i=>({sensor_id:i,zone:['北','南','试验间'][i-1],device_type:'pump'}));
const readings=[1,2,3].map(i=>({sensor_id:i,value:50+i,recorded_at:'2026-11-17T09:0'+i+':00'}));
const name='ui-renumber-'+Date.now();
let r=await page.evaluate(async ({tok,name,sensors,readings})=>{const res=await fetch('/api/datasets',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+tok},body:JSON.stringify({name,description:'ui',sensors,readings})});return res.json();},{tok,name,sensors,readings});
console.log('dataset',r.id);
const dsid=r.id;
r=await page.evaluate(async ({tok,id})=>{const res=await fetch('/api/plans',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+tok},body:JSON.stringify({name:'ui-plan-'+Date.now(),dataset_id:id,steps:[{op:'scan',source:'readings'},{op:'filter',field:'sensor_id',compare:'eq',value:1},{op:'group',keys:['sensor_id'],measure:'value',aggregate:'count'}],note:'ui'})});return {status:res.status,body:await res.json()};},{tok,id:dsid});
if(!r.body.id){console.log('PLAN FAIL',JSON.stringify(r));process.exit(1);}
console.log('plan',r.body.id);const planId=r.body.id;r={id:planId,dataset_id:dsid};
await page.reload();await page.waitForSelector('.sidebar');
// open renumber for that dataset
await page.locator(`[data-renumber="${r.dataset_id}"]`).click();
await page.waitForSelector('#renumber-form');
await page.fill('#renumber-form textarea[name=mapping]','{"1": 2, "2": 1}');
await page.click('#renumber-form button[type=submit]');
await page.waitForSelector('.renumber-preview');
const previewText=await page.textContent('.renumber-preview');
console.log('PREVIEW:',previewText.replace(/\s+/g,' ').slice(0,400));
// simulate concurrent plan insert while preview pending
r=await page.evaluate(async ({tok,id})=>{const res=await fetch('/api/plans',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+tok},body:JSON.stringify({name:'ui-plan-late-'+Date.now(),dataset_id:id,steps:[{op:'scan',source:'readings'},{op:'filter',field:'sensor_id',compare:'eq',value:1}],note:'late'})});return res.json();},{tok,id:r.dataset_id});
await page.waitForTimeout(3000);
const staleBanner=await page.locator('.renumber-preview .feedback.error').first().textContent().catch(()=>null);
console.log('STALE BANNER:',staleBanner);
const confirmDisabled=await page.locator('[data-confirm-renumber]').count();
console.log('CONFIRM BUTTONS:',confirmDisabled);
// force confirm attempt through API to check 409
const conflict=await page.evaluate(async ({tok})=>{const list=await (await fetch('/api/renumbers',{headers:{Authorization:'Bearer '+tok}})).json();const pending=list.find(x=>x.status==='pending');const res=await fetch('/api/renumbers/'+pending.id+'/confirm',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+tok},body:'{}'});return {status:res.status,body:await res.json()};},{tok});
console.log('CONFLICT:',JSON.stringify(conflict));
// re-preview and confirm
await page.click('[data-discard-renumber]');
await page.reload();await page.waitForSelector('.sidebar');
await page.locator(`[data-renumber="${r.dataset_id || 0}"]`).click().catch(async()=>{});
await page.waitForSelector('#renumber-form');
await page.fill('#renumber-form textarea[name=mapping]','{"1": 2, "2": 1}');
await page.click('#renumber-form button[type=submit]');
await page.waitForSelector('[data-confirm-renumber]');
await page.screenshot({path:'/tmp/uitest/preview.png',fullPage:true});
await page.click('[data-confirm-renumber]');
await page.waitForTimeout(1200);
const after=await page.textContent('.renumber-preview');
console.log('AFTER CONFIRM:',after.replace(/\s+/g,' ').slice(0,260));
const ds=await page.evaluate(async ({tok})=>{const list=await (await fetch('/api/datasets',{headers:{Authorization:'Bearer '+tok}})).json();return list[0];},{tok});
console.log('DATASET IDS:',JSON.stringify(ds.sensors.map(s=>s.sensor_id)));
await page.screenshot({path:'/tmp/uitest/confirmed.png',fullPage:true});
console.log('ERRORS:',errors);
await browser.close();
