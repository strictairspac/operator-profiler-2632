"""Bulk sensor renumbering: preview, staleness detection and atomic confirmation.

Rules implemented here:
* one mapping is applied simultaneously by ORIGINAL number, so swaps and cycles work;
* every sensor/reading row is rewritten, preserving duplicate-key multiplicity;
* a target that is still in use (sensor, reading, or a plan eq condition) is a collision;
* a source that does not exist in the dataset sensor table is rejected;
* plan sensor_id eq conditions migrate with the mapping; range conditions either migrate
  to a threshold that selects the same physical sensors or reject the whole batch.
"""
import logging
from fastapi import HTTPException
from sqlalchemy import func
from src.config.database import Session
from src.models.dataset import Dataset
from src.models.plan import Plan
from src.models.renumber import RenumberRequest
from src.utils.serialization import record, fingerprint
from src.constants.enums import RENUMBER_STATUSES

MAX_SENSOR_ID=100000
PENDING,CONFLICTED,COMPLETED=RENUMBER_STATUSES

def _normalize(mapping):
 if not isinstance(mapping,dict) or not mapping:raise HTTPException(422,'映射不能为空，格式为 {"原编号": 新编号}')
 if len(mapping)>100:raise HTTPException(422,'一次最多变更100个传感器编号')
 out={}
 for key,value in mapping.items():
  try:a=int(key);b=int(value)
  except (TypeError,ValueError):raise HTTPException(422,'原编号和新编号都必须是整数')
  if not 1<=a<=MAX_SENSOR_ID or not 1<=b<=MAX_SENSOR_ID:raise HTTPException(422,'传感器编号必须在1至100000之间')
  if a!=b:out[a]=b
 if not out:raise HTTPException(422,'映射里没有任何编号发生变化')
 if len(set(out.values()))!=len(out):raise HTTPException(409,'多个原编号指向同一个新编号，会造成撞号')
 return out

def _field(dataset,key):
 value=dataset[key] if isinstance(dataset,dict) else getattr(dataset,key)
 return value or []
def _sensor_ids(dataset):return [int(s['sensor_id']) for s in _field(dataset,'sensors')]
def _reading_ids(dataset):return [int(r['sensor_id']) for r in _field(dataset,'readings')]

def _plan_refs(plans):
 refs=set()
 for plan in plans:
  for step in plan.steps:
   if step.get('op')=='filter' and step.get('field')=='sensor_id' and step.get('compare')=='eq' and isinstance(step.get('value'),(int,float)) and not isinstance(step.get('value'),bool):
    refs.add(int(step['value']))
 return refs

def _validate(dataset,mapping,plans=()):
 used=set(_sensor_ids(dataset))|set(_reading_ids(dataset));sources=set(mapping)
 missing=sorted(sources-used)
 if missing:raise HTTPException(404,f'来源编号在数据集中不存在：{missing}；请刷新后重新预演')
 targets=set(mapping.values())
 collisions=sorted(t for t in targets if t in used and t not in sources)
 if collisions:raise HTTPException(409,f'目标编号仍被占用：{collisions}；互换/循环必须把所有占用编号写进映射，其余目标必须为空号')
 shadowed=sorted(t for t in targets if t not in used and t in _plan_refs(plans))
 if shadowed:raise HTTPException(409,f'目标编号被计划中未迁移的等值条件引用：{shadowed}；换号会改变该条件的含义，请改用其他编号')

def _translate_range(domain,compare,value,mapping,step_index):
 """Return a threshold whose new selection is exactly the image of the old one.

 `domain` holds the sensor ids that can reach this step under the old plan.
 When the old selection and its complement cannot be separated by any threshold
 of the same direction after mapping, the whole batch is rejected.
 """
 if not domain:return value
 ext={s:mapping.get(s,s) for s in domain}
 selected={s for s in domain if (s>=value if compare=='gte' else s<=value)}
 rest=domain-selected
 image={ext[s] for s in selected};other={ext[s] for s in rest}
 if not image:return max(other)+1 if compare=='gte' else min(other)-1
 bound=min(image) if compare=='gte' else max(image)
 if other and not (max(other)<bound if compare=='gte' else min(other)>bound):
  raise HTTPException(409,f'第{step_index+1}步 sensor_id 的 {compare} {value} 范围条件在本次映射下无法保持含义；整批拒绝，请改用等值条件或调整映射')
 return bound

def _matches(row,step):
 field,compare,value=step['field'],step['compare'],step['value']
 if field not in row:return True  # e.g. a filter on an aggregate after group: keep reachability conservative
 actual=row.get(field)
 if compare=='eq':return actual==value
 if not isinstance(actual,(int,float)) or isinstance(actual,bool):raise ValueError('范围比较遇到非数值')
 return actual>=value if compare=='gte' else actual<=value

def _migrate_steps(steps,mapping,readings):
 """Migrate sensor_id conditions, tracking the reachable sensor ids per step.

 Reachability is always computed with the ORIGINAL predicate (same physical
 sensors), so later conditions are translated against the old-plan domain.
 """
 new_steps=[];changes=[];range_notes=[];targets=set(mapping.values())
 reachable={}
 for row in readings:reachable.setdefault(int(row['sensor_id']),[]).append(row)
 for index,step in enumerate(steps):
  original=step;step=dict(step);op=step.get('op')
  if op=='group':
   reachable=reachable if 'sensor_id' in step.get('keys',[]) else {}
  elif op=='filter':
   field=step.get('field');compare=step.get('compare');value=step.get('value')
   if field=='sensor_id' and isinstance(value,(int,float)) and not isinstance(value,bool):
    numeric=int(value) if float(value).is_integer() else None
    if compare=='eq':
     if numeric in mapping:
      step['value']=mapping[numeric];changes.append({'step':index,'kind':'eq','before':value,'after':step['value']})
     elif numeric in targets:
      raise HTTPException(409,f'第{index+1}步 sensor_id 等值条件引用了本次目标编号 {numeric}，会撞号；整批拒绝')
    elif compare in ('gte','lte'):
     translated=_translate_range(set(reachable),compare,value,mapping,index)
     if translated!=value:
      step['value']=translated;changes.append({'step':index,'kind':compare,'before':value,'after':translated})
      range_notes.append({'step':index,'compare':compare,'before':value,'after':translated})
   reachable={sid:[row for row in rows if _matches(row,original)] for sid,rows in reachable.items()}
   reachable={sid:rows for sid,rows in reachable.items() if rows}
  new_steps.append(step)
 return new_steps,changes,range_notes

def _plan_changes(plans,mapping,readings):
 out=[]
 for plan in plans:
  steps,changes,notes=_migrate_steps(plan.steps,mapping,readings)
  out.append((plan,steps,changes,notes))
 return out

def baseline_of(dataset,plans):
 return {'dataset':fingerprint(record(dataset)),'plans':fingerprint([record(p) for p in sorted(plans,key=lambda p:p.id)]),'plan_ids':sorted(p.id for p in plans)}

def _payload(row,stale=None,with_preview=True):
 data=record(row)
 data.pop('baseline',None)
 if not with_preview:data.pop('preview',None)
 if stale is not None:data['stale']=stale
 return data

def _hits(mapping,bucket):
 counts={k:0 for k in mapping}
 for item in bucket:
  old=int(item['sensor_id'])
  if old in counts:counts[old]+=1
 return counts

def preview(dataset_id,mapping,user):
 mapping=_normalize(mapping)
 with Session() as s:
  dataset=s.get(Dataset,dataset_id)
  if not dataset:raise HTTPException(404,'未找到数据集')
  plans=s.query(Plan).filter_by(dataset_id=dataset.id).order_by(Plan.id).all()
  _validate(dataset,mapping,plans)
  sensor_hits=_hits(mapping,dataset.sensors)
  reading_hits=_hits(mapping,dataset.readings)
  migrated=_plan_changes(plans,mapping,dataset.readings)
  affected=[]
  for plan,steps,changes,notes in migrated:
   if changes:affected.append({'plan_id':plan.id,'plan_name':plan.name,'changes':changes,'range_notes':notes})
  sensor_counts={}
  for sid in _sensor_ids(dataset):sensor_counts[sid]=sensor_counts.get(sid,0)+1
  payload={
   'mapping':{str(k):v for k,v in sorted(mapping.items())},
   'changes':[{'from':k,'to':mapping[k],'sensors':sensor_hits[k],'readings':reading_hits[k]} for k in sorted(mapping)],
   'sensors_before':len(dataset.sensors),'sensors_after':len(dataset.sensors),
   'readings_before':len(dataset.readings),'readings_after':len(dataset.readings),
   'duplicate_sensors':[{'sensor_id':sid,'rows':n,'mapped_to':mapping[sid]} for sid,n in sorted(sensor_counts.items()) if n>1 and sid in mapping],
   'plans_affected':affected,'plans_checked':len(plans),
   'note':'映射按原编号同时生效；采样与计划等值条件同步迁移；连接倍数按行保留；范围条件仅在能精确保持含义时迁移。'
  }
  row=RenumberRequest(dataset_id=dataset.id,status=PENDING,mapping={str(k):v for k,v in mapping.items()},preview=payload,baseline=baseline_of(dataset,plans),created_by=user.email)
  s.add(row);s.commit();s.refresh(row)
  return {'request':_payload(row,False),'preview':payload}

def list_items(dataset_id=None):
 with Session() as s:
  q=s.query(RenumberRequest)
  if dataset_id:q=q.filter_by(dataset_id=dataset_id)
  rows=q.order_by(RenumberRequest.id.desc()).limit(50).all()
  out=[]
  for row in rows:
   stale=False
   if row.status==PENDING:
    dataset=s.get(Dataset,row.dataset_id)
    plans=s.query(Plan).filter_by(dataset_id=row.dataset_id).all() if dataset else []
    stale=not dataset or baseline_of(dataset,plans)!=row.baseline
   out.append(_payload(row,stale,with_preview=False))
  return out

def detail(request_id):
 with Session() as s:
  row=s.get(RenumberRequest,request_id)
  if not row:raise HTTPException(404,'未找到换号预演')
  stale=False
  if row.status==PENDING:
   dataset=s.get(Dataset,row.dataset_id)
   plans=s.query(Plan).filter_by(dataset_id=row.dataset_id).all() if dataset else []
   stale=not dataset or baseline_of(dataset,plans)!=row.baseline
  return {'request':_payload(row,stale),'preview':row.preview}

def confirm(request_id,user):
 with Session() as s:
  row=s.query(RenumberRequest).filter_by(id=request_id).with_for_update().first()
  if not row:raise HTTPException(404,'未找到换号预演')
  if row.status==COMPLETED:return {'request':_payload(row,False),'replayed':True}
  if row.status==CONFLICTED:raise HTTPException(409,'该预演已过期并标记为冲突，请重新预演后再确认')
  dataset=s.query(Dataset).filter_by(id=row.dataset_id).with_for_update().first()
  if not dataset:
   row.status=CONFLICTED;row.error='数据集已不存在';s.commit();raise HTTPException(409,'数据集已不存在，请重新预演')
  plans=s.query(Plan).filter_by(dataset_id=dataset.id).order_by(Plan.id).with_for_update().all()
  if baseline_of(dataset,plans)!=row.baseline:
   row.status=CONFLICTED;row.error='预演期间数据集或关联计划发生变化'
   s.commit();raise HTTPException(409,'预演已过期：预演期间数据集或关联计划被编辑，或有新的关联计划加入。请重新预演后再确认')
  mapping={int(k):int(v) for k,v in row.mapping.items()}
  _validate(dataset,mapping,plans)
  migrated=_plan_changes(plans,mapping,dataset.readings)
  try:
   dataset.sensors=[{**sensor,'sensor_id':mapping.get(int(sensor['sensor_id']),int(sensor['sensor_id']))} for sensor in dataset.sensors]
   dataset.readings=[{**reading,'sensor_id':mapping.get(int(reading['sensor_id']),int(reading['sensor_id']))} for reading in dataset.readings]
   updated=0
   for plan,steps,changes,notes in migrated:
    if changes:plan.steps=steps;updated+=1
   result={'mapping':{str(k):v for k,v in sorted(mapping.items())},'plan_ids':sorted(p.id for p in plans),'plans_updated':updated,'sensors':len(dataset.sensors),'readings':len(dataset.readings)}
   row.status=COMPLETED;row.result=result;row.error=None;row.confirmed_at=func.now()
   s.add(row);s.commit();s.refresh(row)
  except HTTPException:
   s.rollback();raise
  except Exception:
   logging.exception('renumber confirm failed')
   s.rollback();raise HTTPException(500,'换号未提交，数据集与计划保持原状；可直接重试确认')
  return {'request':_payload(row,False),'dataset':record(dataset),'replayed':False}
