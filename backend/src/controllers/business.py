from src.services import datasets,plans,traces,renumber
from src.services.audit import log
def save_dataset(data,user,item_id=None):
 row=datasets.save(data,item_id);log(user.email,'dataset.save',row['name']);return row
def save_plan(data,user,item_id=None):
 row=plans.save(data,item_id);log(user.email,'plan.save',row['name']);return row
def start_trace(data,user):
 row=traces.create(data);log(user.email,'trace.queue',row['name']);return row
def preview_renumber(dataset_id,data,user):
 result=renumber.preview(dataset_id,data.mapping,user);log(user.email,'dataset.renumber.preview',f"dataset={dataset_id} request={result['request']['id']}");return result
def confirm_renumber(request_id,user):
 result=renumber.confirm(request_id,user)
 if not result['replayed']:log(user.email,'dataset.renumber.confirm',f"request={request_id} dataset={result['dataset']['id']}")
 return result
