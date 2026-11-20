"""Worker-level test: run, interrupt mid-way, restart, and confirm the DB row
completes with an identical result and that the restart resumes from the
checkpoint rather than recomputing the confirmed prefix."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(tmp, 'worker.db')
os.environ['REDIS_URL'] = 'redis://localhost:6399/0'  # never contacted below
os.environ['JWT_SECRET'] = 'test'
os.environ['ARTIFACTS_DIR'] = os.path.join(tmp, 'artifacts')
os.environ['CHECKPOINTS_DIR'] = os.path.join(tmp, 'checkpoints')

import numpy as np
from src.config.database import Base, engine, Session
from src.models.study import Study
from src.models.observation import Observation
from src.models.run import Run
from src.utils.serializers import checksum, now
from src.services.segmentation import freeze_snapshot

Base.metadata.create_all(engine)

with Session() as db:
    study = Study(name='worker批次', source_filename='w.csv', description='')
    db.add(study); db.flush()
    rng = np.random.default_rng(2)
    for i in range(8000):
        x = round(float(rng.uniform(4, 28)), 1)
        y = round(x * (0.18 if i < 4000 else 0.45) + rng.normal(0, 1), 2)
        db.add(Observation(study_id=study.id, source_key=f'S{i}', sequence=i + 1,
                           values={'porosity': x, 'absorption': y}))
    db.commit()
    sid = study.id

# Freeze a run exactly as runs.create does, without Redis.
rows = []
with Session() as db:
    from sqlalchemy import select
    rows = [{'source_key': k, 'sequence': s, 'values': v}
            for k, s, v in db.execute(select(Observation.source_key, Observation.sequence, Observation.values)
                                      .where(Observation.study_id == sid).order_by(Observation.sequence))]
snapshot = freeze_snapshot(rows)
with Session() as db:
    run = Run(study_id=sid, name='中断续算', parameters={'split_penalty': 40.0},
              snapshot=snapshot, checksum=checksum(snapshot), created_at=now(),
              status='queued', row_count=len(snapshot))
    db.add(run); db.commit(); rid = run.id

import src.worker as worker

# 1) interrupt the real worker mid-forward pass
with Session() as db:
    obj = db.get(Run, rid)
    orig = worker.segmented_fit
    state = {'hit': False}

    def interrupted(snapshot, penalty, cksum, path, progress):
        def p(phase, done, total):
            progress(phase, done, total)
            if phase == 'forward' and done >= 2000 and not state['hit']:
                state['hit'] = True
                raise KeyboardInterrupt
        return orig(snapshot, penalty, cksum, path, p)

    worker.segmented_fit = interrupted
    try:
        worker.execute(db, obj, worker.CHECKPOINTS / f'run-{rid}.jsonl')
    except KeyboardInterrupt:
        pass
    worker.segmented_fit = orig

with Session() as db:
    obj = db.get(Run, rid)
    # Simulate worker restart: running rows are re-queued and progress is retained.
    obj.status = 'running'
    db.commit()
    assert obj.result is None
    assert obj.progress['phase'] == 'forward' and obj.progress['done'] >= 2000

    worker.execute(db, obj, worker.CHECKPOINTS / f'run-{rid}.jsonl')
    obj = db.get(Run, rid)
    assert obj.status == 'completed' and obj.result['complete']
    resumed = obj.result['resumed_cells']
    assert resumed['forward'] >= 2000, resumed
    assert obj.progress['phase'] == 'completed'

# 2) a fresh run over the same input must agree exactly
with Session() as db:
    fresh = Run(study_id=sid, name='对照', parameters={'split_penalty': 40.0},
                snapshot=snapshot, checksum=checksum(snapshot), created_at=now(),
                status='queued', row_count=len(snapshot))
    db.add(fresh); db.commit()
    worker.execute(db, fresh, worker.CHECKPOINTS / f'run-{fresh.id}.jsonl')
    fresh = db.get(Run, fresh.id)
    done = db.get(Run, rid)
    assert fresh.result['mandatory_cut_rows'] == done.result['mandatory_cut_rows']
    assert fresh.result['rows'] == done.result['rows']
    assert fresh.result['metrics'] == done.result['metrics']

print('worker resume test OK', resumed)
