import numpy as np
from sqlalchemy import select,func
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import KernelDensity
from .models import FuelSample,Tank
from .grade_models import SampleGrade
from .constants import GRADES

def within_spec(grade,density,viscosity,sulfur):
    g=GRADES[grade];return g['density_min']<=float(density)<=g['density_max'] and g['viscosity_min']<=float(viscosity)<=g['viscosity_max'] and float(sulfur)<=g['sulfur_max']

def judged_density(sample):
    return sample.retest_density if sample.retest_density is not None else sample.density_15

def grade_row(s,sample):
    return s.scalar(select(SampleGrade).where(SampleGrade.sample_id==sample.id))

def intake_grade(s,sample):
    row=grade_row(s,sample)
    return row.intake_grade if row else s.get(Tank,sample.tank_id).grade

def accepted_grade(s,sample):
    row=grade_row(s,sample)
    if not row:return s.get(Tank,sample.tank_id).grade
    return row.accepted_grade or row.intake_grade

def references_for(s,grade,exclude_tank_id=None):
    # Reference samples belong to the grade they were taken under, never to the
    # tank's current grade.  Re-grading a tank therefore leaves its old
    # reference samples in the original grade's reference distribution.
    q=select(FuelSample).join(Tank,FuelSample.tank_id==Tank.id).outerjoin(SampleGrade,SampleGrade.sample_id==FuelSample.id).where(FuelSample.reference==True,func.coalesce(SampleGrade.intake_grade,Tank.grade)==grade)
    if exclude_tank_id is not None:q=q.where(FuelSample.tank_id!=exclude_tank_id)
    return s.scalars(q.order_by(FuelSample.id)).all()

def evaluate(s,sample,grade,density=None,exclude_tank_id=None):
    refs=references_for(s,grade,exclude_tank_id)
    values=np.array([[float(x.density_15)]for x in refs])
    if len(values)<20:raise ValueError(f'{grade}等级合格参考样不足20条')
    scaler=StandardScaler();z=scaler.fit_transform(values)
    if float(scaler.var_[0])<1e-8:raise ValueError('参考密度没有足够变化，不能拟合核密度')
    model=KernelDensity(kernel='gaussian',bandwidth=.3).fit(z)
    scores=model.score_samples(z);threshold=float(np.quantile(scores,.05))
    point=float(density if density is not None else sample.density_15)
    score=float(model.score_samples(scaler.transform([[point]]))[0])
    passed=score>=threshold and within_spec(grade,point,sample.viscosity,sample.sulfur)
    grid=np.linspace(float(values.min()-8),float(values.max()+8),70)
    curve=np.exp(model.score_samples(scaler.transform(grid.reshape(-1,1))))/float(scaler.scale_[0])
    return score,threshold,passed,{'grade':grade,'density':point,'reference_count':len(refs),'reference_codes':[x.code for x in refs],'reference_values':values.ravel().tolist(),'threshold_quantile':.05,'threshold':threshold,'score':score,'bandwidth':.3,'kernel':'gaussian','standardized':True,'seed':42,'grid':grid.tolist(),'pdf':curve.tolist(),'spec':GRADES[grade],'fit_scope':'reference=True且参考样归属等级相同；待测样不参与拟合'}

def compare(s,sample):
    return evaluate(s,sample,intake_grade(s,sample),sample.density_15)
