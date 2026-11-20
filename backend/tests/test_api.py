import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(tmp, 'api.db')
os.environ['REDIS_URL'] = 'redis://localhost:6399/0'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['SEED_DEMO'] = 'false'

import numpy as np
from fastapi.testclient import TestClient
import src.services.runs as runs_mod

# Stub queue so creating a run does not need Redis.
runs_mod.enqueue = lambda rid: rid

import src.middlewares.rate_limit as rl
rl.redis = type('R', (), {'incr': lambda self, k: 1, 'expire': lambda self, k, s: True})()

from src.main import app
from src.config.database import Base, engine, Session
from src.models.user import User
from src.models.study import Study
from src.utils.security import hash_password

Base.metadata.create_all(engine)
with Session() as db:
    db.add_all([
        User(email='admin@demo.local', role='admin', password_hash=hash_password('pw')),
        User(email='viewer@demo.local', role='viewer', password_hash=hash_password('pw')),
        User(email='analyst@demo.local', role='analyst', password_hash=hash_password('pw')),
    ])
    db.add(Study(name='API批次', source_filename='api.csv', description='')); db.commit()

with TestClient(app) as client:
    def token(email):
        r = client.post('/auth/login', json={'email': email, 'password': 'pw'})
        assert r.status_code == 200, r.text
        return r.json()['token']

    admin = {'Authorization': 'Bearer ' + token('admin@demo.local')}
    viewer = {'Authorization': 'Bearer ' + token('viewer@demo.local')}

    studies = client.get('/studies', headers=admin).json()
    sid = [s for s in studies if s['name'] == 'API批次'][0]['id']

    rows = []
    rng = np.random.default_rng(0)
    for i in range(60):
        x = float(np.round(rng.uniform(4, 28), 1))
        rows.append({'source_key': f'R{i+1:03}', 'sequence': i + 1,
                     'values': {'porosity': x, 'absorption': round(x * 0.2 + rng.normal(0, 1), 2)}})
    for row in rows:
        r = client.post(f'/studies/{sid}/observations', json=row, headers=admin)
        assert r.status_code == 200, r.text
    listed = client.get(f'/studies/{sid}/observations', headers=admin).json()
    assert listed['total'] == 60 and listed['rows'][0]['sequence'] == 1
    page2 = client.get(f'/studies/{sid}/observations?offset=50&limit=100', headers=admin).json()
    assert len(page2['rows']) == 10
    bad_page = client.get(f'/studies/{sid}/observations?limit=1000', headers=admin)
    assert bad_page.status_code == 422

    duplicate = client.post(f'/studies/{sid}/observations',
                            json={'source_key': 'X', 'sequence': 1, 'values': {'porosity': 1, 'absorption': 1}},
                            headers=admin)
    assert duplicate.status_code == 409, duplicate.text
    print('duplicate sequence ->', duplicate.json()['detail'])

    missing = client.post(f'/studies/{sid}/observations',
                          json={'source_key': 'Y', 'values': {'porosity': 1, 'absorption': 1}},
                          headers=admin)
    assert missing.status_code == 200, missing.text

    bad_run = client.post('/runs', json={'study_id': sid, 'name': '缺序号', 'parameters': {'split_penalty': 10}}, headers=admin)
    assert bad_run.status_code == 422, bad_run.text
    print('missing sequence run ->', bad_run.json()['detail'])
    client.delete(f"/studies/{sid}/observations/{missing.json()['id']}", headers=admin)

    bad_param = client.post('/runs', json={'study_id': sid, 'name': '坏参数', 'parameters': {'split_penalty': -1}}, headers=admin)
    assert bad_param.status_code == 422
    unknown = client.post('/runs', json={'study_id': sid, 'name': '未知参数', 'parameters': {'resamples': 10}}, headers=admin)
    assert unknown.status_code == 422
    print('parameter validation OK')

    created = client.post('/runs', json={'study_id': sid, 'name': 'API分段', 'parameters': {'split_penalty': 30}}, headers=admin)
    assert created.status_code == 200, created.text
    run = created.json()
    assert run['snapshot_count'] == 60
    rid = run['id']

    got = client.get(f'/runs/{rid}', headers=admin).json()
    assert got['status'] == 'queued' and got['ready'] is False

    # viewer cannot create
    denied = client.post('/runs', json={'study_id': sid, 'name': '观察员尝试', 'parameters': {}}, headers=viewer)
    assert denied.status_code == 403

    # export requires complete result
    blocked = client.get(f'/runs/{rid}/export', headers=admin)
    assert blocked.status_code == 409

    # run the worker computation inline
    from src.services.segmentation import segmented_fit, split_penalty
    with Session() as db:
        from src.models.run import Run
        obj = db.get(Run, rid)
        result = segmented_fit(obj.snapshot, split_penalty(obj.parameters), obj.checksum, None)
        result['input_sha256'] = obj.checksum
        obj.result = result; obj.status = 'completed'; db.commit()

    got = client.get(f'/runs/{rid}', headers=admin).json()
    assert got['ready'] is True and got['status'] == 'completed'
    assert got['result']['mandatory_cut_rows'] is not None
    print('run completed with', got['result']['metrics'])

    csv = client.get(f'/runs/{rid}/export', headers=admin)
    assert csv.status_code == 200
    assert '所有最优方案必切位置' in csv.text

    # viewer export before review is forbidden
    v = client.get(f'/runs/{rid}/export', headers=viewer)
    assert v.status_code == 403

    review = client.post(f'/runs/{rid}/review', json={'note': '分段边界仅用于复测安排'}, headers=admin)
    assert review.status_code == 200, review.text
    overlap = client.post(f'/runs/{rid}/review', json={'note': '第二次复核尝试'}, headers=admin)
    assert overlap.status_code == 409
    v = client.get(f'/runs/{rid}/export', headers=viewer)
    assert v.status_code == 200

    audits = client.get('/audits', headers=admin).json()
    assert any(a['action'] == 'run.export' for a in audits)
    print('API integration OK; audit rows:', len(audits))
