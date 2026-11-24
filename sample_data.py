"""生成示例数据：720 分钟（12 小时）的逐分钟模型分数。

背景为带噪声的正弦基线；另外植入一组“误报形态”片段（先升后降的 12 分钟
走势），各片段基线水平、噪声幅度和时间轴微移不同，供 DTW 相似检索演示：

- 样本原型位于分钟 40~51；
- 分钟 90/150/210/270/330/400/540/610/680 等处为相似走势（含带噪、时间微移版本）；
- 分钟 338~349 与 330~341 重叠，用于验证“名单内片段互不重叠”；
- 分钟 470~481 是反向走势（先降后升），距离上限较小时不应入选。
"""
import math
import random

# 12 分钟误报形态（相对首分钟的增量），长度在页面允许的 8~30 分钟范围内
PATTERN = [0, 3, 7, 12, 15, 12, 8, 5, 3, 1, 0, -1]
# 时间轴微移版本：峰值整体晚 1 分钟（靠 DTW 的带内偏移吸收）
PATTERN_SHIFTED = [0, 0, 3, 7, 12, 15, 12, 8, 5, 3, 1, 0]

# (起始分钟, 基线分, 噪声幅度, 形态)
PLANTS = [
    (40, 44, 1, PATTERN),           # 样本原型
    (90, 55, 1, PATTERN),
    (150, 38, 1, PATTERN),
    (210, 47, 1, PATTERN_SHIFTED),
    (270, 42, 2, PATTERN),
    (330, 50, 1, PATTERN),
    (338, 46, 1, PATTERN),          # 与 330~341 相交
    (400, 40, 1, PATTERN_SHIFTED),
    (470, 52, 1, None),             # 反向走势负例
    (540, 45, 2, PATTERN),
    (610, 49, 1, PATTERN),
    (680, 43, 1, PATTERN),
]


def make_sample():
    rng = random.Random(20261124)
    minutes = list(range(1, 721))
    scores = []
    for m in minutes:
        base = 46 + int(8 * math.sin(m / 57.0)) + rng.randint(-6, 6)
        scores.append(max(0, min(100, base)))

    for start, level, noise, shape in PLANTS:
        s = start - 1
        if shape is None:
            shape = [-v for v in PATTERN]  # 反向：先降后升
        for k, delta in enumerate(shape):
            v = level + delta + rng.randint(-noise, noise)
            scores[s + k] = max(0, min(100, v))
    return minutes, scores


def to_csv():
    minutes, scores = make_sample()
    lines = ["minute,score"]
    lines += [f"{m},{s}" for m, s in zip(minutes, scores)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    sys.stdout.write(to_csv())
