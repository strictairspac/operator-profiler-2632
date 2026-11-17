from sqlalchemy import Column,Integer,String,JSON,DateTime,func,ForeignKey
from src.config.database import Base
class RenumberRequest(Base):
 """A previewed bulk sensor renumber; confirmation is idempotent and atomic."""
 __tablename__='renumber_requests'
 id=Column(Integer,primary_key=True)
 dataset_id=Column(Integer,ForeignKey('datasets.id'),nullable=False,index=True)
 status=Column(String(20),nullable=False,default='pending',index=True)
 mapping=Column(JSON,nullable=False)
 preview=Column(JSON,nullable=False)
 baseline=Column(JSON,nullable=False)
 result=Column(JSON,nullable=True)
 error=Column(String(1000),nullable=True)
 created_by=Column(String(120),nullable=False,default='')
 created_at=Column(DateTime,server_default=func.now())
 confirmed_at=Column(DateTime,nullable=True)
