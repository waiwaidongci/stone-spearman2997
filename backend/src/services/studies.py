from fastapi import HTTPException
from sqlalchemy import select,func
from src.config.domain import DOMAIN
from src.models.study import Study
from src.models.observation import Observation
from src.services.domain_validation import validate_values,validate_sequence
from src.services.audits import log
from src.utils.serializers import record
MAX_ROWS=DOMAIN.get('limits',{}).get('max_rows',100000)
PAGE_SIZE=DOMAIN.get('limits',{}).get('page_size',100)

def list_studies(db):
    data=[]
    for s in db.scalars(select(Study).order_by(Study.id)):
        x=record(s);x['row_count']=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==s.id));x['missing_sequence']=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==s.id,Observation.sequence.is_(None)));data.append(x)
    return data

def create(db,data,user):
    if db.scalar(select(Study).where(Study.name==data.name)): raise HTTPException(409,'批次名称已存在')
    s=Study(**data.model_dump());db.add(s);log(db,user,'study.create',data.name);db.commit();return record(s)

def require(db,id):
    s=db.get(Study,id)
    if not s: raise HTTPException(404,'业务批次不存在')
    return s

def observations(db,id):
    """All specimens ordered by production sequence (None last), used for freezing."""
    require(db,id)
    return [record(x) for x in db.scalars(select(Observation).where(Observation.study_id==id).order_by(Observation.sequence.is_(None),Observation.sequence,Observation.id))]

def freeze_rows(db,id):
    """Lightweight sequence + values rows for freezing a run snapshot."""
    require(db,id)
    stmt=select(Observation.source_key,Observation.sequence,Observation.values).where(Observation.study_id==id).order_by(Observation.sequence.is_(None),Observation.sequence,Observation.id)
    return [{'source_key':key,'sequence':sequence,'values':values} for key,sequence,values in db.execute(stmt)]

def page(db,id,offset=0,limit=PAGE_SIZE):
    require(db,id)
    if offset<0 or limit<1 or limit>PAGE_SIZE: raise HTTPException(422,'分页参数超出范围')
    total=db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==id))
    rows=[record(x) for x in db.scalars(select(Observation).where(Observation.study_id==id).order_by(Observation.sequence.is_(None),Observation.sequence,Observation.id).offset(offset).limit(limit))]
    return {'total':total,'offset':offset,'limit':limit,'rows':rows}

def save_observation(db,id,data,user,row_id=None):
    require(db,id)
    try:
        values=validate_values(data.values);sequence=validate_sequence(data.sequence)
    except (ValueError,TypeError) as e: raise HTTPException(422,str(e))
    other=db.scalar(select(Observation).where(Observation.study_id==id,Observation.source_key==data.source_key))
    if other and other.id!=row_id: raise HTTPException(409,'源记录编号重复')
    if sequence is not None:
        clash=db.scalar(select(Observation).where(Observation.study_id==id,Observation.sequence==sequence))
        if clash and clash.id!=row_id: raise HTTPException(409,'生产序号重复')
    if row_id:
        row=db.get(Observation,row_id)
        if not row or row.study_id!=id: raise HTTPException(404,'记录不存在')
        row.source_key=data.source_key;row.sequence=sequence;row.values=values
    else:
        if db.scalar(select(func.count()).select_from(Observation).where(Observation.study_id==id))>=MAX_ROWS: raise HTTPException(422,f'每个批次最多{MAX_ROWS}条')
        row=Observation(study_id=id,source_key=data.source_key,sequence=sequence,values=values);db.add(row)
    log(db,user,'observation.save',f'{id}/{data.source_key}'+(f' seq={sequence}' if sequence is not None else ''));db.commit();return record(row)
