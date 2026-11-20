import math
from src.config.domain import DOMAIN

def validate_values(values):
    expected={f[0] for f in DOMAIN['fields']}
    if set(values)!=expected: raise ValueError('字段必须为 '+', '.join(expected))
    out={}
    for key,label,kind in DOMAIN['fields']:
        v=values[key]
        if kind=='text':
            if v not in ('A','B'): raise ValueError('工艺组必须为A或B')
        else:
            if isinstance(v,bool): raise ValueError(label+'必须是数值')
            try: v=float(v)
            except (TypeError,ValueError): raise ValueError(label+'必须是数值')
            if not math.isfinite(v) or abs(v)>1e9: raise ValueError(label+'必须为有限业务数值')
            if not 0<=v<=100: raise ValueError(label+'必须在0到100之间')
        out[key]=v
    return out
def validate_sequence(sequence):
    if sequence is None: return None
    if isinstance(sequence,bool) or not isinstance(sequence,int): raise ValueError('生产序号必须是整数')
    if not 1<=sequence<=1000000: raise ValueError('生产序号必须在1到1000000之间')
    return sequence

def validate_parameters(params):
    defaults=DOMAIN['defaults']
    if not isinstance(params,dict) or set(params)-set(defaults): raise ValueError('存在未知分析参数')
    p=defaults|params
    value=p.get('split_penalty')
    if isinstance(value,bool) or not isinstance(value,(int,float)): raise ValueError('切分罚分必须是数值')
    value=float(value)
    if not math.isfinite(value) or not 0<=value<=1e9: raise ValueError('切分罚分必须是0到1e9之间的有限数')
    p['split_penalty']=value
    return p
