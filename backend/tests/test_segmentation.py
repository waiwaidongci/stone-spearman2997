"""Segmentation tests.  Run: python -m tests.test_segmentation  (from backend/)"""
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.services import algorithm, segmentation as seg


def rows(n, seed=0, constant_from=None):
    rng = np.random.default_rng(seed)
    seqs = rng.permutation(np.arange(1, n + 1) * 3)
    out = []
    for i in range(n):
        x = float(np.round(rng.uniform(4, 28), 1))
        if constant_from is not None and i >= constant_from:
            x = 5.0
        y = round(max(0, x * 0.25 + rng.normal(0, 1.5)), 2)
        out.append({'source_key': f'S{i}', 'sequence': int(seqs[i]),
                    'values': {'porosity': x, 'absorption': y}})
    return out


def test_matches_oracle():
    for n in (40, 60, 95, 200):
        for penalty in (0.0, 5.0, 40.0, 400.0):
            frozen = seg.freeze_snapshot(rows(n, seed=n + int(penalty)))
            best, mandatory = algorithm.solve(frozen, penalty)
            got = seg.segmented_fit(frozen, penalty, 'x', None)
            total = got['metrics']['残差平方和合计'] + penalty * got['metrics']['切分数']
            assert abs(total - best) <= 1e-6 * max(1, abs(best)), (n, penalty, total, best)
            assert [m['position'] for m in got['mandatory_cuts']] == mandatory


def test_sequence_sorted_and_validated():
    frozen = seg.freeze_snapshot(rows(40, seed=1))
    assert [r['sequence'] for r in frozen] == sorted(r['sequence'] for r in frozen)
    good = {'porosity': 1.0, 'absorption': 2.0}
    for bad in (
        [{'source_key': 'a', 'sequence': 1, 'values': good},
         {'source_key': 'b', 'sequence': 1, 'values': good}],
        [{'source_key': str(i), 'values': good} for i in range(25)],
        [{'source_key': str(i), 'sequence': 0, 'values': good} for i in range(25)],
        [{'source_key': str(i), 'sequence': i, 'values': good} for i in range(19)],
        [{'source_key': 'a', 'sequence': 1, 'values': {'porosity': float('nan'), 'absorption': 2}}]
        + [{'source_key': str(i), 'sequence': i, 'values': good} for i in range(1, 25)],
    ):
        try:
            seg.freeze_snapshot(bad)
        except ValueError:
            continue
        raise AssertionError('invalid snapshot accepted')


def test_constant_porosity_mean_model():
    frozen = seg.freeze_snapshot(rows(150, seed=3, constant_from=0))
    got = seg.segmented_fit(frozen, 1e9, 'x', None)
    assert got['metrics']['分段数'] == 1
    assert got['segments'][0]['model'] == 'mean'
    assert got['segments'][0]['slope'] is None
    y = np.array([r['values']['absorption'] for r in frozen])
    assert abs(got['segments'][0]['rss'] - float(((y - y.mean()) ** 2).sum())) < 1e-6


def test_resume_prefix_not_recomputed():
    frozen = seg.freeze_snapshot(rows(5000, seed=7))
    full = seg.segmented_fit(frozen, 25.0, 'sum', None)
    for phase in ('forward', 'backward', 'verify'):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'chk.jsonl')
            state = {'hit': False}

            def progress(p, done, total, phase=phase):
                if p == phase and done >= 2000 and not state['hit']:
                    state['hit'] = True
                    raise KeyboardInterrupt

            try:
                seg.segmented_fit(frozen, 25.0, 'sum', path, progress)
            except KeyboardInterrupt:
                pass
            assert state['hit'], phase
            resumed = seg.segmented_fit(frozen, 25.0, 'sum', path)
            key = 'cover' if phase == 'verify' else phase
            assert resumed['resumed_cells'][key] >= 2000, (phase, resumed['resumed_cells'])
            assert abs(resumed['metrics']['目标值(RSS+罚分)'] - full['metrics']['目标值(RSS+罚分)']) < 1e-6
            assert [m['position'] for m in resumed['mandatory_cuts']] == [m['position'] for m in full['mandatory_cuts']]
            assert resumed['metrics']['分段数'] == full['metrics']['分段数']


def test_checkpoint_invalidated_by_penalty():
    frozen = seg.freeze_snapshot(rows(3000, seed=8))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'chk.jsonl')
        seg.segmented_fit(frozen, 25.0, 'sum', path)
        other = seg.segmented_fit(frozen, 99.0, 'sum', path)
        assert other['resumed_cells']['forward'] == 0


def test_100k_not_enumerated():
    frozen = seg.freeze_snapshot(rows(100000, seed=11))
    started = time.time()
    got = seg.segmented_fit(frozen, 200.0, 'big', None)
    elapsed = time.time() - started
    assert got['complete'] and got['metrics']['独立试件数'] == 100000
    assert elapsed < 120, elapsed


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            started = time.time()
            fn()
            print(f'{name} ok ({time.time() - started:.1f}s)')
