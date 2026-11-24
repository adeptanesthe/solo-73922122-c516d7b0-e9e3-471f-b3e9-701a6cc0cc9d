"""核心算法与网页端自测。

运行：python3 tests.py

对照方式：
  - DTW：受限结果与同实现的“无约束大带宽”结果、以及独立朴素实现交叉验证；
  - 名额选择：带基数的区间调度 DP 与 itertools 枚举全部子集的结果对照；
  - 网页：Flask 测试客户端走通 上传 -> 检索 -> 复核重选 -> 失败回滚 ->
    旧 token 拦截 -> 换 CSV 清空 全流程。
"""
import itertools
import random

import core


# ---------------------------------------------------------------- 独立朴素实现

def naive_dtw(a, b, band):
    """独立的朴素受限 DTW：用 INF 矩阵直接按定义计算，不共享 core 的循环写法。"""
    n = len(a)
    inf = float("inf")
    d = [[inf] * n for _ in range(n)]
    d[0][0] = abs(a[0] - b[0])
    for i in range(n):
        for j in range(n):
            if i == j == 0 or abs(i - j) > band:
                continue
            preds = []
            if i > 0 and abs(i - 1 - j) <= band:
                preds.append(d[i - 1][j])
            if j > 0 and abs(i - (j - 1)) <= band:
                preds.append(d[i][j - 1])
            if i > 0 and j > 0 and abs(i - 1 - (j - 1)) <= band:
                preds.append(d[i - 1][j - 1])
            if all(p == inf for p in preds):
                continue
            d[i][j] = min(preds) + abs(a[i] - b[j])
    return d[n - 1][n - 1]


def brute_select(candidates, blocked, quota):
    """枚举全部子集，按 数量优先、总距离最小、下标字典序 选最优。"""
    best = None
    cands = [c for c in candidates
             if not any(core.intersects(c["start"], c["end"], a, b)
                        for a, b in blocked)]
    for r in range(min(quota, len(cands)) + 1):
        for combo in itertools.combinations(range(len(cands)), r):
            ok = True
            for x, y in zip(combo, combo[1:]):
                if core.intersects(cands[x]["start"], cands[x]["end"],
                                   cands[y]["start"], cands[y]["end"]):
                    ok = False
                    break
            if not ok:
                continue
            total = sum(cands[i]["distance"] for i in combo)
            key = (len(combo), -total, tuple(-i for i in combo))
            if best is None or key > best[0]:
                best = (key, [cands[i] for i in combo])
    return best[1] if best else []


# ---------------------------------------------------------------- DTW 测试

def test_dtw_basic():
    # 相同序列距离为 0；归一化后“整体平移但走势相同”的距离也为 0
    a = [3, 5, 9, 8, 2, 4, 6, 1]
    b = [13, 15, 19, 18, 12, 14, 16, 11]
    assert core.dtw_distance(a, a) == 0
    assert core.dtw_distance(core.normalize(a), core.normalize(b)) == 0
    # 与朴素实现、无约束实现一致
    rng = random.Random(20261124)
    for n in range(1, 13):
        for _ in range(50):
            x = [rng.randint(-10, 10) for _ in range(n)]
            y = [rng.randint(-10, 10) for _ in range(n)]
            got = core.dtw_distance(x, y, core.BAND)
            assert got == naive_dtw(x, y, core.BAND)
            free = core.dtw_distance(x, y, n - 1)
            assert free == naive_dtw(x, y, n - 1)
            assert got >= free  # 加约束只会让最优距离变大或相等
    print("ok  dtw_distance：归一化平移不变、与朴素实现一致、约束距离 >= 无约束距离")


def test_dtw_band_effect():
    # 峰值分别在两端：无约束可把峰值错开到不同偏移对齐；带宽 2 时被迫错位对齐，
    # 累计绝对差显著变大
    a = [0, 10, 0, 0, 0, 0, 0, 0, 0, 0]
    b = [0, 0, 0, 0, 0, 0, 0, 0, 10, 0]
    banded = core.dtw_distance(a, b, 2)
    free = core.dtw_distance(a, b, 9)
    assert banded > free
    # 带宽 2 时偏移差不允许超过 2：构造仅在偏移差 4 才能零代价对齐的序列
    x = [0, 5, 5, 5, 5, 5, 5, 5]
    y = [0, 0, 0, 0, 5, 5, 5, 5]
    assert core.dtw_distance(x, y, 2) > core.dtw_distance(x, y, 7)
    print(f"ok  带宽约束生效：峰值两端例 banded={banded} > free={free}")


# ---------------------------------------------------------------- 候选与选择测试

def test_find_candidates(rng):
    n = 120
    scores = [rng.randint(0, 60) for _ in range(n)]
    s, e = 10, 21  # 12 分钟样本
    for limit in (0, 5, 15, 40):
        cands = core.find_candidates(scores, s, e, limit)
        starts = {c["start"] for c in cands}
        # 样本自身及与之相交的窗口全部排除
        for c in cands:
            assert not core.intersects(s, e, c["start"], c["end"])
            assert c["end"] - c["start"] + 1 == e - s + 1
            assert c["distance"] <= limit
        # 与逐窗口独立复算一致
        expect = set()
        sample = core.normalize(scores[s:e + 1])
        for w in range(n - (e - s)):
            we = w + (e - s)
            if core.intersects(s, e, w, we):
                continue
            d = core.dtw_distance(sample, core.normalize(scores[w:we + 1]), core.BAND)
            if d <= limit:
                expect.add(w)
        assert starts == expect
    print("ok  find_candidates：等长、不相交、距离达标，与独立复算一致")


def test_select_vs_brute(rng, trials=400):
    for _ in range(trials):
        n = rng.randint(1, 14)
        length = rng.randint(3, 6)
        used, cands = set(), []
        for i in range(n):
            s = rng.randint(0, 60)
            c = {"start": s, "end": s + length - 1,
                 "distance": rng.randint(0, 20)}
            cands.append(c)
        # 去重，模拟等长滑动窗口（起点唯一）
        if s in used:
            continue
        used.add(s)
        cands.sort(key=lambda c: c["start"])
        nb = rng.randint(0, 3)
        blocked = []
        cur = rng.randint(0, 5)
        for _ in range(nb):
            a = cur
            b = a + rng.randint(0, 4)
            blocked.append((a, b))
            cur = b + rng.randint(0, 5)
        quota = rng.randint(1, 10)
        got = core.select_segments(cands, blocked, quota)
        want = brute_select(cands, blocked, quota)
        assert [(c["start"], c["distance"]) for c in got] == \
               [(c["start"], c["distance"]) for c in want], \
            (cands, blocked, quota, got, want)
        # 结果必须互不重叠且不碰 blocked
        for i, c in enumerate(got):
            assert all(not core.intersects(c["start"], c["end"], a, b)
                       for a, b in blocked)
            for c2 in got[i + 1:]:
                assert not core.intersects(c["start"], c["end"],
                                           c2["start"], c2["end"])
        assert len(got) <= quota
    print("ok  select_segments 与枚举全部子集一致（400 组随机）")


def test_select_objective_order():
    # 数量优先：即便单段距离更小，也应选更多段
    cands = [
        {"start": 0, "end": 2, "distance": 1},
        {"start": 3, "end": 5, "distance": 1},
        {"start": 6, "end": 8, "distance": 1},
    ]
    sel = core.select_segments(cands, [], 10)
    assert len(sel) == 3 and sum(c["distance"] for c in sel) == 3
    # 数量相同取总距离最小：重叠对中选距离小的
    cands = [
        {"start": 0, "end": 3, "distance": 9},
        {"start": 2, "end": 5, "distance": 2},   # 与上者重叠，距离更小
        {"start": 6, "end": 9, "distance": 1},
    ]
    sel = core.select_segments(cands, [], 10)
    assert [c["start"] for c in sel] == [2, 6]
    # 名额截断
    sel = core.select_segments(
        [{"start": i * 4, "end": i * 4 + 2, "distance": 1} for i in range(6)],
        [], 2)
    assert len(sel) == 2
    # blocked 生效
    sel = core.select_segments(
        [{"start": 0, "end": 2, "distance": 1},
         {"start": 3, "end": 5, "distance": 1}], [(3, 5)], 10)
    assert [c["start"] for c in sel] == [0]
    print("ok  选择目标：数量优先、同数量总距离最小、名额与 blocked 生效")


# ---------------------------------------------------------------- 网页端冒烟

def test_web_app():
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

    # 非法参数被拦截
    r = c.post("/search", data={"start_min": "40", "end_min": "44",
                                "max_dist": "20", "quota": "5"},
               follow_redirects=True)
    assert "8~30 分钟" in r.get_data(as_text=True)
    r = c.post("/search", data={"start_min": "40", "end_min": "51",
                                "max_dist": "20", "quota": "11"},
               follow_redirects=True)
    assert "复核名额" in r.get_data(as_text=True)

    # 正式检索：样本 40~51、上限 20、名额 10 -> 9 段互不重叠名单
    r = c.post("/search", data={"start_min": "40", "end_min": "51",
                                "max_dist": "20", "quota": "10"},
               follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "候选 14 段" in html and "名单入选 9 段" in html
    # 从 STORE 直接核对名单互不重叠
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    q = st["query"]
    token = q["token"]
    sel = q["selection"]
    assert len(sel) == 9
    for i, x in enumerate(sel):
        for y in sel[i + 1:]:
            assert not core.intersects(x["start"], x["end"], y["start"], y["end"])
    assert sum(x["distance"] for x in sel) == 108
    first = sel[0]

    # 复核第一段：用原查询从全部候选重选，名单少 1 段、标记 +1
    r = c.post("/review", data={"token": token, "start": first["start"],
                                "end": first["end"]}, follow_redirects=True)
    assert "标为已复核" in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert (first["start"], first["end"]) in st["reviewed"]
    assert all(not (x["start"] == first["start"] and x["end"] == first["end"])
               for x in st["query"]["selection"])
    assert len(st["query"]["selection"]) == 8

    # 旧 token 提交：要求重新检索，状态不变
    r = c.post("/review", data={"token": "stale-token",
                                "start": first["start"], "end": first["end"]},
               follow_redirects=True)
    assert "重新检索" in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert len(st["reviewed"]) == 1

    # 重选失败回滚：上限 8 时只有 2 段候选，连续复核到选不出时必须保留原名单与标记
    c.post("/search", data={"start_min": "40", "end_min": "51",
                            "max_dist": "8", "quota": "10"},
           follow_redirects=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    q2 = st["query"]
    assert len(q2["candidates"]) == 2 and len(q2["selection"]) == 2
    # 先复核第一段 -> 只剩另一段
    a0 = q2["selection"][0]
    c.post("/review", data={"token": q2["token"], "start": a0["start"],
                            "end": a0["end"]}, follow_redirects=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert len(st["query"]["selection"]) == 1
    # 再复核最后一段 -> 重选为空，回滚：名单与标记恢复到本次操作前
    a1 = st["query"]["selection"][0]
    before_rev = [tuple(x) for x in st["reviewed"]]
    before_sel = [dict(x) for x in st["query"]["selection"]]
    r = c.post("/review", data={"token": q2["token"], "start": a1["start"],
                                "end": a1["end"]}, follow_redirects=True)
    assert "重选失败" in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert st["reviewed"] == before_rev
    assert st["query"]["selection"] == before_sel
    # 回滚后仍是上限 8 查询的 1 段名单，而不是更早的上限 20 名单
    assert [x["start"] for x in st["query"]["selection"]] == \
           [x["start"] for x in before_sel]
    # 回滚后重复提交同一段：仍失败、仍回滚，且不触发类型错误（回归保护）
    r = c.post("/review", data={"token": q2["token"], "start": a1["start"],
                                "end": a1["end"]}, follow_redirects=True)
    assert r.status_code == 200 and "重选失败" in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert st["reviewed"] == before_rev
    assert st["query"]["selection"] == before_sel

    # 换 CSV：名单与标记清空
    r = c.post("/upload", data={"file": (_io.BytesIO(to_csv().encode()), "s2.csv")},
               content_type="multipart/form-data", follow_redirects=True)
    assert "旧名单与复核标记已清空" in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert st["query"] is None and st["reviewed"] == []

    # 清空回放：全部清除
    r = c.post("/reset", follow_redirects=True)
    assert "清空回放" in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert st["data"] is None and st["query"] is None and st["reviewed"] == []

    # 无数据时检索被拦截
    r = c.post("/search", data={"start_min": "40", "end_min": "51",
                                "max_dist": "20", "quota": "10"},
               follow_redirects=True)
    assert "请先上传" in r.get_data(as_text=True)

    # CSV 校验：分钟号不连续，上传失败不会覆盖当前数据
    c.post("/upload", data={"file": (_io.BytesIO(to_csv().encode()), "good.csv")},
           content_type="multipart/form-data", follow_redirects=True)
    r = c.post(
        "/upload",
        data={"file": (_io.BytesIO(b"minute,score\n1,50\n3,60\n"), "bad.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    assert "分钟号不连续" in r.get_data(as_text=True)
    with c.session_transaction() as sess:
        st = web.STORE[sess["sid"]]
    assert st["data"] is not None and st["data"]["name"] == "good.csv"
    print("ok  网页端冒烟测试（检索/复核重选/失败回滚/旧 token/换 CSV/清空）")


def main():
    test_dtw_basic()
    test_dtw_band_effect()
    rng = random.Random(20261124)
    test_find_candidates(rng)
    test_select_vs_brute(rng)
    test_select_objective_order()
    test_web_app()
    print("\n全部自测通过。")


if __name__ == "__main__":
    main()
