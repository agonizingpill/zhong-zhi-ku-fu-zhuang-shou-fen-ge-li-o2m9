from fastapi import HTTPException
from sqlalchemy import select
from .models import Accession,Lot,Regeneration,Pollination,Harvest,Flowering,HarvestExclusion
from .common import lock,pack,add,audit
from . import yield_check as yc

def _crop_of(s,regen_id):
    r=s.get(Regeneration,regen_id);lot=s.get(Lot,r.lot_id)
    return s.get(Accession,lot.accession_id).crop

def preview(s,id,u):
    h=s.get(Harvest,id)
    if not h:raise HTTPException(404,'收获单不存在')
    pollen=s.scalar(select(Pollination).where(Pollination.regeneration_id==h.regeneration_id))
    if not pollen:raise HTTPException(409,'该复壮单尚无套袋授粉记录')
    return yc.evaluate(s,_crop_of(s,h.regeneration_id),pollen.flowers,h.seeds)

def save(s,d,u,id=None):
    r=lock(s,Regeneration,d.regeneration_id)
    pollen=s.scalar(select(Pollination).where(Pollination.regeneration_id==r.id))
    if not pollen or pollen.state!='pollinated' or r.state!='issued':raise HTTPException(409,'必须完成套袋授粉后才能登记收获')
    x=lock(s,Harvest,id) if id else None
    if x and (x.state!='drying' or x.regeneration_id!=r.id or x.code!=d.code):raise HTTPException(409,'已回库或跨单收获不可修改')
    if x:
        for k,v in d.model_dump().items():setattr(x,k,v)
    else:x=Harvest(**d.model_dump());s.add(x)
    s.flush();audit(s,u,'记录收获干燥 '+d.code);return pack(x)

def store(s,id,u,d=None):
    reason=(d.reason if d else '').strip()
    pre=s.get(Harvest,id)
    if not pre:raise HTTPException(404,'收获单不存在')
    regen=s.get(Regeneration,pre.regeneration_id);parent=s.get(Lot,regen.lot_id);lock(s,Accession,parent.accession_id)
    # 与save保持相同顺序；种质锁只用于新增种批的保底聚合。
    r=lock(s,Regeneration,pre.regeneration_id);h=lock(s,Harvest,id)
    if h.state!='drying' or r.state!='issued':raise HTTPException(409,'回库不可重复')
    if h.moisture>12:raise HTTPException(409,'种子含水率须≤12%，请干燥复测后回库')
    pollen=s.scalar(select(Pollination).where(Pollination.regeneration_id==r.id));f=s.scalar(select(Flowering).where(Flowering.regeneration_id==r.id))
    crop=s.get(Accession,parent.accession_id).crop
    check=yc.evaluate(s,crop,pollen.flowers,h.seeds)
    needs_reason=check['status']=='insufficient_samples' or check['within'] is False
    released=False
    if needs_reason:
        if not reason:
            if check['status']=='insufficient_samples':raise HTTPException(409,'同作物参考收获不足%d笔，无法形成预测区间；主管写明理由才可放行'%yc.MIN_SAMPLES)
            raise HTTPException(409,'实收%d粒超出95%%预测区间(%d–%d粒)；主管写明理由才可放行'%(h.seeds,check['lower95_int'],check['upper95_int']))
        if len(reason)<4:raise HTTPException(409,'放行理由不少于4个字')
        released=True
    check['supervisor_release']={'released':released,'reason':reason,'actor':u.email}
    child=Lot(accession_id=parent.accession_id,code=h.code+'-G'+str(parent.generation+1),generation=parent.generation+1,available=h.seeds,stored_months=0,temperature=-18,initial_rate=parent.initial_rate,parent_lot_id=parent.id);s.add(child);s.flush()
    h.lineage={'parent_lot':pack(parent),'father_lot':pack(s.get(Lot,pollen.father_lot_id)),'pollination':pack(pollen),'flowering':pack(f),'vitality':r.vitality_snapshot,'initial_rate_note':'Inherited planning prior; offspring requires independent germination test before sharing'}
    h.yield_check=check;h.state='stored';h.offspring_lot_id=child.id;r.state='harvested';f.state='released'
    audit(s,u,('主管区间外/缺样本放行回库 ' if released else '主管回库 ')+h.code)
    return pack(h)

def exclude_sample(s,id,d,u):
    # 撤样与回库都从最小收获ID开始锁全部收获行：互等同一批行锁，先提交落账者胜，
    # 后来者必须在其锁释放后基于新样本集重算，不覆盖已回库单当时凭据。
    harvests=s.scalars(select(Harvest).order_by(Harvest.id).with_for_update()).all()
    target=next((x for x in harvests if x.id==id),None)
    if not target:raise HTTPException(404,'收获单不存在')
    if target.state!='stored':raise HTTPException(409,'仅已回库的参考收获可查实撤出')
    if target.excluded:raise HTTPException(409,'该收获已撤出参考样本，无需重复撤样')
    target_pollen=s.scalar(select(Pollination).where(Pollination.regeneration_id==target.regeneration_id))
    before=yc.evaluate(s,_crop_of(s,target.regeneration_id),target_pollen.flowers,target.seeds)
    # 置位前先记录哪些待回库单的样本集引用本笔：其既有判断与签字随即失效，重算后再办
    affected=[]
    for h in harvests:
        if h.state!='drying':continue
        refs=yc.reference_samples(s,_crop_of(s,h.regeneration_id))
        if any(z['harvest_id']==id for z in refs):affected.append(h.id)
    target.excluded=True
    s.flush()
    after=yc.evaluate(s,_crop_of(s,target.regeneration_id),target_pollen.flowers,target.seeds)
    rec=HarvestExclusion(harvest_id=id,reason=d.reason.strip(),actor=u.email,
                         affected_harvest_ids=affected,check_snapshot={'before':before,'after':after})
    s.add(rec)
    audit(s,u,'撤出误记参考收获 '+target.code+'；失效待回库判断 '+('、'.join(map(str,affected)) or '无'))
    return add(s,rec)
