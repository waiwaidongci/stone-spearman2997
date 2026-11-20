"""Production-sequence segmented regression.

Specimens are ordered by their unique production sequence.  Every segment
covers 20..200 consecutive specimens and fits absorption on porosity by
ordinary least squares; a segment whose porosity is constant falls back to
fitting the mean absorption only.  Each cut costs the same frozen penalty.

The optimal segmentation is found by dynamic programming in
O(n * (MAX_SEGMENT - MIN_SEGMENT + 1)) time, never by enumerating
segmentations.  Cut positions shared by *every* optimal segmentation are
derived from forward/backward optimal costs: a position is mandatory only
when no optimal segment spans across it.

Forward values, predecessors, backward values and the tight-segment scan are
checkpointed incrementally, so an interrupted run resumes from the last
confirmed prefix instead of recomputing it.
"""
import json
import math
import os

import numpy as np

MIN_SEGMENT = 20
MAX_SEGMENT = 200
DEFAULT_SPLIT_PENALTY = 25.0
CHECKPOINT_VERSION = 1
_BLOCK = 2000          # checkpoint every N dynamic-programming cells
_TOL_FACTOR = 1e-9     # relative tolerance when deciding "optimal"


def freeze_snapshot(rows):
    """Validate unique production sequences and numeric values, then order."""
    if not rows:
        raise ValueError('空批次无法分析')
    seen = set()
    frozen = []
    for row in rows:
        sequence = row.get('sequence')
        if sequence is None:
            raise ValueError('分段拟合要求为每个试件填写生产序号')
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ValueError('生产序号必须是整数')
        if sequence < 1:
            raise ValueError('生产序号必须为正整数')
        if sequence in seen:
            raise ValueError(f'生产序号 {sequence} 重复，请先修正后再发起研究')
        seen.add(sequence)
        values = row.get('values') or {}
        clean = {}
        for key in ('porosity', 'absorption'):
            if key not in values:
                raise ValueError('观测字段不完整')
            try:
                value = float(values[key])
            except (TypeError, ValueError):
                raise ValueError('观测必须为数值')
            if not math.isfinite(value):
                raise ValueError('观测必须为有限数值')
            clean[key] = value
        frozen.append({'source_key': row['source_key'], 'sequence': sequence, 'values': clean})
    if len(frozen) < MIN_SEGMENT:
        raise ValueError(f'分段拟合至少需要 {MIN_SEGMENT} 个试件，当前 {len(frozen)} 个')
    frozen.sort(key=lambda item: item['sequence'])
    return frozen


def split_penalty(params):
    value = (params or {}).get('split_penalty', DEFAULT_SPLIT_PENALTY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('切分罚分必须是数值')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('切分罚分必须是有限非负数')
    return value


def _prefix(values):
    return np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))


def _prepare(snapshot):
    sequences = []
    xs = []
    ys = []
    for row in snapshot:
        sequence = row.get('sequence')
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ValueError('分段拟合要求为每个试件填写整数生产序号')
        sequences.append(sequence)
        values = row.get('values') or {}
        for key in ('porosity', 'absorption'):
            if key not in values:
                raise ValueError('观测字段不完整')
            value = float(values[key])
            if not math.isfinite(value):
                raise ValueError('观测必须为有限数值')
            (xs if key == 'porosity' else ys).append(value)
    if len(set(sequences)) != len(sequences):
        raise ValueError('生产序号必须唯一')
    n = len(sequences)
    if n < MIN_SEGMENT:
        raise ValueError(f'分段拟合至少需要 {MIN_SEGMENT} 个试件')
    order = np.argsort(np.asarray(sequences, dtype=np.int64), kind='stable')
    sequences = [sequences[i] for i in order]
    x = np.asarray(xs, dtype=np.float64)[order]
    y = np.asarray(ys, dtype=np.float64)[order]
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    xc = x - x_mean
    yc = y - y_mean
    return {
        'sequences': sequences,
        'n': n,
        'x_mean': x_mean,
        'y_mean': y_mean,
        'px': _prefix(xc),
        'py': _prefix(yc),
        'pxx': _prefix(xc * xc),
        'pyy': _prefix(yc * yc),
        'pxy': _prefix(xc * yc),
    }


def _costs(prep, a, b):
    """Residual sum of squares for every segment [a, b).

    Returns (rss, linear) where ``linear`` is false for a constant-porosity
    segment, whose fit is the segment mean.
    """
    length = (b - a).astype(np.float64)
    sx = prep['px'][b] - prep['px'][a]
    sy = prep['py'][b] - prep['py'][a]
    sxx = prep['pxx'][b] - prep['pxx'][a]
    syy = prep['pyy'][b] - prep['pyy'][a]
    sxy = prep['pxy'][b] - prep['pxy'][a]
    syy_c = syy - sy * sy / length
    sxx_c = sxx - sx * sx / length
    sxy_c = sxy - sx * sy / length
    linear = (sxx > 0) & (sxx_c > 1e-9 * np.maximum(1.0, sxx))
    denominator = np.where(linear, sxx_c, 1.0)
    rss = syy_c - np.where(linear, sxy_c * sxy_c / denominator, 0.0)
    return np.maximum(rss, 0.0), linear


class _Checkpoint:
    """Append-only JSONL checkpoint.  A torn trailing line is discarded."""

    def __init__(self, path, meta):
        self.path = path
        self.meta = meta
        self.handle = None
        self.f = None
        self.pred = None
        self.g = None
        self.diff = None
        self.done = set()
        self.forward_next = 0
        self.backward_next = -1
        self.cover_next = MIN_SEGMENT
        self.resumed = {'forward': 0, 'backward': 0, 'cover': 0}

    def open(self):
        n = self.meta['n']
        self.f = np.full(n + 1, np.inf)
        self.pred = np.full(n + 1, -1, dtype=np.int32)
        self.g = np.full(n + 1, np.inf)
        self.diff = np.zeros(n + 2, dtype=np.int64)
        # DP base cases are invariant; set them before any record is replayed so
        # in-memory and resumed runs share identical boundary costs.
        self.f[0] = 0.0
        self.g[n] = 0.0
        records = self._read()
        if not self.path:
            self.handle = None
            return
        if records is None:
            self.handle = open(self.path, 'w', encoding='utf-8')
            self._write({'kind': 'meta', 'version': CHECKPOINT_VERSION, **self.meta})
        else:
            self.handle = open(self.path, 'a', encoding='utf-8')
            for record in records:
                self._apply(record)
        if 'forward' not in self.done:
            self.resumed['forward'] = self.forward_next
        if 'backward' not in self.done and self.backward_next >= 0:
            self.resumed['backward'] = n - self.backward_next
        if 'cover' not in self.done:
            self.resumed['cover'] = self.cover_next - MIN_SEGMENT

    def _read(self):
        if not self.path or not os.path.exists(self.path):
            return None
        try:
            raw = open(self.path, 'rb').read()
        except OSError:
            return None
        lines = raw.split(b'\n')
        if lines and lines[-1] == b'':
            lines.pop()
        records = []
        valid = []
        for line in lines:
            try:
                record = json.loads(line)
            except Exception:
                break
            if record.get('kind') == 'meta':
                if (record.get('version') != CHECKPOINT_VERSION
                        or record.get('checksum') != self.meta['checksum']
                        or record.get('n') != self.meta['n']
                        or record.get('split_penalty') != self.meta['split_penalty']):
                    return None
            records.append(record)
            valid.append(line)
        # Drop a partly written tail so later appends stay parseable.
        with open(self.path, 'wb') as handle:
            for line in valid:
                handle.write(line + b'\n')
        return records

    def _apply(self, record):
        kind = record.get('kind')
        if kind == 'done':
            self.done.add(record.get('phase'))
            return
        start = int(record.get('start', 0))
        values = record.get('values') or []
        if kind == 'f':
            preds = record.get('pred') or []
            for offset, value in enumerate(values):
                index = start + offset
                if index <= self.meta['n']:
                    self.f[index] = np.inf if value is None else float(value)
                    if offset < len(preds):
                        self.pred[index] = int(preds[offset])
            self.forward_next = max(self.forward_next, start + len(values))
        elif kind == 'g':
            for offset, value in enumerate(values):
                index = start + offset
                if index <= self.meta['n']:
                    self.g[index] = np.inf if value is None else float(value)
            if values:
                self.backward_next = start
        elif kind == 'cover':
            for offset, amin in enumerate(record.get('amin') or []):
                amin = int(amin)
                if amin < 0:
                    continue
                b = start + offset
                # All tight segments with end b are nested around the same end,
                # so their union is simply (amin, b): cut positions amin+1..b-1.
                left = amin + 1
                right = b - 1
                if left <= right:
                    self.diff[left] += 1
                    self.diff[right + 1] -= 1
            self.cover_next = max(self.cover_next,
                                  start + len(record.get('amin') or []))

    def _write(self, record):
        # Keep in-memory state authoritative even when no checkpoint file is
        # configured; replaying a record is idempotent.
        self._apply(record)
        if not self.handle:
            return
        self.handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def save_forward(self, start, end):
        self._write({
            'kind': 'f', 'start': start,
            # Unreachable cells stay +inf; null keeps JSON standards-compliant.
            'values': [float(value) if np.isfinite(value) else None
                       for value in self.f[start:end]],
            'pred': [int(value) for value in self.pred[start:end]],
        })
        self.forward_next = end

    def save_backward(self, start, end):
        self._write({'kind': 'g', 'start': start,
                     'values': [float(value) if np.isfinite(value) else None
                                for value in self.g[start:end]]})
        self.backward_next = start

    def save_cover(self, start, end, amins):
        self._write({'kind': 'cover', 'start': start,
                     'amin': [int(value) for value in amins]})
        self.cover_next = end

    def mark_done(self, phase):
        self.done.add(phase)
        self._write({'kind': 'done', 'phase': phase})

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


def _report(callback, phase, done, total):
    if callback:
        callback(phase, done, total)


def segmented_fit(snapshot, penalty, checksum, checkpoint_path=None, on_progress=None):
    """Return the optimal segmentation of ``snapshot``; raises if none exists."""
    prep = _prepare(snapshot)
    n = prep['n']
    meta = {'n': n, 'checksum': checksum, 'split_penalty': penalty}
    checkpoint = _Checkpoint(checkpoint_path, meta)
    checkpoint.open()
    try:
        f, pred, g = checkpoint.f, checkpoint.pred, checkpoint.g

        # ---- forward DP: f[b] = cheapest cover of [0, b) ----
        # Penalty is charged once per cut, i.e. for every segment except the
        # first.  So f[n] = sum(RSS) + penalty * cuts.
        if 'forward' not in checkpoint.done:
            f[0] = 0.0
            start = max(checkpoint.forward_next, 1)
            block_start = start
            for b in range(start, n + 1):
                lo = max(0, b - MAX_SEGMENT)
                hi = b - MIN_SEGMENT
                if hi >= lo:
                    a = np.arange(lo, hi + 1)
                    rss, _ = _costs(prep, a, b)
                    candidate = f[a] + rss + np.where(a > 0, penalty, 0.0)
                    best = int(np.argmin(candidate))
                    if np.isfinite(candidate[best]):
                        f[b] = float(candidate[best])
                        pred[b] = int(a[best])
                if b - block_start + 1 >= _BLOCK:
                    checkpoint.save_forward(block_start, b + 1)
                    block_start = b + 1
                    _report(on_progress, 'forward', b + 1, n + 1)
            if block_start <= n:
                checkpoint.save_forward(block_start, n + 1)
            checkpoint.mark_done('forward')
            _report(on_progress, 'forward', n + 1, n + 1)
        if not np.isfinite(f[n]):
            raise ValueError('无法按每段 20 至 200 件完成分段')

        # ---- backward DP: g[b] = cheapest charged cover of [b, n) ----
        if 'backward' not in checkpoint.done:
            g[n] = 0.0
            top = n - MIN_SEGMENT
            begin = min(checkpoint.backward_next, top) if checkpoint.backward_next >= 0 else top
            block_hi = begin
            for b in range(begin, -1, -1):
                hi = min(n, b + MAX_SEGMENT)
                lo = b + MIN_SEGMENT
                if lo <= hi:
                    c = np.arange(lo, hi + 1)
                    rss, _ = _costs(prep, np.full_like(c, b), c)
                    candidate = rss + (penalty if b > 0 else 0.0) + g[c]
                    best = int(np.argmin(candidate))
                    if np.isfinite(candidate[best]):
                        g[b] = float(candidate[best])
                if block_hi - b + 1 >= _BLOCK:
                    checkpoint.save_backward(b, block_hi + 1)
                    block_hi = b - 1
                    _report(on_progress, 'backward', n - b, n + 1)
            if block_hi >= 0:
                checkpoint.save_backward(0, block_hi + 1)
            checkpoint.mark_done('backward')
            _report(on_progress, 'backward', n + 1, n + 1)

        # ---- tight segments: intervals some optimal path may span ----
        optimal = float(f[n])
        tol = _TOL_FACTOR * max(1.0, abs(optimal))
        if 'cover' not in checkpoint.done:
            begin = checkpoint.cover_next
            block_start = begin
            amins = []
            for b in range(begin, n + 1):
                lo = max(0, b - MAX_SEGMENT)
                hi = b - MIN_SEGMENT
                amin = -1
                if hi >= lo and np.isfinite(g[b]):
                    a = np.arange(lo, hi + 1)
                    rss, _ = _costs(prep, a, b)
                    charged = f[a] + rss + np.where(a > 0, penalty, 0.0) + g[b]
                    tight = np.abs(charged - optimal) <= tol
                    if tight.any():
                        amin = int(a[tight].min())
                amins.append(amin)
                if b - block_start + 1 >= _BLOCK:
                    checkpoint.save_cover(block_start, b + 1, amins)
                    amins = []
                    block_start = b + 1
                    _report(on_progress, 'verify', b + 1, n + 1)
            if block_start <= n:
                checkpoint.save_cover(block_start, n + 1, amins)
            checkpoint.mark_done('cover')
            _report(on_progress, 'verify', n + 1, n + 1)

        covered = np.cumsum(checkpoint.diff[:n + 1]) > 0
        mandatory = [p for p in range(1, n) if not covered[p]]

        # ---- deterministic representative optimal segmentation ----
        cuts = []
        b = n
        while b > 0:
            a = int(pred[b])
            if a < 0:
                raise ValueError('最优分段回溯失败')
            cuts.append((a, b))
            b = a
        cuts.reverse()
        if not cuts or cuts[0][0] != 0:
            raise ValueError('最优分段回溯不完整')

        return _result(prep, cuts, mandatory, optimal, penalty, tol,
                       checkpoint.resumed)
    finally:
        checkpoint.close()


def _result(prep, cuts, mandatory, optimal, penalty, tolerance, resumed):
    sequences = prep['sequences']
    n = prep['n']
    segments = []
    rows = []
    for index, (a, b) in enumerate(cuts, start=1):
        length = b - a
        rss_arr, linear_arr = _costs(prep, np.array([a]), np.array([b]))
        rss = float(rss_arr[0])
        linear = bool(linear_arr[0])
        sx = prep['px'][b] - prep['px'][a]
        sy = prep['py'][b] - prep['py'][a]
        sxx = prep['pxx'][b] - prep['pxx'][a]
        sxy = prep['pxy'][b] - prep['pxy'][a]
        sxx_c = sxx - sx * sx / length
        sxy_c = sxy - sx * sy / length
        xbar = prep['x_mean'] + sx / length
        ybar = prep['y_mean'] + sy / length
        if linear:
            slope = float(sxy_c / sxx_c)
            intercept = float(ybar - slope * xbar)
        else:
            slope = None
            intercept = float(ybar)
        segment = {
            'index': index,
            'start_sequence': int(sequences[a]),
            'end_sequence': int(sequences[b - 1]),
            'count': int(length),
            'model': 'linear' if linear else 'mean',
            'slope': slope,
            'intercept': intercept,
            'rss': rss,
        }
        segments.append(segment)
        rows.append([index, segment['start_sequence'], segment['end_sequence'], length,
                     '线性' if linear else '均值', slope, intercept, rss])

    cuts_count = len(segments) - 1
    rss_total = sum(segment['rss'] for segment in segments)
    target = rss_total + penalty * cuts_count
    mean_count = sum(segment['model'] == 'mean' for segment in segments)
    mandatory_rows = [[p, int(sequences[p - 1]), int(sequences[p])] for p in mandatory]
    metrics = {
        '独立试件数': n,
        '生产序号范围': f'{sequences[0]}–{sequences[-1]}',
        '分段数': len(segments),
        '切分数': cuts_count,
        '切分罚分': penalty,
        '残差平方和合计': rss_total,
        '罚分合计': penalty * cuts_count,
        '目标值(RSS+罚分)': target,
        '必切位置数': len(mandatory),
        '孔隙率恒定的分段数': mean_count,
    }
    if mandatory:
        marks = '、'.join(f'{sequences[p - 1]} | {sequences[p]}' for p in mandatory)
        shared = f'所有最优方案都必须在序号 {marks} 处切分。'
    else:
        shared = '没有所有最优方案共同要求的切分位置。'
    interpretation = (
        f'按生产序号排序后，{n} 件试件分为 {len(segments)} 段、切分 {cuts_count} 处；'
        f'每段以孔隙率线性拟合吸水率（孔隙率恒定段只拟合均值），'
        f'目标值 RSS+罚分={target:.6g}。{shared}'
        '展示的分段只是某一最优方案，必切位置才是不依赖具体方案共同边界。'
    )
    caveat = (
        '分段依据发起研究时冻结的生产序号、测值与切分罚分；计算期间修改测值只影响下一次研究。'
        '段数随罚分变化；不能据分段结果删改试件或推断因果。每段连续 20 至 200 件。'
    )
    return {
        'complete': True,
        'algorithm': '生产序号分段线性拟合',
        'metrics': metrics,
        'segments': segments,
        'mandatory_cuts': [
            {'position': p, 'after_sequence': after, 'before_sequence': before}
            for p, after, before in mandatory_rows
        ],
        'mandatory_cut_rows': mandatory_rows,
        'mandatory_columns': ['切分位置(第n件之后)', '前一生产序号', '后一生产序号'],
        'columns': ['段', '起始生产序号', '结束生产序号', '件数', '模型', '斜率', '截距', '残差平方和'],
        'rows': rows,
        'table_title': '最优分段与逐段拟合（代表解）',
        'interpretation': interpretation,
        'caveat': caveat,
        'resumed_cells': resumed,
        'tolerance': tolerance,
    }


def cleanup(path):
    """Remove a finished checkpoint; ignored when unset or already gone."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass
