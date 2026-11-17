import importlib,sys,json,pytest
import conftest_env
from fastapi.testclient import TestClient
from sqlalchemy import text
from src.config.database import Base,engine,Session
from src.models.dataset import Dataset
from src.models.plan import Plan
from src.models.trace import Trace
from src.models.identity import User
from src.utils.security import hash_password
from src.services import renumber
import src.main as main

@pytest.fixture(scope='session')
def client():
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','operator','viewer'):
   s.add(User(email=role+'@operator.local',role=role,password=hash_password('Trace2026!')))
  s.commit()
 c=TestClient(main.app)
 return c

def token(client,role):
 r=client.post('/auth/login',json={'email':role+'@operator.local','password':'Trace2026!'})
 assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}

import itertools
SEQ=itertools.count()
def uniq(name):return f'{name}-{next(SEQ)}'

def make_dataset(name,sensors,readings):
 name=uniq(name)
 with Session() as s:
  d=Dataset(name=name,sensors=sensors,readings=readings,description='t');s.add(d);s.commit();s.refresh(d);return d.id

def make_plan(name,dataset_id,steps):
 name=uniq(name)
 with Session() as s:
  p=Plan(name=name,dataset_id=dataset_id,steps=steps);s.add(p);s.commit();s.refresh(p);return p.id

SENSORS=[{'sensor_id':1,'zone':'北','device_type':'pump'},{'sensor_id':2,'zone':'南','device_type':'motor'},{'sensor_id':3,'zone':'东','device_type':'valve'}]
READINGS=[{'sensor_id':1,'value':80,'recorded_at':'a'},{'sensor_id':2,'value':40,'recorded_at':'b'},{'sensor_id':3,'value':60,'recorded_at':'c'}]

def get_dataset(i):
 with Session() as s:return Dataset.__table__.select().where(Dataset.id==i).execute if False else None

def dataset_row(i):
 with Session() as s:
  d=s.get(Dataset,i);return {'sensors':d.sensors,'readings':d.readings}
def plan_row(i):
 with Session() as s:return s.get(Plan,i).steps

def test_swap_and_cycle(client):
 h=token(client,'operator')
 ds=make_dataset('swap-ds',SENSORS,READINGS)
 pl=make_plan('swap-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':1},{'op':'filter','field':'value','compare':'gte','value':50}])
 # swap 1<->2 and cycle 1->2->3->1 variant separately
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':2,'2':1}},headers=h)
 assert r.status_code==200,r.text
 req=r.json()['request'];assert req['status']=='pending' and req['stale']==False
 r=client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 assert r.status_code==200,r.text
 body=r.json();assert body['replayed']==False
 row=dataset_row(ds);ids=sorted(x['sensor_id'] for x in row['sensors']);assert ids==[1,2,3]
 by_zone={x['zone']:x['sensor_id'] for x in row['sensors']}
 assert by_zone['北']==2 and by_zone['南']==1
 assert sorted(x['sensor_id'] for x in row['readings'])==[1,2,3]
 assert plan_row(pl)[1]['value']==2
 # idempotent retry
 r=client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 assert r.status_code==200 and r.json()['replayed'] is True
 assert {x['zone']:x['sensor_id'] for x in dataset_row(ds)['sensors']}['北']==2

def test_cycle_three(client):
 h=token(client,'operator')
 ds=make_dataset('cycle-ds',SENSORS,READINGS)
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':2,'2':3,'3':1}},headers=h)
 assert r.status_code==200,r.text
 r=client.post(f"/renumbers/{r.json()['request']['id']}/confirm",json={},headers=h)
 assert r.status_code==200,r.text
 zones={x['zone']:x['sensor_id'] for x in dataset_row(ds)['sensors']}
 assert zones=={'北':2,'南':3,'东':1}

def test_reject_target_collision(client):
 h=token(client,'operator')
 ds=make_dataset('collide-ds',SENSORS,READINGS)
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':2}},headers=h)
 assert r.status_code==409 and '占用' in r.json()['detail']

def test_reject_missing_source(client):
 h=token(client,'operator')
 ds=make_dataset('missing-ds',SENSORS,READINGS)
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'99':7}},headers=h)
 assert r.status_code==404

def test_reject_duplicate_targets(client):
 h=token(client,'operator')
 ds=make_dataset('dup-ds',SENSORS,READINGS)
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':7,'2':7}},headers=h)
 assert r.status_code==409

def test_duplicate_sensor_multiplicity(client):
 h=token(client,'operator')
 ds=make_dataset('mult-ds',[{'sensor_id':1,'zone':'北','device_type':'pump'},{'sensor_id':1,'zone':'复核','device_type':'pump'}],[{'sensor_id':1,'value':80,'recorded_at':'a'}])
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':5}},headers=h)
 assert r.status_code==200,r.text
 prev=r.json()['preview'];assert prev['duplicate_sensors']==[{'sensor_id':1,'rows':2,'mapped_to':5}]
 r=client.post(f"/renumbers/{r.json()['request']['id']}/confirm",json={},headers=h)
 assert r.status_code==200,r.text
 row=dataset_row(ds);assert len(row['sensors'])==2 and all(x['sensor_id']==5 for x in row['sensors'])
 assert row['readings'][0]['sensor_id']==5

def test_stale_on_dataset_edit(client):
 h=token(client,'operator')
 ds=make_dataset('stale-ds',SENSORS,READINGS)
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':9}},headers=h);req=r.json()['request']
 # edit dataset through API
 d=client.get('/datasets',headers=h).json();target=[x for x in d if x['id']==ds][0]
 target['description']='edited after preview'
 rr=client.put(f'/datasets/{ds}',json={'name':target['name'],'description':target['description'],'sensors':target['sensors'],'readings':target['readings']},headers=h)
 assert rr.status_code==200,rr.text
 listing={x['id']:x for x in client.get('/renumbers',headers=h).json()}
 assert listing[req['id']]['stale'] is True
 r=client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 assert r.status_code==409 and '过期' in r.json()['detail']
 assert {x['sensor_id'] for x in dataset_row(ds)['sensors']}=={1,2,3}

def test_stale_on_new_plan(client):
 h=token(client,'operator')
 ds=make_dataset('staleplan-ds',SENSORS,READINGS)
 make_plan('staleplan-a',ds,[{'op':'scan','source':'readings'}])
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':9}},headers=h);req=r.json()['request']
 late=make_plan('staleplan-b',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':1}])
 r=client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 assert r.status_code==409 and '新的关联计划' in r.json()['detail']
 # preview again (now includes new plan) -> confirm
 r2=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':9}},headers=h)
 assert r2.status_code==200,r2.text
 affected=r2.json()['preview']['plans_affected'];assert any(p['plan_id']==late for p in affected),affected
 r2=client.post(f"/renumbers/{r2.json()['request']['id']}/confirm",json={},headers=h)
 assert r2.status_code==200
 with Session() as s:
  ps=s.query(Plan).filter_by(dataset_id=ds).order_by(Plan.id).all()
  assert ps[1].steps[1]['value']==9

def test_range_preserving_and_reject(client):
 h=token(client,'operator')
 ds=make_dataset('range-ds',[{'sensor_id':i,'zone':str(i),'device_type':'pump'} for i in (1,2,3,4)],
   [{'sensor_id':i,'value':50,'recorded_at':'a'} for i in (1,2,3,4)])
 pl=make_plan('range-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'gte','value':3}])
 # order-preserving move: 3->10,4->11 keeps gte semantics
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'3':10,'4':11}},headers=h)
 assert r.status_code==200,r.text
 prev=r.json()['preview'];assert prev['plans_affected'][0]['range_notes'][0]['after']==10
 r=client.post(f"/renumbers/{r.json()['request']['id']}/confirm",json={},headers=h)
 assert r.status_code==200,r.text
 assert plan_row(pl)[1]['value']==10
 assert sorted(x['sensor_id'] for x in dataset_row(ds)['readings'])==[1,2,10,11]
 # non-monotone mapping swaps 3 and 4 -> gte 4 can't keep selection {4}
 ds2=make_dataset('range-ds2',[{'sensor_id':i,'zone':str(i),'device_type':'pump'} for i in (1,2,3,4)],[{'sensor_id':i,'value':50,'recorded_at':'a'} for i in (1,2,3,4)])
 make_plan('range-plan2',ds2,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'gte','value':4}])
 r=client.post(f'/datasets/{ds2}/renumber/preview',json={'mapping':{'3':4,'4':3}},headers=h)
 assert r.status_code==409 and '范围条件' in r.json()['detail']

def test_trace_snapshot_stable(client):
 h=token(client,'operator')
 ds=make_dataset('snap-ds',SENSORS,READINGS)
 pl=make_plan('snap-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':1}])
 # queue a trace, then renumber; snapshot keeps old ids
 with Session() as s:
  from src.models.trace import Trace
  p=s.get(Plan,pl);d=s.get(Dataset,ds)
  s.add(Trace(name='snap-old',plan_id=pl,dataset_id=ds,status='completed',snapshot={'dataset':{'sensors':d.sensors,'readings':d.readings},'plan':{'steps':p.steps}}));s.commit()
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':8}},headers=h)
 assert r.status_code==200
 client.post(f"/renumbers/{r.json()['request']['id']}/confirm",json={},headers=h)
 with Session() as s:
  old=s.query(Trace).filter_by(name='snap-old').one()
  assert old.snapshot['dataset']['sensors'][0]['sensor_id']==1
  assert old.snapshot['plan']['steps'][1]['value']==1
 # new trace snapshot sees new revision
 rt=client.post('/traces',json={'name':'snap-new','plan_id':pl},headers=h)
 # redis unavailable -> 503 is fine; inspect snapshot row if created
 with Session() as s:
  new=s.query(Trace).filter_by(name='snap-new').first()
  if new:
   assert new.snapshot['dataset']['sensors'][0]['sensor_id']==8
   assert new.snapshot['plan']['steps'][1]['value']==8
   assert new.snapshot['dataset']['sensors'][0]['sensor_id']==new.snapshot['plan']['steps'][1]['value']

def test_viewer_forbidden(client):
 h=token(client,'viewer')
 ds=make_dataset('view-ds',SENSORS,READINGS)
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':9}},headers=h)
 assert r.status_code==403
 assert client.get('/renumbers',headers=h).status_code==200

def test_range_and_eq_combination(client):
 h=token(client,'operator')
 sensors=[{'sensor_id':i,'zone':str(i),'device_type':'pump'} for i in (1,2,3,4,5,6)]
 readings=[{'sensor_id':i,'value':50,'recorded_at':'a'} for i in (1,2,3,4,5,6)]
 ds=make_dataset('combo-ds',sensors,readings)
 # eq 2 first narrows reachable domain to {2}; a later gte 2 is then trivially preserved
 pl=make_plan('combo-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':2},{'op':'filter','field':'sensor_id','compare':'gte','value':2}])
 # mapping 2->5 alone collides with existing 5; swap 2 and 5
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'2':5,'5':2}},headers=h)
 assert r.status_code==200,r.text
 prev=r.json()['preview'];changes=prev['plans_affected'][0]['changes']
 assert [c['kind'] for c in changes]==['eq','gte'],changes
 assert changes[0]['after']==5
 client.post(f"/renumbers/{r.json()['request']['id']}/confirm",json={},headers=h)
 steps=plan_row(pl)
 assert steps[1]['value']==5 and steps[2]['value']==5

def test_shadowed_target_rejected(client):
 h=token(client,'operator')
 ds=make_dataset('shadow-ds',SENSORS,READINGS)
 make_plan('shadow-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':50}])
 # 50 is a free number but a plan eq condition references it; moving a sensor onto 50 must be rejected
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':50}},headers=h)
 assert r.status_code==409 and '等值条件' in r.json()['detail']

def test_execution_equivalence_after_migration(client):
 h=token(client,'operator')
 sensors=[{'sensor_id':i,'zone':['北','南'][i%2],'device_type':'pump'} for i in (1,2,3,4,5,6)]
 readings=[{'sensor_id':i,'value':10*i,'recorded_at':'a'} for i in (1,2,3,4,5,6)]
 ds=make_dataset('equiv-ds',sensors,readings)
 steps=[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'gte','value':3},{'op':'hash_join','right':'sensors','left_key':'sensor_id','right_key':'sensor_id'},{'op':'group','keys':['zone'],'measure':'value','aggregate':'sum'}]
 pl=make_plan('equiv-plan',ds,steps)
 from src.services.operators import execute
 with Session() as s:d=s.get(Dataset,ds);before=execute({'sensors':d.sensors,'readings':d.readings},steps)
 # order-preserving map 1..6 -> 10..15 keeps range semantics
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{str(i):i+9 for i in range(1,7)}},headers=h)
 assert r.status_code==200,r.text
 client.post(f"/renumbers/{r.json()['request']['id']}/confirm",json={},headers=h)
 newsteps=plan_row(pl);assert newsteps[1]['value']==12
 with Session() as s:d=s.get(Dataset,ds);after=execute({'sensors':d.sensors,'readings':d.readings},newsteps)
 assert before['output']==after['output'],(before['output'],after['output'])
 assert before['checksum']==after['checksum']
 assert sorted(x['sensor_id'] for x in d.sensors)==list(range(10,16))

def test_audit_and_openapi(client):
 h=token(client,'admin')
 paths=client.get('/openapi.json').json()['paths']
 assert '/datasets/{dataset_id}/renumber/preview' in paths
 assert '/renumbers/{request_id}/confirm' in paths
 audit=client.get('/audit',headers=h).json()
 actions={a['action'] for a in audit}
 assert 'dataset.renumber.preview' in actions and 'dataset.renumber.confirm' in actions

def test_bad_mapping_inputs(client):
 h=token(client,'operator')
 ds=make_dataset('badinput-ds',SENSORS,READINGS)
 assert client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{}},headers=h).status_code==422
 assert client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'a':2}},headers=h).status_code==422
 assert client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':1}},headers=h).status_code==422
 ok=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':9,'2':2}},headers=h)
 assert ok.status_code==200,ok.text
 assert ok.json()['preview']['mapping']=={'1':9}
 assert client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':0}},headers=h).status_code==422
 assert client.post('/datasets/999999/renumber/preview',json={'mapping':{'1':2}},headers=h).status_code==404
 assert client.post('/renumbers/999999/confirm',json={},headers=h).status_code==404

def test_new_plan_no_edit_race_stale_detected(client):
 # editing a plan (not adding) also invalidates a preview
 h=token(client,'operator')
 ds=make_dataset('planedit-ds',SENSORS,READINGS)
 pl=make_plan('planedit-plan',ds,[{'op':'scan','source':'readings'}])
 req=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':9}},headers=h).json()['request']
 r=client.put(f'/plans/{pl}',json={'name':'planedit-renamed','dataset_id':ds,'steps':[{'op':'scan','source':'readings'}],'note':'x'},headers=h)
 assert r.status_code==200,r.text
 c=client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 assert c.status_code==409
 assert client.get(f"/renumbers/{req['id']}",headers=h).json()['request']['status']=='conflicted'
 # a conflicted request can never be re-confirmed
 assert client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h).status_code==409

def test_group_then_filter_on_aggregate_does_not_break_migration(client):
 h=token(client,'operator')
 sensors=[{'sensor_id':i,'zone':'北','device_type':'pump'} for i in (1,2,3)]
 readings=[{'sensor_id':i,'value':60,'recorded_at':'t'} for i in (1,2,3)]
 ds=make_dataset('agg-ds',sensors,readings)
 make_plan('agg-plan',ds,[{'op':'scan','source':'readings'},{'op':'group','keys':['sensor_id'],'measure':'value','aggregate':'sum'},{'op':'filter','field':'sum','compare':'gte','value':50}])
 r=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':7}},headers=h)
 assert r.status_code==200,r.text
