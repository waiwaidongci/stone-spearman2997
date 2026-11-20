"""End-to-end service test on SQLite with a stubbed queue and worker loop."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(tmp, 'test.db')
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['ARTIFACTS_DIR'] = os.path.join(tmp, 'artifacts')

import numpy as np
from src.config.database import Base, engine, Session
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.services import runs
from src.services.segmentation import segmented_fit, split_penalty
from src.utils.security import hash_password

Base.metadata.create_all(engine)


class FakeUser:
    def __init__(self, role):
        self.role = role
        self.email = role + '@demo.local'


class FakeInput:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_run(db, study_id, name, penalty):
    # emulate runs.create without Redis by monkeypatching enqueue
    import src.services.runs as runs_mod
    orig = runs_mod.enqueue
    runs_mod.enqueue = lambda rid: rid
    try:
        data = FakeInput(study_id=study_id, name=name, parameters={'split_penalty': penalty})
        return runs.create(db, data, FakeUser('analyst'))
    finally:
        runs_mod.enqueue = orig


def execute(db, run_id, checkpoint_dir):
    """Mirror worker logic, writing results to the DB."""
    from src.models.run import Run
    run = db.get(Run, run_id)
    run.status = 'running'
    db.commit()
    progress_rows = []
    result = segmented_fit(run.snapshot, split_penalty(run.parameters), run.checksum,
                           os.path.join(checkpoint_dir, f'run-{run_id}.jsonl'),
                           lambda p, d, t: progress_rows.append((p, d, t)))
    result['input_sha256'] = run.checksum
    run.result = result
    run.status = 'completed'
    db.commit()
    return result, progress_rows


rng = np.random.default_rng(7)
with Session() as db:
    db.add(User(email='analyst@demo.local', role='analyst', password_hash=hash_password('x')))
    db.add(User(email='viewer@demo.local', role='viewer', password_hash=hash_password('x')))
    db.add(User(email='admin@demo.local', role='admin', password_hash=hash_password('x')))
    s = Study(name='测试批次', source_filename='t.csv', description='')
    db.add(s); db.flush()
    rows = []
    n = 700
    for i in range(n):
        if i >= 400:
            x = float(np.round(rng.uniform(16, 28), 1)); y = round(x * 0.42 + rng.normal(0, 1.2), 2)
        else:
            x = float(np.round(rng.uniform(4, 28), 1)); y = round(x * 0.18 + rng.normal(0, 1.5), 2)
        rows.append(Observation(study_id=s.id, source_key=f'K{i+1:04}', sequence=n - i, values={'porosity': x, 'absorption': y}))
    db.add_all(rows); db.commit()
    study_id = s.id

    # create run: freezes sequence and penalty
    r = make_run(db, study_id, '集成测试', 60)
    assert r['snapshot_count'] == 700
    from src.models.run import Run as _R
    snap=db.get(_R, r['id']).snapshot
    assert [x['sequence'] for x in snap] == sorted(x['sequence'] for x in snap)
    assert r['parameters']['split_penalty'] == 60
    assert r['progress']['phase'] == 'queued'
    run_id = r['id']

    ck = os.path.join(tmp, 'ck'); os.makedirs(ck)
    result, progress = execute(db, run_id, ck)
    assert result['complete']
    assert 'mandatory_cut_rows' in result and 'rows' in result
    assert result['metrics']['切分罚分'] == 60

    # immutability: mutate observations after the run; result must not change
    target = db.query(Observation).filter_by(study_id=study_id).first()
    target.values = {'porosity': 1.0, 'absorption': 1.0}
    db.commit()
    again = runs.get(db, run_id)
    assert again['checksum'] == r['checksum']
    assert again['result']['metrics']['残差平方和合计'] == result['metrics']['残差平方和合计']

    # incomplete result must not be approvable / exportable
    from src.models.run import Run
    run = db.get(Run, run_id)
    saved = run.result
    run.result = {'complete': False}
    db.commit()
    try:
        runs.approve(db, run_id, FakeInput(note='尝试复核'), FakeUser('admin'))
        raise SystemExit('incomplete result approved')
    except Exception as e:
        print('incomplete approve blocked:', getattr(e, 'detail', e))
    try:
        runs.export(db, run_id, FakeUser('admin'))
        raise SystemExit('incomplete exported')
    except Exception as e:
        print('incomplete export blocked:', getattr(e, 'detail', e))
    run.result = saved; db.commit()

    # duplicate sequence on a second specimen is rejected on create
    dup = Observation(study_id=study_id, source_key='DUP', sequence=1, values={'porosity': 1, 'absorption': 1})
    db.add(dup)
    try:
        db.commit()
        raise SystemExit('db accepted duplicate sequence')
    except Exception:
        db.rollback(); print('duplicate sequence rejected by DB')

    # incomplete data: a specimen without a sequence blocks the run
    db.add(Observation(study_id=study_id, source_key='NOSEQ', sequence=None, values={'porosity': 5, 'absorption': 1}))
    db.commit()
    try:
        make_run(db, study_id, '缺序号', 10)
        raise SystemExit('run created with missing sequence')
    except Exception as err:
        print('missing sequence blocked:', getattr(err, 'detail', err))
    db.query(Observation).filter_by(source_key='NOSEQ').delete(); db.commit()

    # export contents include segments and mandatory cuts
    text = runs.export(db, run_id, FakeUser('admin'))
    assert '所有最优方案必切位置' in text and '分段（代表解）' in text
    assert 'INPUT' not in text.splitlines()[0]
    print('export sections OK')

print('end-to-end service test OK')
