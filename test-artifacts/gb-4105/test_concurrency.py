import threading,time,pytest
import conftest_env
from fastapi.testclient import TestClient
from src.config.database import Base,engine,Session
from src.models.dataset import Dataset
from src.models.plan import Plan
from src.models.identity import User
from src.models.renumber import RenumberRequest
from src.utils.security import hash_password
import src.main as main
from test_renumber import token,make_dataset,make_plan,SENSORS,READINGS,dataset_row,plan_row

@pytest.fixture(scope='module')
def client():
 c=TestClient(main.app);return c

def test_concurrent_confirm_only_once(client):
 h=token(client,'operator')
 ds=make_dataset('conc-ds',SENSORS,READINGS)
 pl=make_plan('conc-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':1}])
 req=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':9}},headers=h).json()['request']
 results=[]
 def work():
  c=TestClient(main.app)
  r=c.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
  results.append((r.status_code,r.json().get('replayed')))
 ts=[threading.Thread(target=work) for _ in range(4)]
 [t.start() for t in ts];[t.join() for t in ts]
 assert all(code==200 for code,_ in results),results
 assert sum(1 for _,replayed in results if replayed)>=3
 # idempotent: exactly one application, no double mapping of 9
 ids={x['sensor_id'] for x in dataset_row(ds)['sensors']}
 assert ids=={9,2,3}
 assert plan_row(pl)[1]['value']==9

def test_failed_confirm_preserves_state_and_can_retry(client,monkeypatch):
 h=token(client,'operator')
 ds=make_dataset('fail-ds',SENSORS,READINGS)
 pl=make_plan('fail-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':1}])
 req=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':7}},headers=h).json()['request']
 import src.services.renumber as svc
 calls={'n':0}
 real_commit=svc.Session
 # make the first confirm transaction fail after mutation, before commit
 from sqlalchemy.orm import Session as SASession
 orig_commit=SASession.commit
 class Boom(Exception):pass
 def flaky(self):
  calls['n']+=1
  if calls['n']==1:raise Boom('disk full')
  return orig_commit(self)
 monkeypatch.setattr(SASession,'commit',flaky)
 r=client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 assert r.status_code==500 and '未提交' in r.json()['detail']
 assert {x['sensor_id'] for x in dataset_row(ds)['sensors']}=={1,2,3}
 assert plan_row(pl)[1]['value']==1
 assert client.get(f"/renumbers/{req['id']}",headers=h).json()['request']['status']=='pending'
 monkeypatch.setattr(SASession,'commit',orig_commit)
 r=client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 assert r.status_code==200,r.text
 assert {x['sensor_id'] for x in dataset_row(ds)['sensors']}=={7,2,3}
 assert plan_row(pl)[1]['value']==7

def test_new_trace_snapshot_consistent_after_confirm(client):
 h=token(client,'operator')
 ds=make_dataset('snap2-ds',SENSORS,READINGS)
 pl=make_plan('snap2-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':1}])
 req=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':6}},headers=h).json()['request']
 client.post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 client.post('/traces',json={'name':'snap2-trace','plan_id':pl},headers=h)
 with Session() as s:
  from src.models.trace import Trace
  t=s.query(Trace).filter_by(name='snap2-trace').one()
  assert t.snapshot['dataset']['sensors'][0]['sensor_id']==6
  assert t.snapshot['plan']['steps'][1]['value']==6

def test_trace_created_during_confirm_sees_consistent_pair(client,monkeypatch):
 """A trace accepted while a renumber is in flight must not pair new data with old plan."""
 h=token(client,'operator')
 ds=make_dataset('torn-ds',SENSORS,READINGS)
 pl=make_plan('torn-plan',ds,[{'op':'scan','source':'readings'},{'op':'filter','field':'sensor_id','compare':'eq','value':1}])
 req=client.post(f'/datasets/{ds}/renumber/preview',json={'mapping':{'1':4}},headers=h).json()['request']
 from sqlalchemy.orm import Session as SASession
 orig_commit=SASession.commit
 entered=threading.Event();release=threading.Event()
 def paused(self):
  # pause only the confirmation transaction, after its writes, before commit
  if getattr(self,'info',{}).get('renumber_pause') and not entered.is_set():
   entered.set();release.wait(10)
  return orig_commit(self)
 monkeypatch.setattr(SASession,'commit',paused)
 import src.services.renumber as svc
 orig_session=svc.Session
 def tagged_session(*a,**k):
  s=orig_session(*a,**k);s.info['renumber_pause']=True;return s
 monkeypatch.setattr(svc,'Session',tagged_session)
 out={}
 def confirm():out['r']=TestClient(main.app).post(f"/renumbers/{req['id']}/confirm",json={},headers=h)
 t=threading.Thread(target=confirm);t.start()
 assert entered.wait(10)
 # confirm is mid-transaction; a new trace must now wait for the commit and retry
 midout={}
 def midtrace():midout['r']=TestClient(main.app).post('/traces',json={'name':'torn-mid','plan_id':pl},headers=h)
 mt=threading.Thread(target=midtrace);mt.start();time.sleep(0.5)
 release.set();t.join();mt.join()
 assert midout['r'].status_code in (200,503),midout['r'].text
 with Session() as s:
  from src.models.trace import Trace
  tm=s.query(Trace).filter_by(name='torn-mid').one()
  # either the pre-commit revision (blocked/waiting) or, after retry, the committed one;
  # what must never happen is data=new paired with plan=old.
  data_id=tm.snapshot['dataset']['sensors'][0]['sensor_id']
  plan_id=tm.snapshot['plan']['steps'][1]['value']
  assert data_id==plan_id,(data_id,plan_id)
  assert data_id==4,(data_id,'trace must see the committed revision after retry')
 assert out['r'].status_code==200,out['r'].text
 monkeypatch.setattr(SASession,'commit',orig_commit);monkeypatch.setattr(svc,'Session',orig_session)
 post=TestClient(main.app).post('/traces',json={'name':'torn-post','plan_id':pl},headers=h)
 assert post.status_code in (200,503),post.text
 with Session() as s:
  from src.models.trace import Trace
  tp=s.query(Trace).filter_by(name='torn-post').one()
  assert tp.snapshot['dataset']['sensors'][0]['sensor_id']==4
  assert tp.snapshot['plan']['steps'][1]['value']==4
