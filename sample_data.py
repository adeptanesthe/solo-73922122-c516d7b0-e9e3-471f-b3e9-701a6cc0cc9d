"""生成示例数据：720 分钟（12 小时）的逐分钟模型分数，含若干超阈值段。"""
import math
import random


def make_sample():
    rng = random.Random(20261124)
    minutes = list(range(1, 721))
    scores = []
    for m in minutes:
        base = 46 + int(8 * math.sin(m / 57.0)) + rng.randint(-6, 6)
        scores.append(max(0, min(100, base)))
    # 注入超阈值段（起始分钟, 持续, 峰值），每段只在首分钟告警
    bursts = [(60, 3, 88), (118, 2, 84), (175, 4, 90), (240, 2, 83),
              (301, 5, 91), (360, 3, 86), (420, 2, 82), (469, 4, 89),
              (535, 3, 85), (590, 2, 83), (648, 4, 90), (700, 2, 84)]
    for s, ln, pk in bursts:
        for k in range(ln):
            v = pk - k * 3 - rng.randint(0, 2)
            scores[s - 1 + k] = max(scores[s - 1 + k], v)
    return minutes, scores


def to_csv():
    minutes, scores = make_sample()
    lines = ["minute,score"]
    lines += [f"{m},{s}" for m, s in zip(minutes, scores)]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    sys.stdout.write(to_csv())
