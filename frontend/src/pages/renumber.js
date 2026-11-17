import {store} from '../stores/business.js';import {esc} from '../utils/html.js';import {renumbers} from '../api/business.js';import {RENUMBER_STATUSES} from '../constants/enums.js';
export const renumberStatusLabel=r=>r.status==='pending'&&r.stale?'<span class="status failed">预演已过期</span>':`<span class="status ${r.status}">${RENUMBER_STATUSES[r.status]||r.status}</span>`;
const mapText=m=>Object.entries(m||{}).map(([a,b])=>a+'→'+b).join(', ');
function planLines(p){return p.changes.map(c=>`<li>第 ${c.step+1} 步 ${esc(c.kind)} ${esc(c.before)} → <b>${esc(c.after)}</b></li>`).join('');}
function previewBody(result){
 const {request,preview}=result;if(!preview)return '';
 const editable=store.user?.role!=='viewer'&&request.status==='pending'&&!request.stale;
 const changes=preview.changes.map(c=>`<li>${esc(c.from)} → <b>${esc(c.to)}</b><small>传感器 ${c.sensors} 行 / 采样 ${c.readings} 行</small></li>`).join('');
 const dup=preview.duplicate_sensors.length?`<p class="muted">重复传感器按行保留倍数：${preview.duplicate_sensors.map(d=>`编号 ${d.sensor_id} 的 ${d.rows} 行 → ${d.mapped_to}`).join('；')}</p>`:'';
 const plans=preview.plans_affected.length?preview.plans_affected.map(p=>`<details open><summary>计划 ${esc(p.plan_name)}</summary><ul>${planLines(p)}</ul>${p.range_notes.length?`<p class="muted">范围条件：${p.range_notes.map(n=>`${esc(n.compare)} ${esc(n.before)} → ${esc(n.after)}`).join('；')}</p>`:''}</details>`).join(''):'<p class="muted">没有计划的编号条件需要迁移。</p>';
 const banner=request.status==='completed'?'<p class="feedback">换号已完成：数据集与关联计划已原子切换；旧执行快照保持不变，新受理执行读取同一版数据与计划。</p>':request.status==='conflicted'?`<p class="feedback error">预演冲突：${esc(request.error||'预演已过期')}。请重新预演后再确认。</p>`:request.stale?'<p class="feedback error">预演已过期：数据集或关联计划在预演后被编辑，或有新的关联计划加入。请重新预演。</p>':'';
 return `<div class="renumber-preview"><div class="preview-head"><span class="eyebrow">PREVIEW #${request.id}</span>${renumberStatusLabel(request)}</div><p>按映射重写 ${preview.sensors_before} 行传感器、${preview.readings_before} 行采样（行数与重复键倍数不变）；检查 ${preview.plans_checked} 个关联计划。</p>${banner}<ul>${changes}</ul>${dup}<h4>计划编号条件迁移</h4>${plans}<p class="muted">${esc(preview.note)}</p><div class="actions">${editable?`<button data-confirm-renumber="${request.id}">确认换号</button>`:''}<button class="quiet" data-discard-renumber>收起预演</button></div></div>`;
}
export function renumberPanel(){
 const open=store.renumberOpen;if(!open)return '';
 const dataset=store.datasets.find(d=>d.id===open.datasetId);
 const history=store.renumbers.filter(r=>!open.datasetId||r.dataset_id===open.datasetId).slice(0,8);
 const list=history.length?`<table><thead><tr><th>预演</th><th>状态</th><th>映射</th><th></th></tr></thead><tbody>${history.map(r=>`<tr><td>#${r.id}<small>${esc(r.created_by)}</small></td><td>${renumberStatusLabel(r)}</td><td>${esc(mapText(r.mapping))}</td><td><button class="quiet" data-show-renumber="${r.id}">查看</button></td></tr>`).join('')}</tbody></table>`:'<p class="muted">还没有换号预演。</p>';
 return `<div class="renumber-block"><h3>批量换号${dataset?' · '+esc(dataset.name):''}</h3><form id="renumber-form"><input type="hidden" name="dataset_id" value="${open.datasetId||''}"><label>数据集<select name="dataset_pick">${store.datasets.map(d=>`<option value="${d.id}" ${d.id===open.datasetId?'selected':''}>${esc(d.name)}</option>`).join('')}</select></label><label>映射 JSON<textarea name="mapping" class="code-input" rows="4" placeholder='{"1": 12, "12": 1}'>${esc(open.mapping??'')}</textarea></label><small>{"原编号": "新编号"}；同一提交按原编号同时生效，可互换或循环。目标号不得被未换走的编号占用，来源必须存在。预先检查与确认之间可继续编辑数据集和计划，确认时会检测过期。</small><button type="submit">预演换号</button></form>${store.renumberPreview?previewBody(store.renumberPreview):''}<h4>最近预演</h4>${list}</div>`;
}
