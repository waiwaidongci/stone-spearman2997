from pydantic import BaseModel,Field
class SpecimenInput(BaseModel):
    source_key:str=Field(min_length=1,max_length=100)
    sequence:int|None=Field(default=None,ge=1,le=1000000)
    values:dict
ObservationInput=SpecimenInput
