import time,redis
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from src.config.database import Session,engine
from src.config.settings import REDIS_URL
from src.models.dataset import Dataset
from src.models.plan import Plan
from src.models.trace import Trace
from src.utils.serialization import record
queue=redis.Redis.from_url(REDIS_URL)
SNAPSHOT_ATTEMPTS=4

def list_items():
 with Session() as s:return [record(r) for r in s.query(Trace).order_by(Trace.id.desc()).limit(100).all()]
def detail(item_id):
 with Session() as s:
  row=s.get(Trace,item_id)
  if not row:raise HTTPException(404,'未找到执行轨迹')
  return record(row)

def _snapshot(plan_id,name):
 # One repeatable-read snapshot: a concurrently confirmed renumber cannot yield a
 # plan revision paired with a different dataset revision in the same trace.
 with Session(bind=engine.execution_options(isolation_level='REPEATABLE READ')) as s:
  p=s.get(Plan,plan_id)
  if not p:raise HTTPException(404,'未找到算子计划')
  d=s.get(Dataset,p.dataset_id)
  if not d:raise HTTPException(404,'未找到数据集')
  row=Trace(name=name,plan_id=p.id,dataset_id=d.id,status='queued',snapshot={'dataset':record(d),'plan':record(p)})
  s.add(row);s.commit();s.refresh(row)
  return record(row)

def create(data):
 result=None
 for attempt in range(SNAPSHOT_ATTEMPTS):
  try:result=_snapshot(data.plan_id,data.name);break
  except DBAPIError as exc:
   # A renumber committed while this snapshot was in flight: retry on a fresh snapshot.
   if not getattr(exc.orig,'sqlstate',None) in ('40001','40P01') or attempt==SNAPSHOT_ATTEMPTS-1:
    raise
   time.sleep(0.05*(attempt+1))
 try:queue.rpush('operator-traces',result['id'])
 except redis.RedisError:
  with Session() as s:r=s.get(Trace,result['id']);r.status='failed';r.error='队列不可用，请创建新任务';s.commit()
  raise HTTPException(503,'队列不可用，请稍后重试')
 return result
