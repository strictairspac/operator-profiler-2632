from fastapi import APIRouter,Depends
from src.middlewares.auth import current,writer
from src.schemas.business import RenumberInput
from src.controllers.business import preview_renumber,confirm_renumber
from src.services import renumber
router=APIRouter()

@router.get('/renumbers')
def listing(dataset_id:int|None=None,u=Depends(current)):return renumber.list_items(dataset_id)

@router.get('/renumbers/{request_id}')
def detail(request_id:int,u=Depends(current)):return renumber.detail(request_id)

@router.post('/datasets/{dataset_id}/renumber/preview')
def preview(dataset_id:int,data:RenumberInput,u=Depends(writer)):return preview_renumber(dataset_id,data,u)

@router.post('/renumbers/{request_id}/confirm')
def confirm(request_id:int,u=Depends(writer)):return confirm_renumber(request_id,u)
