from decimal import Decimal
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select
from .models import Tank,FuelSample,Transfer
from .grade_models import SampleGrade,TankGradeChange
from .density_model import evaluate,within_spec,accepted_grade,intake_grade,references_for
from .transfer_service import obj,get,audit
from .database import Session

def now():return datetime.now(timezone.utc).isoformat()

def backfill():
    """Snapshot legacy samples under their tank's current grade before any
    re-grade can happen, so their old lab records keep their own-time grade."""
    with Session.begin() as s:
        missing=s.scalars(select(FuelSample).outerjoin(SampleGrade,SampleGrade.sample_id==FuelSample.id).where(SampleGrade.id==None)).all()
        for sample in missing:
            tank=s.get(Tank,sample.tank_id)
            if tank:s.add(SampleGrade(sample_id=sample.id,intake_grade=tank.grade,accepted_grade=None))

def sample_grade_row(s,sample):
    row=s.scalar(select(SampleGrade).where(SampleGrade.sample_id==sample.id).with_for_update())
    if row:return row
    row=SampleGrade(sample_id=sample.id,intake_grade=s.get(Tank,sample.tank_id).grade,accepted_grade=None);s.add(row);s.flush();return row

def change_view(s,x):
    sample=s.get(FuelSample,x.sample_id)
    return {**obj(x),'tank_code':s.get(Tank,x.tank_id).code,'sample_code':sample.code,
            'sample_status':sample.status,'judgment':x.evidence,
            'tank_grade':s.get(Tank,x.tank_id).grade}

def suggestion(s,sample,to_grade,u):
    tank=get(s,Tank,sample.tank_id,True)
    sample=get(s,FuelSample,sample.id,True)
    if sample.reference:raise HTTPException(409,'参考样不参与整罐改牌')
    current=intake_grade(s,sample)
    if current!=tank.grade:raise HTTPException(409,'油样归属牌号与储罐现牌号不一致，不能按现牌号改牌')
    if sample.status not in ['anomaly','rejected']:raise HTTPException(409,'仅按现牌号判异常的库存油样可申请整罐改牌')
    if to_grade==tank.grade:raise HTTPException(409,'目标牌号须与现牌号不同')
    if s.scalar(select(TankGradeChange.id).where(TankGradeChange.tank_id==tank.id,TankGradeChange.state=='suggested')):raise HTTPException(409,'本罐已有待确认改牌建议，不可重复申请')
    density=sample.retest_density if sample.retest_density is not None else sample.density_15
    if not within_spec(to_grade,density,sample.viscosity,sample.sulfur):raise HTTPException(409,'按目标牌号规格复判未通过，不能提出改牌')
    # Reference distribution comes from *other* tanks' target-grade samples only.
    try:score,threshold,passed,evidence=evaluate(s,sample,to_grade,density,exclude_tank_id=tank.id)
    except ValueError as e:raise HTTPException(409,f'目标牌号复判失败：{e}')
    if not passed:raise HTTPException(409,'目标牌号参考分布复判仍为异常，不能提出改牌')
    ref_tanks=sorted({s.get(Tank,x.tank_id).code for x in references_for(s,to_grade,tank.id)})
    prior=sample.status
    row=sample_grade_row(s,sample);row.accepted_grade=to_grade
    sample.status='regraded'
    x=TankGradeChange(tank_id=tank.id,sample_id=sample.id,from_grade=tank.grade,to_grade=to_grade,state='suggested',
                      evidence={**evidence,'sample_status_before':prior,'tank_liters_at_judgment':float(tank.liters),
                                'reference_tanks':ref_tanks,'judged_at':now()},
                      tank_liters_snapshot=tank.liters,requested_by=u['email'])
    s.add(x);s.flush();audit(s,u,'grade_change_suggested',f'{tank.code}:{tank.grade}->{to_grade} / {sample.code}')
    return change_view(s,x)

def revert_sample(s,x):
    row=s.scalar(select(SampleGrade).where(SampleGrade.sample_id==x.sample_id).with_for_update())
    sample=s.get(FuelSample,x.sample_id)
    if row and row.accepted_grade==x.to_grade:row.accepted_grade=None
    if sample.status=='regraded':sample.status=x.evidence.get('sample_status_before','anomaly')
    # Orders requested during the tentative re-grade window lose their basis.
    for tr in s.scalars(select(Transfer).where(Transfer.sample_id==x.sample_id,Transfer.status=='requested').with_for_update().execution_options(populate_existing=True)):
        if tr.grade_snapshot==x.to_grade:tr.status='voided'

def abandon(s,x,reason,u):
    x.state='voided';x.void_reason=reason;revert_sample(s,x)
    audit(s,u,'grade_change_voided',f'{x.from_grade}->{x.to_grade} / {reason}')

def void_pending(s,tank,reason,u):
    x=s.scalar(select(TankGradeChange).where(TankGradeChange.tank_id==tank.id,TankGradeChange.state=='suggested').with_for_update().execution_options(populate_existing=True))
    if x:abandon(s,x,reason,u)

def confirm(s,id,u):
    """Returns (view, error). A stale suggestion is voided and committed before
    the caller raises, so the void survives and a retry cannot void twice."""
    candidate=get(s,TankGradeChange,id)
    tank=get(s,Tank,candidate.tank_id,True)
    x=get(s,TankGradeChange,id,True)
    if x.state=='confirmed':return change_view(s,x),'整罐改牌已确认，不会重复作废调拨或重复切换牌号'
    if x.state=='voided':return change_view(s,x),'改牌建议已作废：'+x.void_reason
    sample=get(s,FuelSample,x.sample_id)
    reasons=[]
    if tank.liters!=x.tank_liters_snapshot:reasons.append('复判后罐量已发生入罐或扣量')
    if tank.grade!=x.from_grade:reasons.append('储罐牌号已变化')
    if sample.status!='regraded' or accepted_grade(s,sample)!=x.to_grade:reasons.append('油样复判结果已变化')
    if reasons:
        reason='；'.join(reasons)+'，改牌建议作废，须重新取样复判'
        abandon(s,x,reason,u);s.flush()
        return change_view(s,x),reason
    voided=[]
    for tr in s.scalars(select(Transfer).join(FuelSample,FuelSample.id==Transfer.sample_id).where(Transfer.status=='requested',FuelSample.tank_id==tank.id).with_for_update().execution_options(populate_existing=True)):
        if tr.grade_snapshot!=x.from_grade:continue
        tr.status='voided';voided.append(tr.code)
    before=tank.grade;tank.grade=x.to_grade
    x.state='confirmed';x.confirmed_by=u['email']
    x.confirmation_snapshot={'tank_code':tank.code,'from_grade':before,'to_grade':x.to_grade,'sample_code':sample.code,
        'tank_liters':float(tank.liters),'voided_transfers':voided,'confirmed_by':u['email'],'at':now(),
        'released_transfers_kept':'原牌号已签调拨保留原化验与牌号快照'}
    audit(s,u,'grade_change_confirmed',f'{tank.code}:{before}->{x.to_grade} / 作废{len(voided)}笔未签调拨')
    return change_view(s,x),None