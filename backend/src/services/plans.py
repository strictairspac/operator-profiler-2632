from fastapi import HTTPException
from src.config.database import Session
from src.models.plan import Plan
from src.utils.serialization import record
from src.models.dataset import Dataset

def list_items():
 with Session() as s:return [record(r) for r in s.query(Plan).order_by(Plan.id.desc()).all()]

def save(data,item_id=None):
 with Session() as s:
  current=s.get(Plan,item_id) if item_id else None
  if item_id and not current:raise HTTPException(404,'未找到记录')
  # Lock every touched dataset in id order before reading or writing the plan, so
  # a plan edit serialises with a renumber confirmation instead of racing it.
  ids={data.dataset_id}|({current.dataset_id} if current else set())
  locked={d.id for d in s.query(Dataset).filter(Dataset.id.in_(sorted(ids))).order_by(Dataset.id).with_for_update().all()}
  if data.dataset_id not in locked:raise HTTPException(404,'未找到数据集')
  row=s.get(Plan,item_id,populate_existing=True) if item_id else Plan()
  for k,v in data.model_dump().items():setattr(row,k,v)
  s.add(row);s.commit();s.refresh(row);return record(row)
