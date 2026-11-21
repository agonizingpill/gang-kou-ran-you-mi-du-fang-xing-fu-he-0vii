from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from .database import Session
from .security import current,allow
from .grade_models import TankGradeChange
from .grade_schemas import GradeSuggestion
from .models import FuelSample
from .transfer_service import get
from . import grade_service as svc
router=APIRouter()
@router.get('/grade-changes')
def changes(u=Depends(current)):
    with Session()as s:return [svc.change_view(s,x)for x in s.scalars(select(TankGradeChange).order_by(TankGradeChange.id.desc()))]
@router.post('/grade-changes')
def suggest(p:GradeSuggestion,u=Depends(current)):
    allow(u,'chemist')
    with Session.begin()as s:return svc.suggestion(s,get(s,FuelSample,p.sample_id,True),p.to_grade,u)
@router.post('/grade-changes/{id}/confirm')
def confirm(id:int,u=Depends(current)):
    allow(u,'admin')
    # A stale suggestion is voided and committed, then reported as a 409 so the
    # void survives the failed confirmation.  A retry cannot void twice.
    with Session.begin()as s:
        view,error=svc.confirm(s,id,u)
    if error:raise HTTPException(409,error)
    return view
