from sqlalchemy import Column,Integer,String,JSON,ForeignKey,Index
from src.config.database import Base
class Specimen(Base):
    __tablename__='observations'
    __table_args__=(Index('uq_observations_study_source','study_id','source_key',unique=True),Index('uq_observations_study_sequence','study_id','sequence',unique=True),)
    id=Column(Integer,primary_key=True)
    study_id=Column(Integer,ForeignKey('studies.id'),nullable=False)
    source_key=Column(String(100),nullable=False)
    sequence=Column(Integer,nullable=True)
    values=Column(JSON,nullable=False)
Observation=Specimen
