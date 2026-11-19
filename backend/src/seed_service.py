from sqlalchemy import select,func,inspect,text
from .db import Base,engine,Session
from .models import *
from .auth import hashed

def _ensure_columns():
    # 项目无迁移框架：旧库的 harvests 表需要补列，新表由 create_all 直接建立
    insp=inspect(engine);existing={c['name'] for c in insp.get_columns('harvests')}
    ddl=[]
    if 'excluded' not in existing:ddl.append('ALTER TABLE harvests ADD COLUMN excluded BOOLEAN NOT NULL DEFAULT 0')
    if 'yield_check' not in existing:ddl.append('ALTER TABLE harvests ADD COLUMN yield_check JSON NULL')
    if ddl:
        with engine.begin() as conn:
            for stmt in ddl:conn.execute(text(stmt))

def seed():
    Base.metadata.create_all(engine)
    _ensure_columns()
    with Session() as s:
        for role in ('operator','supervisor','viewer'):
            email=role+'@qinghe.demo'
            if not s.scalar(select(User).where(User.email==email)):s.add(User(email=email,role=role,password_hash=hashed('Seeds2026!')))
        a=s.scalar(select(Accession).where(Accession.code=='QH-021'))
        if not a:a=Accession(code='QH-021',crop='青豆',name='青禾早熟青豆',origin='青禾北圃2023采集',minimum=300);s.add(a);s.flush()
        for code,generation,available in [('QH-021-G3',3,960),('QH-021-POLLEN',3,400)]:
            if not s.scalar(select(Lot).where(Lot.code==code)):s.add(Lot(accession_id=a.id,code=code,generation=generation,available=available,stored_months=36,temperature=4,initial_rate=94))
        other=s.scalar(select(Accession).where(Accession.code=='QH-087'))
        if not other:other=Accession(code='QH-087',crop='青豆',name='异源晚熟青豆',origin='南坡异源对照',minimum=300);s.add(other);s.flush()
        if not s.scalar(select(Lot).where(Lot.code=='QH-087-G2')):s.add(Lot(accession_id=other.id,code='QH-087-G2',generation=2,available=800,stored_months=18,temperature=-8,initial_rate=96))
        for i in range(18):
            code=chr(65+i//6)+str(i%6+1)
            if not s.scalar(select(Plot).where(Plot.code==code)):s.add(Plot(code=code,row=i//6,position=i%6,capacity=250))
        s.flush();lot=s.scalar(select(Lot).where(Lot.code=='QH-021-G3'))
        if not s.scalar(select(GerminationTest).where(GerminationTest.code=='GT-SEED-80')):s.add(GerminationTest(lot_id=lot.id,code='GT-SEED-80',sampled=100,germinated=80,state='recorded'))
        # 同作物已回库历史收获：作为“套袋花数→净收粒数”预测区间的参考样本（仅演示数据）
        if not s.scalar(select(Harvest).where(Harvest.code=='HV-DEMO-REF-01')):
            refs=[('HV-DEMO-REF-01',30,1390),('HV-DEMO-REF-02',35,1605),('HV-DEMO-REF-03',40,1780),('HV-DEMO-REF-04',45,2040)]
            plots=s.scalars(select(Plot).order_by(Plot.id)).all()
            for idx,(hcode,flowers,seeds) in enumerate(refs):
                rcode='RG-DEMO-REF-%02d'%(idx+1);gcode='GT-DEMO-REF-%02d'%(idx+1)
                gt=GerminationTest(lot_id=lot.id,code=gcode,sampled=100,germinated=82,state='ready',
                                   prediction={'observed_pct':82.0,'renewal_required':True})
                s.add(gt);s.flush()
                rg=Regeneration(code=rcode,test_id=gt.id,lot_id=lot.id,issued=60,state='harvested',
                                reason='演示参考批次：历史同作物复壮回库',vitality_snapshot=gt.prediction);s.add(rg);s.flush()
                plot=plots[idx+6]
                s.add(Flowering(regeneration_id=rg.id,plot_id=plot.id,start='2025-09-0%d'%(idx+1),end='2025-09-%02d'%(idx+6),state='released'))
                s.add(Pollination(regeneration_id=rg.id,mother_lot_id=lot.id,
                                  father_lot_id=s.scalar(select(Lot).where(Lot.code=='QH-021-POLLEN')).id,
                                  bag_code='BAG-DEMO-REF-%02d'%(idx+1),bagged=True,flowers=flowers,state='pollinated'))
                child=Lot(accession_id=a.id,code=hcode+'-G4',generation=4,available=seeds,
                          stored_months=0,temperature=-18,initial_rate=94,parent_lot_id=lot.id);s.add(child);s.flush()
                s.add(Harvest(regeneration_id=rg.id,code=hcode,seeds=seeds,moisture=10.2,state='stored',
                              offspring_lot_id=child.id,lineage={'demo_reference':True,'note':'演示参考批次，用于同作物预测区间拟合'}))
        s.commit();return {cls.__tablename__:s.scalar(select(func.count()).select_from(cls))for cls in (User,Accession,Lot,GerminationTest,Regeneration,Plot,Flowering,Pollination,Harvest,HarvestExclusion,Distribution)}
