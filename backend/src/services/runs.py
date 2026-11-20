import csv,io,logging
from sqlalchemy import select
from fastapi import HTTPException
from src.models.run import Run
from src.models.review import Review
from src.services import studies
from src.services.domain_validation import validate_parameters
from src.services.segmentation import freeze_snapshot
from src.services.audits import log
from src.services.queue import enqueue
from src.utils.serializers import record,now,checksum

def create(db,data,user):
    studies.require(db,data.study_id)
    rows=studies.freeze_rows(db,data.study_id)
    if not rows: raise HTTPException(422,'先录入至少一条业务观测')
    try:
        params=validate_parameters(data.parameters)
        snapshot=freeze_snapshot(rows)
    except (ValueError,TypeError) as e: raise HTTPException(422,str(e))
    run=Run(study_id=data.study_id,name=data.name,parameters=params,snapshot=snapshot,checksum=checksum(snapshot),created_at=now(),status='queued',row_count=len(snapshot),progress={'phase':'queued','label':'等待计算','done':0,'total':len(snapshot)})
    db.add(run);db.flush();log(db,user,'run.queue',str(run.id));db.commit()
    try:
        enqueue(run.id)
    except Exception:
        # The run stays queued; the worker re-queues every queued/running row on
        # startup, so a transient Redis outage delays rather than loses the job.
        logging.exception('enqueue failed; job will be recovered on worker start')
    return _view(run)

SNAPSHOT_DISPLAY_LIMIT=2000

def _view(run,review=None,with_snapshot=False):
    result=record(run)
    snapshot=result.pop('snapshot',None)
    result['snapshot_count']=len(snapshot or [])
    if with_snapshot and result['snapshot_count']<=SNAPSHOT_DISPLAY_LIMIT:
        result['snapshot']=snapshot
    elif with_snapshot:
        result['snapshot']=None
        result['snapshot_omitted']=True
    result['review']=record(review) if review else None
    result['ready']=bool(result.get('result') and result['result'].get('complete'))
    return result

def get(db,id,with_snapshot=False):
    run=db.get(Run,id)
    if not run: raise HTTPException(404,'运行不存在')
    review=db.scalar(select(Review).where(Review.run_id==id))
    return _view(run,review,with_snapshot)

def list_runs(db):
    return [get(db,x) for x in db.scalars(select(Run.id).order_by(Run.id.desc()))]

def approve(db,id,data,user):
    run=db.get(Run,id)
    if not run: raise HTTPException(404,'运行不存在')
    if run.status!='completed': raise HTTPException(409,'只能复核已完成运行')
    if not run.result or not run.result.get('complete'): raise HTTPException(409,'结果不完整，不能复核')
    if db.scalar(select(Review).where(Review.run_id==id)): raise HTTPException(409,'已复核，记录不可覆盖')
    obj=Review(run_id=id,note=data.note,author=user.email,created_at=now());db.add(obj);log(db,user,'review.approve',f'{id}: {data.note}');db.commit();return record(obj)

def export(db,id,user):
    r=get(db,id)
    if r['status']!='completed' or not r['ready']: raise HTTPException(409,'尚无完整结果')
    if user.role=='viewer' and not r['review']: raise HTTPException(403,'观察员只能导出已复核结果')
    stream=io.StringIO();w=csv.writer(stream)
    w.writerow(['运行编号','输入校验SHA256','切分罚分'])
    w.writerow([id,r['checksum'],r['parameters'].get('split_penalty')])
    w.writerow([]);w.writerow(['指标','值'])
    for k,v in r['result']['metrics'].items(): w.writerow([k,v])
    w.writerow([]);w.writerow(['分段（代表解）']);w.writerow(r['result']['columns'])
    for row in r['result']['rows']: w.writerow(row)
    w.writerow([]);w.writerow(['所有最优方案必切位置']);w.writerow(r['result']['mandatory_columns'])
    for row in r['result']['mandatory_cut_rows']: w.writerow(row)
    w.writerow([]);w.writerow(['解释',r['result']['interpretation']]);w.writerow(['边界',r['result']['caveat']])
    log(db,user,'run.export',id);db.commit()
    return '﻿'+stream.getvalue()
