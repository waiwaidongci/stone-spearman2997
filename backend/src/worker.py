import os,time,json,logging
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session
from src.models.run import Run
from src.models import user,study,observation,review,audit  # noqa: F401  (register tables)
from src.services.segmentation import segmented_fit,split_penalty,cleanup
from src.utils.serializers import checksum
redis=Redis.from_url(os.environ['REDIS_URL'])
ARTIFACTS=Path(os.environ.get('ARTIFACTS_DIR','/app/artifacts'))
CHECKPOINTS=Path(os.environ.get('CHECKPOINTS_DIR','/app/checkpoints'))
ARTIFACTS.mkdir(parents=True,exist_ok=True)
CHECKPOINTS.mkdir(parents=True,exist_ok=True)
PHASES={'forward':'前向动态规划','backward':'后向动态规划','verify':'必切位置校验'}

def execute(db,run,checkpoint):
    """Run one queued/running row; returns True when it finished (ok or error)."""
    total=run.row_count or len(run.snapshot)
    run.status='running';run.error=''
    run.progress={'phase':'forward','label':PHASES['forward'],'done':0,'total':total};db.commit()
    last={'at':0.0}
    def progress(phase,done,total):
        now_=time.time()
        # Persist every block; throttle verify-only ticks to avoid row churn.
        if phase=='verify' and 0<done<total and now_-last['at']<1.0: return
        last['at']=now_
        run.progress={'phase':phase,'label':PHASES.get(phase,phase),'done':int(done),'total':int(total)}
        db.commit()
    try:
        if checksum(run.snapshot)!=run.checksum: raise ValueError('输入快照校验失败')
        result=segmented_fit(run.snapshot,split_penalty(run.parameters),run.checksum,str(checkpoint),progress)
        result['input_sha256']=run.checksum
        run.result=result;run.status='completed'
        run.progress={'phase':'completed','label':'已完成','done':total,'total':total}
        Path(ARTIFACTS/f'run-{run.id}.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
        cleanup(str(checkpoint))
    except Exception as e:
        logging.exception('analysis failed');run.status='failed';run.error=str(e)
        run.progress={'phase':'failed','label':'失败','done':0,'total':total}
    db.commit()
    return True

def main():
    # Recover jobs interrupted after dequeue. A project runs exactly one worker.
    with Session() as db:
        pending=list(db.scalars(select(Run).where(Run.status.in_(['queued','running']))))
        for run in pending:
            run.status='queued';db.commit()
            redis.rpush('analysis-queue',run.id)
    while True:
        Path('/tmp/worker-heartbeat').touch()
        job=redis.blpop('analysis-queue',timeout=2)
        if not job: continue
        id=int(job[1])
        with Session() as db:
            run=db.get(Run,id)
            if not run or run.status in ('completed','failed'): continue
            execute(db,run,CHECKPOINTS/f'run-{run.id}.jsonl')

if __name__=='__main__':
    main()
