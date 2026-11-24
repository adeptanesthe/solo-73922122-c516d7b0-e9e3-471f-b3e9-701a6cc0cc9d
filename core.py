"""卡纸预警提前量核定的核心算法。

问题：给定逐分钟模型分数产生的告警点集合 A、若干互不重叠的卡纸可能区间
[a_i, b_i]（每组卡纸时刻从各区间各取一分钟），以及提前量窗口 [L, U]（告警 a
可配对卡纸 t 当且仅当 L <= t - a <= U，一对一配对），求"任一组时刻组合的最大
配对数都 >= K"的最大整数 L。

不枚举全部时刻组合的原理（区间互不重叠是关键）：
  区间互不重叠 => 任一组合 t_1 < t_2 < ... < t_m => 各窗口 W_i = [t_i-U, t_i-L]
  的左、右端点均按下标递增。对这种"点 vs 有序区间"的二部图，由 König 定理：
      maxMatching(t) = min_{S ⊆ {1..m}} ( |S| + |A ∩ ⋃_{i∉S} W_i| )，
  即最小顶点覆盖 = "删去的卡纸数 + 剩余卡纸窗口并集覆盖的告警数"。
  又由端点有序性，并集覆盖数可沿下标分解：设上一个保留窗口右端点为 r_prev，
  则当前窗口 W_j 的新增覆盖 = |A ∩ [max(t_j-U, r_prev+1), t_j-L]|。
  于是 min_t maxMatching(t) 化为最短路 DP：按序处理每个卡纸，
    - 删去：代价 +1，状态（上一保留窗口右端点）不变；
    - 保留：选 t_j ∈ [a_j, b_j]（至多 10 种），代价 += 新增覆盖告警数，
      状态变为 t_j - L。
  状态数 O(m·|I|)，转移 O(|I|)，整体 O(m·|I|²)，无需枚举指数级时刻组合。
  窗口 [t-U, t-L] 随 L 增大而收缩 => minHits(L) 关于 L 单调不增 => 可二分。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

INF = 10 ** 18


def compute_alerts(minutes, scores, threshold):
    """每段连续达标（score >= threshold）只在首分钟产生一条告警，返回升序告警分钟列表。"""
    alerts = []
    prev_ok = False
    for m, s in zip(minutes, scores):
        ok = s >= threshold
        if ok and not prev_ok:
            alerts.append(int(m))
        prev_ok = ok
    return alerts


def _count(alerts, lo, hi):
    """有序告警列表中落在 [lo, hi] 内的条数。"""
    if lo > hi:
        return 0
    return bisect_right(alerts, hi) - bisect_left(alerts, lo)


def _dp(L, alerts, intervals, U, want_path=False):
    """最短路 DP：返回 min_t maxMatching(t)；want_path 时同时回推一组最坏组合。

    intervals 需已按 a_i 升序且互不重叠。状态 = 上一保留窗口右端点（None 表示尚无）。
    """
    dp = {None: 0}
    parents = []
    for a, b in intervals:
        new_dp = {}
        parent = {}
        for prev_r, cost in dp.items():
            # 选择一：删去该卡纸（顶点覆盖中为该卡纸支付 1）
            c = cost + 1
            if c < new_dp.get(prev_r, INF):
                new_dp[prev_r] = c
                parent[prev_r] = (prev_r, None)
            # 选择二：保留该卡纸，为其选时刻 t，支付新增覆盖的告警数
            for t in range(a, b + 1):
                r = t - L
                lo = t - U if prev_r is None else max(t - U, prev_r + 1)
                c = cost + _count(alerts, lo, r)
                if c < new_dp.get(r, INF):
                    new_dp[r] = c
                    parent[r] = (prev_r, t)
        dp = new_dp
        parents.append(parent)
    best = min(dp.values())
    if not want_path:
        return best
    state = min(dp, key=lambda s: (dp[s], str(s)))
    combo = [0] * len(intervals)
    for j in range(len(intervals) - 1, -1, -1):
        prev_state, t = parents[j][state]
        combo[j] = t if t is not None else intervals[j][0]
        state = prev_state
    return best, combo


def min_hits(L, alerts, intervals, U):
    """任一组卡纸时刻组合的最大配对数的最小值。"""
    return _dp(L, sorted(alerts), sorted(intervals), U)


def max_matching(combo, alerts, L, U):
    """给定组合 combo（各卡纸时刻），求与告警的一对一最大配对。

    点 vs 区间的凸二部图：按窗口右端点升序，依次分配 >= 左端点的最小可用告警点，
    该贪心对该类图最优。返回 [(卡纸序号, 卡纸时刻, 告警时刻, 提前量), ...]。
    """
    pts = sorted(int(a) for a in alerts)
    pairs = []
    for i in sorted(range(len(combo)), key=lambda i: (combo[i] - L, combo[i])):
        lo, hi = combo[i] - U, combo[i] - L
        k = bisect_left(pts, lo)
        if k < len(pts) and pts[k] <= hi:
            a = pts.pop(k)
            pairs.append((i, combo[i], a, combo[i] - a))
    return pairs


def counterexample(L, alerts, intervals, U):
    """构造一组在提前量窗口 [L, U] 下配对数等于 minHits(L) 的卡纸时刻组合及其最佳配对。"""
    intervals = sorted(intervals)
    _, combo = _dp(L, sorted(alerts), intervals, U, want_path=True)
    pairs = max_matching(combo, sorted(alerts), L, U)
    return combo, pairs


def solve(alerts, intervals, U, K):
    """主求解：返回结论字典。

    status = 'ok'        ：可承诺的最大 L（result['L']），min_hits 为该 L 下的保证命中数；
                           若 L < U，附 L+1 档的反例 counter。
    status = 'infeasible'：连 L=1 都不达标，附 L=1 档反例 counter。
    counter = {'L', 'combo', 'pairs', 'hits', 'unmatched'}
    intervals 会按 a_i 升序整理后返回在 result['intervals'] 中。
    """
    intervals = sorted((int(a), int(b)) for a, b in intervals)
    alerts = sorted(int(a) for a in alerts)
    m = len(intervals)
    out = {"m": m, "U": U, "K": K, "n_alerts": len(alerts), "intervals": intervals}

    def make_counter(Lc):
        combo, pairs = counterexample(Lc, alerts, intervals, U)
        by_jam = {p[0]: p for p in pairs}
        rows = []
        for i, (a, b) in enumerate(intervals):
            p = by_jam.get(i)
            rows.append({"jam": i, "a": a, "b": b, "t": combo[i],
                         "alert": p[2] if p else None,
                         "lead": p[3] if p else None})
        return {
            "L": Lc,
            "combo": list(combo),
            "pairs": pairs,
            "rows": rows,
            "hits": len(pairs),
            "unmatched": [i for i in range(m) if i not in by_jam],
        }

    if not alerts:
        out.update(status="infeasible", reason="no_alerts", counter=make_counter(1))
        return out

    # L 超过 max(b_i)-min(alerts) 时任何告警都够不到任何卡纸，minHits 必为 0
    reach = max(b for _, b in intervals) - alerts[0]
    hi = min(U, max(reach, 1))

    mh1 = min_hits(1, alerts, intervals, U)
    if mh1 < K:
        out.update(status="infeasible", reason="L1", min_hits_1=mh1,
                   counter=make_counter(1))
        return out

    # 二分最大的可行 L（minHits 关于 L 单调不增）
    lo, hi2 = 1, hi
    while lo < hi2:
        mid = (lo + hi2 + 1) // 2
        if min_hits(mid, alerts, intervals, U) >= K:
            lo = mid
        else:
            hi2 = mid - 1
    Lstar = lo
    mh = min_hits(Lstar, alerts, intervals, U)
    out.update(status="ok", L=Lstar, min_hits=mh, reach_capped=hi < U)
    if Lstar < U:
        out["counter"] = make_counter(Lstar + 1)
    return out
