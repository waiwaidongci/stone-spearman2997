"""Independent oracle for the segmented fit.

Deliberately shares no code with ``segmentation``: an O(n^2) memoised DP over
explicit least-squares fits, used to check optimal cost and the exact set of
mandatory cut positions (positions no optimal segment spans).
"""

import numpy as np


def segment_cost(x, y, a, b):
    xx, yy = x[a:b], y[a:b]
    if np.ptp(xx) == 0:
        return float(((yy - yy.mean()) ** 2).sum())
    X = np.column_stack([np.ones(b - a), xx])
    beta, *_ = np.linalg.lstsq(X, yy, rcond=None)
    resid = yy - X @ beta
    return float(resid @ resid)


def solve(snapshot, penalty, min_rows=20, max_rows=200):
    ordered = sorted(snapshot, key=lambda row: row['sequence'])
    x = np.array([r['values']['porosity'] for r in ordered], dtype=float)
    y = np.array([r['values']['absorption'] for r in ordered], dtype=float)
    n = len(x)
    cost = {}
    for a in range(n):
        for b in range(a + min_rows, min(n, a + max_rows) + 1):
            cost[(a, b)] = segment_cost(x, y, a, b)
    best = {n: 0.0}
    for a in range(n - 1, -1, -1):
        vals = [cost[(a, b)] + (penalty if a > 0 else 0.0) + best[b]
                for b in range(a + min_rows, min(n, a + max_rows) + 1) if b in best]
        if vals:
            best[a] = min(vals)
    optimal = best[0]
    tol = 1e-9 * max(1.0, abs(optimal))
    forward = {0: 0.0}
    for b in range(min_rows, n + 1):
        vals = [forward[a] + cost[(a, b)] + (penalty if a > 0 else 0.0)
                for a in range(max(0, b - max_rows), b - min_rows + 1) if a in forward]
        if vals:
            forward[b] = min(vals)
    spanned = set()
    for a in range(n + 1):
        for b in range(a + min_rows, min(n, a + max_rows) + 1):
            if a in forward and b in best and abs(
                    forward[a] + cost[(a, b)] + (penalty if a > 0 else 0.0) + best[b] - optimal) <= tol:
                spanned.update(range(a + 1, b))
    return optimal, [p for p in range(1, n) if p not in spanned]
