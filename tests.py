"""核心算法自测：与暴力枚举全部时刻组合的结果交叉验证。

运行：python3 tests.py
"""
import itertools
import random

from core import (compute_alerts, counterexample, max_matching, min_hits,
                  solve)


def brute_max_matching(combo, alerts, L, U):
    """增广路求二分图最大匹配（暴力，供对照）。"""
    adj = []
    for t in combo:
        lo, hi = t - U, t - L
        adj.append([a for a in alerts if lo <= a <= hi])
    match_of_alert = {}

    def aug(i, seen):
        for a in adj[i]:
            if a in seen:
                continue
            seen.add(a)
            if a not in match_of_alert or aug(match_of_alert[a], seen):
                match_of_alert[a] = i
                return True
        return False

    cnt = 0
    for i in range(len(combo)):
        if aug(i, set()):
            cnt += 1
    return cnt


def brute_min_hits(L, alerts, intervals, U):
    """枚举全部时刻组合，取最大配对数的最小值（暴力，供对照）。"""
    best = None
    for combo in itertools.product(*[range(a, b + 1) for a, b in intervals]):
        v = brute_max_matching(list(combo), alerts, L, U)
        best = v if best is None else min(best, v)
    return best


def test_compute_alerts():
    minutes = [10, 11, 12, 13, 14, 15, 16, 17]
    scores = [5, 9, 9, 8, 2, 8, 7, 1]
    assert compute_alerts(minutes, scores, 8) == [11, 15]
    assert compute_alerts(minutes, scores, 10) == []
    assert compute_alerts(minutes, scores, 1) == [10]  # 整段只在首分钟告警
    print("ok  compute_alerts")


def test_max_matching_vs_brute(rng, trials=300):
    for _ in range(trials):
        n = rng.randint(0, 12)
        alerts = sorted(rng.sample(range(0, 40), n))
        m = rng.randint(1, 5)
        U = rng.randint(1, 8)
        L = rng.randint(1, U)
        combo = [rng.randint(0, 40) for _ in range(m)]
        g = len(max_matching(combo, alerts, L, U))
        b = brute_max_matching(combo, alerts, L, U)
        assert g == b, (combo, alerts, L, U, g, b)
    print("ok  max_matching == brute_max_matching")


def test_min_hits_vs_brute(rng, trials=400):
    for _ in range(trials):
        n = rng.randint(0, 10)
        alerts = sorted(rng.sample(range(0, 30), n))
        m = rng.randint(1, 4)
        U = rng.randint(1, 7)
        L = rng.randint(1, U)
        # 互不重叠的小区间
        intervals = []
        cur = rng.randint(0, 3)
        for _ in range(m):
            a = cur
            b = a + rng.randint(0, 3)
            intervals.append((a, b))
            cur = b + rng.randint(1, 4)
        got = min_hits(L, alerts, intervals, U)
        want = brute_min_hits(L, alerts, intervals, U)
        assert got == want, (L, alerts, intervals, U, got, want)
        # 反例组合的配对数应恰好等于 min_hits
        combo, pairs = counterexample(L, alerts, intervals, U)
        assert len(pairs) == want, (L, alerts, intervals, U, combo, pairs, want)
        # 反例组合必须取自各区间
        for (a, b), t in zip(intervals, combo):
            assert a <= t <= b
    print("ok  min_hits/counterexample == brute force (400 组随机)")


def test_solve_vs_linear(rng, trials=120):
    for _ in range(trials):
        n = rng.randint(0, 10)
        alerts = sorted(rng.sample(range(0, 30), n))
        m = rng.randint(1, 4)
        U = rng.randint(1, 8)
        K = rng.randint(1, m + 2)
        intervals = []
        cur = rng.randint(0, 3)
        for _ in range(m):
            a = cur
            b = a + rng.randint(0, 3)
            intervals.append((a, b))
            cur = b + rng.randint(1, 4)
        res = solve(alerts, intervals, U, K)
        # 线性扫描对照
        mh = {L: brute_min_hits(L, alerts, intervals, U) for L in range(1, U + 1)}
        feasible = [L for L in range(1, U + 1) if mh[L] >= K]
        if not feasible:
            assert res["status"] == "infeasible", (res, mh, K)
            c = res["counter"]
            assert c["L"] == 1 and c["hits"] == mh[1] < K
        else:
            Lstar = max(feasible)
            assert res["status"] == "ok" and res["L"] == Lstar, (res, mh, K)
            assert res["min_hits"] == mh[Lstar]
            if Lstar < U:
                c = res["counter"]
                assert c["L"] == Lstar + 1 and c["hits"] == mh[Lstar + 1] < K
            else:
                assert "counter" not in res
    print("ok  solve == 线性扫描暴力解 (120 组随机)")


def test_monotonicity(rng, trials=60):
    for _ in range(trials):
        n = rng.randint(0, 12)
        alerts = sorted(rng.sample(range(0, 40), n))
        m = rng.randint(1, 5)
        U = rng.randint(1, 10)
        intervals = []
        cur = rng.randint(0, 3)
        for _ in range(m):
            a = cur
            b = a + rng.randint(0, 3)
            intervals.append((a, b))
            cur = b + rng.randint(1, 4)
        seq = [min_hits(L, alerts, intervals, U) for L in range(1, U + 1)]
        assert all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1)), seq
    print("ok  min_hits 关于 L 单调不增")


def test_edge_cases():
    # 无告警 -> 无法承诺
    res = solve([], [(10, 12)], 5, 1)
    assert res["status"] == "infeasible" and res["counter"]["hits"] == 0
    # K 大于卡纸数 -> 无法承诺（告警 6/7/8 能覆盖 [10,12] 的任一时刻，但最多配对 1 次）
    res = solve([6, 7, 8], [(10, 12)], 5, 2)
    assert res["status"] == "infeasible" and res["counter"]["hits"] == 1
    # 告警远早于所有卡纸（提前量永远不够）-> 无法承诺
    res = solve([1, 2], [(100, 105)], 5, 1)
    assert res["status"] == "infeasible"
    # 告警提前量为 3/4/5 分钟 -> 最大可承诺 L=5，L=6 档反例配对 0 次
    res = solve([95, 96, 97], [(100, 100)], 10, 1)
    assert res["status"] == "ok" and res["L"] == 5
    assert res["counter"]["L"] == 6 and res["counter"]["hits"] == 0
    # 告警恰好都在窗口内 -> L 可取到 U
    res = solve([90], [(100, 100)], 10, 1)
    assert res["status"] == "ok" and res["L"] == 10 and "counter" not in res
    # U 大于可达提前量时被截断，且给出 L+1 反例
    res = solve([90], [(100, 100)], 50, 1)
    assert res["status"] == "ok" and res["L"] == 10
    assert res["counter"]["L"] == 11 and res["counter"]["hits"] == 0
    print("ok  边界情形")


def test_web_app():
    """用 Flask 测试客户端走通：上传 -> 核定 -> 更正撤下 -> 无法承诺 -> 校验拦截。"""
    import io as _io

    import app as web
    from sample_data import to_csv

    web.app.config.update(TESTING=True)
    c = web.app.test_client()
    assert c.get("/").status_code == 200
    assert len(c.get("/sample.csv").data.decode().strip().splitlines()) == 721

    # 上传示例数据
    r = c.post("/upload", data={"file": (_io.BytesIO(to_csv().encode()), "s.csv")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "已加载" in r.get_data(as_text=True)

    # 演示参数：可承诺 L=60，L=61 档反例命中 4 次
    iv = "125-130\n250-254\n370-374\n480-484\n600-604\n705-709"
    r = c.post("/analyze", data={"threshold": "80", "U": "90", "K": "5",
                                 "intervals": iv}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "可承诺：L = 60 分钟" in html
    assert "任意卡纸时刻组合至少命中 5 次" in html
    assert "合计命中 4 次（共 6 次卡纸）" in html

    # 更正区间：旧结论撤下并出现在历史
    r = c.post("/analyze", data={"threshold": "80", "U": "90", "K": "5",
                                 "intervals": "126-130\n250-254\n370-374\n480-484\n600-604\n705-709"},
               follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "已撤下" in html and "参数或卡纸区间已更正" in html

    # K=7 超过卡纸数：无法承诺
    r = c.post("/analyze", data={"threshold": "80", "U": "90", "K": "7",
                                 "intervals": iv}, follow_redirects=True)
    assert "无法承诺" in r.get_data(as_text=True)

    # 非法输入被拦截
    r = c.post("/upload", data={"file": (_io.BytesIO(b"minute,score\n1,50\n3,60\n"), "b.csv")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "分钟号不连续" in r.get_data(as_text=True)
    r = c.post("/analyze", data={"threshold": "80", "U": "90", "K": "5",
                                 "intervals": "125-130\n128-135"},
               follow_redirects=True)
    assert "重叠" in r.get_data(as_text=True)
    print("ok  网页端冒烟测试")


def main():
    test_compute_alerts()
    rng = random.Random(20261124)
    test_max_matching_vs_brute(rng)
    test_min_hits_vs_brute(rng)
    test_solve_vs_linear(rng)
    test_monotonicity(rng)
    test_edge_cases()
    test_web_app()
    print("\n全部自测通过。")


if __name__ == "__main__":
    main()
