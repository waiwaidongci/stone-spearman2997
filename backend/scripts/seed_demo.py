import json
import numpy as np
from sqlalchemy import select,func
from src.config.database import Base,engine,Session
from src.config.domain import DOMAIN
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.utils.security import hash_password

def _rows(k,n,rng):
    """Independent synthetic specimens with a production sequence.

    Study 0 changes its porosity/absorption relationship halfway through
    production; study 1 is a stable run; study 2 is short and stable.
    """
    rows=[]
    for i in range(n):
        sequence=1+i
        if k==0 and i>=n//2:
            x=round(float(rng.uniform(16,28)),1)
            y=round(max(0,x*0.42+rng.normal(0,1.2)),2)
        else:
            x=round(float(rng.uniform(4,28)),1)
            y=round(max(0,x*(0.18+k*0.02)+rng.normal(0,1.5)),2)
        rows.append((sequence,{'porosity':x,'absorption':y}))
    return rows

def seed():
    Base.metadata.create_all(engine)
    rng=np.random.default_rng(42)
    counts=[900,640,240]
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)): db.add(User(email=email,role=role,password_hash=hash_password('Research2026!')))
        for k,name in enumerate(DOMAIN['names']):
            study=db.scalar(select(Study).where(Study.name==name))
            if not study:
                study=Study(name=name,source_filename=DOMAIN['slug']+f'-202611-{k+1}.csv',description='独立合成业务观测 / 固定种子42 / 仅用于方法与流程演示');db.add(study);db.flush()
            for sequence,values in _rows(k,counts[k],rng):
                key=f'DEMO-{k+1}-{sequence:04}'
                if not db.scalar(select(Observation.id).where(Observation.study_id==study.id,Observation.source_key==key)):
                    db.add(Observation(study_id=study.id,source_key=key,sequence=sequence,values=values))
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (User,Study,Observation)}
        print(json.dumps(counts));return counts
if __name__=='__main__': seed()
