"""误报相似走势检索的核心算法。

业务背景：一次误报发生后，班长要在逐分钟模型分数记录里找走势相似的其他片段。

处理流程与本模块对应关系：
  1. 归一化：每个片段各减去本段首分钟的分数，只比较走势形状，不比较绝对水平；
  2. 受限 DTW：动态时间规整求最小累计绝对差，规整路径上两个位置在各自片段内
     的偏移量之差不得超过 BAND（=2）分钟，即 Sakoe-Chiba 带约束；
  3. 候选筛选：在整条记录上滑动取与样本等长的窗口，距离不超过上限、且不与
     样本段相交者才是候选；
  4. 名额选择：候选间互不重叠、且不得与已复核区段相交，用带基数上限的加权
     区间调度 DP，在复核名额内先最大化片段数量，数量相同取总距离最小的一组。

片段在本模块中一律使用 0 基下标、闭区间 [start, end] 表示；分钟号与下标的
换算由 Web 层负责。
"""
from __future__ import annotations

INF = 10 ** 18

# 规整路径允许的最大偏移差（分钟）
BAND = 2
# 样本/片段长度与复核名额的取值范围
MIN_LEN, MAX_LEN = 8, 30
MIN_QUOTA, MAX_QUOTA = 1, 10


# ---------------------------------------------------------------- 归一化与 DTW

def normalize(seg):
    """减去本段首分钟的分数，返回只表达走势的新序列。"""
    base = seg[0]
    return [v - base for v in seg]


def dtw_distance(a, b, band=BAND):
    """受限动态时间规整距离。

    定义在两条等长序列上：D[i][j] 表示 a[:i+1] 与 b[:j+1] 规整对齐的最小累计
    绝对差，转移来自 (i-1,j)、(i,j-1)、(i-1,j-1) 三个前驱；仅当 |i-j| <= band
    的格点允许对齐（偏移量至多相差 band 分钟）。答案为 D[n-1][n-1]。
    band 取 >= n-1 时退化为无约束 DTW（测试对照用）。
    """
    n = len(a)
    if n != len(b):
        raise ValueError("DTW 仅比较等长序列")
    if n == 0:
        raise ValueError("空序列无法比较")
    D = [[INF] * n for _ in range(n)]
    D[0][0] = abs(a[0] - b[0])
    for i in range(n):
        for j in range(n):
            if i == 0 and j == 0:
                continue
            if abs(i - j) > band:
                continue
            if i > 0:
                up = D[i - 1][j]
                diag = D[i - 1][j - 1] if j > 0 else INF
            else:
                up = diag = INF
            left = D[i][j - 1] if j > 0 else INF
            prev = min(up, left, diag)
            if prev >= INF:
                continue
            D[i][j] = prev + abs(a[i] - b[j])
    return D[n - 1][n - 1]


# ---------------------------------------------------------------- 区间工具

def intersects(a, b, c, d):
    """闭区间 [a,b] 与 [c,d] 是否相交（共享端点算相交）。"""
    return not (b < c or d < a)


# ---------------------------------------------------------------- 候选检索

def find_candidates(scores, start, end, max_dist, band=BAND):
    """在 scores 上找出样本 [start,end] 的全部等长相似窗口。

    遍历每个等长滑动窗口（下标闭区间 [s, s+L-1]）：
      - 与样本段相交的窗口（含样本自身）一律排除；
      - 两段分别减去自身首分钟分数后做受限 DTW；
      - 距离 <= max_dist 才入选。
    返回 [{"start", "end", "distance"}, ...]，按下标升序；等长窗口的起点
    唯一，故 start 可作为片段在本次查询内的稳定标识。
    """
    if not (0 <= start <= end < len(scores)):
        raise ValueError("样本区间越界")
    length = end - start + 1
    sample = normalize(scores[start:end + 1])
    out = []
    for s in range(0, len(scores) - length + 1):
        e = s + length - 1
        if intersects(start, end, s, e):
            continue
        seg = normalize(scores[s:e + 1])
        d = dtw_distance(sample, seg, band)
        if d <= max_dist:
            out.append({"start": s, "end": e, "distance": int(d)})
    return out


# ---------------------------------------------------------------- 名额选择

def select_segments(candidates, blocked, quota):
    """从候选中挑选互不重叠的片段，至多 quota 个。

    约束：
      - 入选片段两两不相交（等长窗口，端点相邻允许：上一段 end < 下一段 start）；
      - 不得与 blocked 中任一已复核区段 [a,b] 相交；
      - 总数不超过复核名额 quota。
    目标：先最大化入选数量；数量相同取总距离最小；再相同按下标字典序确定，
    保证结果可复现。

    实现为带基数维的加权区间调度 DP：
      prev[i] = 终点严格小于候选 i 起点的最大候选下标；
      f[i][k] = 只看前 i 个候选、至多取 k 个时的最优 (数量, 总距离, 下标元组)。
    """
    cands = [c for c in candidates
             if not any(intersects(c["start"], c["end"], a, b)
                        for a, b in blocked)]
    n = len(cands)
    if n == 0 or quota <= 0:
        return []
    quota = min(quota, n)

    prev = []
    for i, c in enumerate(cands):
        p = -1
        for j in range(i - 1, -1, -1):
            if cands[j]["end"] < c["start"]:
                p = j
                break
        prev.append(p)

    def better(x, y):
        """数量多者优；数量相同总距离小者优；再相同下标元组字典序小者优。"""
        if x[0] != y[0]:
            return x if x[0] > y[0] else y
        if x[1] != y[1]:
            return x if x[1] < y[1] else y
        return x if x[2] <= y[2] else y

    # f[i][k]：仅用前 i 个候选（下标 0..i-1）、至多 k 个时的最优状态
    f = [[(0, 0, ())] * (quota + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        c = cands[i - 1]
        p = prev[i - 1]
        for k in range(quota + 1):
            best = f[i - 1][k]  # 不选候选 i-1
            if k >= 1:
                base = f[p + 1][k - 1]  # 选 i-1：只能搭配前 p+1 个候选
                take = (base[0] + 1, base[1] + c["distance"],
                        base[2] + (i - 1,))
                best = better(best, take)
            f[i][k] = best

    _, _, idxs = f[n][quota]
    return [cands[i] for i in idxs]
