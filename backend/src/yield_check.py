import math
from sqlalchemy import select
from .models import Accession,Lot,Regeneration,Pollination,Harvest

MIN_SAMPLES=3
CONFIDENCE=.95
# 同作物已回库收获按 套袋花数(x)→净收粒数(y) 做一元OLS，
# 预测区间用 t 分位数的预测标准误（含新观测噪声），不依赖scipy。

def _betacf(a,b,x):
    eps=3e-12;fpmin=1e-300;qab=a+b;qap=a+1;qam=a-1
    c=1;d=1-qab*x/qap
    if abs(d)<fpmin:d=fpmin
    d=1/d;h=d
    for m in range(1,201):
        m2=2*m
        aa=m*(b-m)*x/((qam+m2)*(a+m2))
        d=1+aa*d
        if abs(d)<fpmin:d=fpmin
        c=1+aa/c
        if abs(c)<fpmin:c=fpmin
        d=1/d;h*=d*c
        aa=-(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d=1+aa*d
        if abs(d)<fpmin:d=fpmin
        c=1+aa/c
        if abs(c)<fpmin:c=fpmin
        d=1/d;de=d*c;h*=de
        if abs(de-1)<eps:break
    return h

def _betai(a,b,x):
    if x<=0:return 0.0
    if x>=1:return 1.0
    bt=math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    if x<(a+1)/(a+b+2):return bt*_betacf(a,b,x)/a
    return 1-bt*_betacf(b,a,1-x)/b

def _t_sf(t,df):
    return .5*_betai(df/2,.5,df/(df+t*t))

def t_critical(df,confidence=CONFIDENCE):
    # 双侧临界值：P(|T|<=t)=confidence，上尾概率=(1-confidence)/2，二分求解
    alpha=(1-confidence)/2;lo,hi=0.0,1.0
    while _t_sf(hi,df)>alpha:hi*=2
    for _ in range(80):
        mid=(lo+hi)/2
        if _t_sf(mid,df)>alpha:lo=mid
        else:hi=mid
    return (lo+hi)/2

def reference_samples(s,crop):
    rows=s.scalars(select(Harvest).join(Regeneration,Regeneration.id==Harvest.regeneration_id)
                   .join(Lot,Lot.id==Regeneration.lot_id).join(Accession,Accession.id==Lot.accession_id)
                   .where(Accession.crop==crop,Harvest.state=='stored',Harvest.excluded==False)
                   .order_by(Harvest.id)).all()
    out=[]
    for h in rows:
        p=s.scalar(select(Pollination).where(Pollination.regeneration_id==h.regeneration_id))
        if p:out.append({'harvest_id':h.id,'code':h.code,'flowers':p.flowers,'seeds':h.seeds})
    return out

def fit(samples,x0,observed=None):
    n=len(samples)
    base={'status':'insufficient_samples' if n<MIN_SAMPLES else 'ok','sample_size':n,
          'min_samples':MIN_SAMPLES,'confidence':CONFIDENCE,'flowers':x0,'observed_seeds':observed,
          'samples':samples,'model':'OLS 净收粒数 ~ 套袋花数；预测区间=均值±t(0.975,df)×残差标准差×sqrt(1+1/n+(x0-x̄)²/Sxx)'}
    if n<MIN_SAMPLES:
        base['reason']='同作物已回库参考收获不足%d笔，无法拟合预测区间'%MIN_SAMPLES
        base['within']=None;return base
    xs=[z['flowers'] for z in samples];ys=[z['seeds'] for z in samples]
    xbar=sum(xs)/n;ybar=sum(ys)/n
    sxx=sum((x-xbar)**2 for x in xs);sxy=sum((x-xbar)*(y-ybar) for x,y in zip(xs,ys))
    sst=sum((y-ybar)**2 for y in ys)
    if sxx<=0:
        # 套袋花数完全一致，退化为均值模型，自由度n-1
        df=n-1;slope=0.0;intercept=ybar;sse=sst
        r2=0.0
    else:
        df=n-2;slope=sxy/sxx;intercept=ybar-slope*xbar
        sse=sum((y-(intercept+slope*x))**2 for x,y in zip(xs,ys))
        r2=1-sse/sst if sst>0 else 1.0
    s2=sse/df if df>=1 else 0.0
    tcrit=t_critical(df)
    if sxx<=0:se_pred=math.sqrt(s2*(1+1/n))
    else:se_pred=math.sqrt(s2*(1+1/n+(x0-xbar)**2/sxx))
    mean=intercept+slope*x0;half=tcrit*se_pred
    lower,upper=mean-half,mean+half
    lower_int=max(1,math.floor(lower));upper_int=math.ceil(upper)
    within=None if observed is None else lower_int<=observed<=upper_int
    return base|{'status':'ok','intercept':round(intercept,4),'slope':round(slope,4),
                 'r_squared':round(r2,4),'residual_std':round(math.sqrt(s2),4),'df':df,
                 't_critical':round(tcrit,4),'predicted_seeds':round(mean,2),
                 'lower95':round(lower,2),'upper95':round(upper,2),
                 'lower95_int':lower_int,'upper95_int':upper_int,'within':within,
                 'degenerate_x':sxx<=0}

def evaluate(s,crop,flowers,observed=None):
    samples=reference_samples(s,crop)
    res=fit(samples,flowers,observed);res['crop']=crop
    return res
