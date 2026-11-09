"""Causal rolling short-separation regression in the OD domain (OLS / Ridge).

Running sums over a window, rebuilt exactly every ``window`` pushes to bound
floating-point drift. One instance per (long channel, wavelength).
Corrected = y - sum(beta_i * (x_i - mean_x_i)) (long mean preserved), as in the legacy tool.
"""

from __future__ import annotations

import math
from collections import deque


def solve(matrix: list[list[float]], vec: list[float], tol: float = 1e-12) -> list[float]:
    n = len(vec)
    aug = [list(row) + [vec[i]] for i, row in enumerate(matrix)]
    for p in range(n):
        best = max(range(p, n), key=lambda r: abs(aug[r][p]))
        if abs(aug[best][p]) < tol:
            raise ValueError("regression matrix is singular")
        aug[p], aug[best] = aug[best], aug[p]
        div = aug[p][p]
        aug[p] = [v / div for v in aug[p]]
        for r in range(n):
            if r != p:
                f = aug[r][p]
                aug[r] = [a - f * b for a, b in zip(aug[r], aug[p])]
    return [row[n] for row in aug]


def _solve_stats(sxx, sxy, n, method, lam):
    p = len(sxy)
    if method == "ridge":
        scales = []
        for i in range(p):
            rms = math.sqrt(max(sxx[i][i], 0.0) / n)
            if rms <= 1e-15:
                raise ValueError("a short-separation regressor has near-zero variance")
            scales.append(rms)
        m = [[sxx[i][j] / (scales[i] * scales[j]) + (lam if i == j else 0.0) for j in range(p)]
             for i in range(p)]
        bs = solve(m, [sxy[i] / scales[i] for i in range(p)])
        return [bs[i] / scales[i] for i in range(p)]
    return solve([row[:] for row in sxx], list(sxy))


def fit_beta(rows: list, method: str, ridge_lambda: float) -> tuple[list[float], list[float]]:
    """Direct batch fit on rows [(y, (x1, ...)), ...]. Returns (beta, x_means)."""
    n, p = len(rows), len(rows[0][1])
    ym = sum(y for y, _ in rows) / n
    xm = [sum(x[j] for _, x in rows) / n for j in range(p)]
    sxx = [[sum((x[i] - xm[i]) * (x[j] - xm[j]) for _, x in rows) for j in range(p)] for i in range(p)]
    sxy = [sum((x[i] - xm[i]) * (y - ym) for y, x in rows) for i in range(p)]
    return _solve_stats(sxx, sxy, n, method, ridge_lambda), xm


class RollingRegressor:
    def __init__(self, n_regressors: int, window: int, min_samples: int, method: str,
                 ridge_lambda: float) -> None:
        if method == "auto":
            method = "ridge" if n_regressors == 2 else "ols"
        self.p, self.window, self.min_samples = n_regressors, window, min_samples
        self.method, self.lam = method, ridge_lambda
        self.reset()

    def reset(self) -> None:
        self._rows: deque = deque()
        self.last_beta: list[float] | None = None
        self._rebuild()

    def _rebuild(self) -> None:
        p = self.p
        self.n = len(self._rows)
        self.sx = [sum(r[1][i] for r in self._rows) for i in range(p)]
        self.sy = sum(r[0] for r in self._rows)
        self.sxx = [[sum(r[1][i] * r[1][j] for r in self._rows) for j in range(p)] for i in range(p)]
        self.sxy = [sum(r[1][i] * r[0] for r in self._rows) for i in range(p)]
        self._since = 0

    def _add(self, y, x, sign):
        self.n += sign
        self.sy += sign * y
        for i in range(self.p):
            self.sx[i] += sign * x[i]
            self.sxy[i] += sign * x[i] * y
            for j in range(self.p):
                self.sxx[i][j] += sign * x[i] * x[j]

    def push(self, y: float, x: tuple) -> float | None:
        """Add one valid row; return the corrected y, or None while warming up / singular."""
        self._rows.append((y, tuple(x)))
        self._add(y, x, +1)
        if len(self._rows) > self.window:
            oy, ox = self._rows.popleft()
            self._add(oy, ox, -1)
        self._since += 1
        if self._since >= self.window:
            self._rebuild()
        if self.n < self.min_samples:
            return None
        n, p = self.n, self.p
        xm = [self.sx[i] / n for i in range(p)]
        ym = self.sy / n
        sxx = [[self.sxx[i][j] - n * xm[i] * xm[j] for j in range(p)] for i in range(p)]
        sxy = [self.sxy[i] - n * xm[i] * ym for i in range(p)]
        try:
            beta = _solve_stats(sxx, sxy, n, self.method, self.lam)
        except ValueError:
            return None
        self.last_beta = beta
        return y - sum(beta[i] * (x[i] - xm[i]) for i in range(p))
