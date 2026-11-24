"""卡纸预警提前量核定回放网页（Flask）。

用法见 README.md。核心计算在 core.py，本文件负责：
  - 上传/校验逐分钟模型分数 CSV（≤3000 行、分钟号连续、整数分数）
  - 录入告警阈值、有效提前量上限 U、至少命中次数 K、卡纸可能区间（≤20 段，
    每段≤10 个分钟号、互不重叠）
  - 调用 core.solve 核定最大可承诺提前量 L，并给出反例组合与最佳配对
  - 区间/参数/数据一经更正，旧结论立即撤下（历史区可见）
"""
import csv
import io
import json
import os
import re
import time
import uuid
from datetime import datetime

from flask import (Flask, Response, flash, redirect, render_template,
                   request, session, url_for)

import core
from sample_data import to_csv as sample_csv

MAX_ROWS = 3000          # CSV 最多行数
MAX_JAMS = 20            # 卡纸区间最多段数
MAX_SPAN = 9             # 每段区间至多含 10 个分钟号 => b-a <= 9
MAX_HISTORY = 20

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# 会话级存储（单进程内存型工具；重启即清空）
STORE = {}


# ---------------------------------------------------------------- 工具

def get_store():
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
    return STORE.setdefault(sid, {"data": None, "current": None,
                                  "history": [], "seq": 0})


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def withdraw_current(store, reason):
    """更正数据/区间/参数时，撤下当前结论，移入历史。"""
    cur = store.get("current")
    if cur:
        cur["status"] = "已撤下"
        cur["withdraw_reason"] = reason
        store["history"].insert(0, cur)
        store["history"] = store["history"][:MAX_HISTORY]
        store["current"] = None


def _to_int(s):
    s = s.strip()
    if not re.fullmatch(r"[+-]?\d+", s):
        raise ValueError(f"“{s}”不是整数")
    return int(s)


def parse_csv_text(text):
    """解析并校验 CSV：两列（分钟号, 整数分数），可含表头；分钟号须连续（步长 1）。"""
    rows = []
    for raw in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in raw]
        if any(cells):
            rows.append(cells)
    if not rows:
        raise ValueError("CSV 为空")
    start = 0
    try:
        _to_int(rows[0][0])
        _to_int(rows[0][1])
    except (ValueError, IndexError):
        start = 1  # 表头行
    minutes, scores = [], []
    for no, r in enumerate(rows[start:], start=start + 1):
        if len(r) < 2:
            raise ValueError(f"第 {no} 行不足两列")
        try:
            m, s = _to_int(r[0]), _to_int(r[1])
        except ValueError as e:
            raise ValueError(f"第 {no} 行：{e}")
        minutes.append(m)
        scores.append(s)
    if not minutes:
        raise ValueError("没有数据行")
    if len(minutes) > MAX_ROWS:
        raise ValueError(f"数据 {len(minutes)} 行，超过上限 {MAX_ROWS} 行")
    for i in range(1, len(minutes)):
        if minutes[i] != minutes[i - 1] + 1:
            raise ValueError(
                f"分钟号不连续：第 {i} 行 {minutes[i - 1]}，第 {i + 1} 行 {minutes[i]}")
    return minutes, scores


def parse_intervals(text):
    """解析卡纸可能区间：每行一段，形如“125-130”或“125 130”，单分钟写“125”。"""
    intervals = []
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = [p for p in re.split(r"[\s,，]+", line) if p]
        try:
            if len(parts) == 1:
                sub = [p for p in re.split(r"[-~～—–:：]", parts[0]) if p]
                if len(sub) == 1:
                    a = b = _to_int(sub[0])
                elif len(sub) == 2:
                    a, b = _to_int(sub[0]), _to_int(sub[1])
                else:
                    raise ValueError("格式无法识别")
            elif len(parts) == 2:
                a, b = _to_int(parts[0]), _to_int(parts[1])
            else:
                raise ValueError("格式无法识别")
        except ValueError as e:
            raise ValueError(f"第 {ln} 行：{e}")
        if a > b:
            raise ValueError(f"第 {ln} 行：区间起点 {a} 大于终点 {b}")
        if b - a > MAX_SPAN:
            raise ValueError(f"第 {ln} 行：区间 [{a},{b}] 含 {b - a + 1} 个分钟号，"
                             f"超过 {MAX_SPAN + 1} 个")
        intervals.append((a, b))
    if not intervals:
        raise ValueError("请至少录入 1 次卡纸")
    if len(intervals) > MAX_JAMS:
        raise ValueError(f"卡纸次数 {len(intervals)} 超过上限 {MAX_JAMS}")
    intervals.sort()
    for i in range(1, len(intervals)):
        if intervals[i][0] <= intervals[i - 1][1]:
            raise ValueError(
                f"区间 [{intervals[i - 1][0]},{intervals[i - 1][1]}] 与 "
                f"[{intervals[i][0]},{intervals[i][1]}] 重叠")
    return intervals


def summarize(entry):
    r = entry["result"]
    if r["status"] == "ok":
        s = f"可承诺 L={r['L']}（任意组合至少命中 {r['min_hits']} 次 ≥ K={r['K']}）"
        if "counter" in r:
            s += f"；L={r['counter']['L']} 档反例命中 {r['counter']['hits']} 次"
    else:
        s = f"无法承诺（L=1 档反例命中 {r['counter']['hits']} 次 < K={r['K']}）"
    return s


# ---------------------------------------------------------------- 图表

def build_svg(minutes, scores, threshold=None, alerts=(), intervals=(),
              combo=None, pairs=None):
    """服务端生成回放图：分数折线、阈值线、告警点、卡纸区间带、反例时刻与配对弧。"""
    W, H = 1160, 340
    ml, mr, mt, mb = 52, 18, 34, 38
    pw, ph = W - ml - mr, H - mt - mb
    xs = list(minutes)
    if intervals:
        xs += [a for a, _ in intervals] + [b for _, b in intervals]
    if combo:
        xs += list(combo)
    x0, x1 = min(xs), max(xs)
    if x1 <= x0:
        x1 = x0 + 1
    ys = list(scores)
    if threshold is not None:
        ys.append(threshold)
    y0, y1 = min(ys), max(ys)
    if y1 <= y0:
        y1 = y0 + 1
    pad = (y1 - y0) * 0.08
    y0, y1 = y0 - pad, y1 + pad

    def X(x):
        return ml + (x - x0) * pw / (x1 - x0)

    def Y(y):
        return mt + ph - (y - y0) * ph / (y1 - y0)

    P = [f'<svg viewBox="0 0 {W} {H}" width="100%" '
         f'xmlns="http://www.w3.org/2000/svg" font-size="11">']
    # 网格与坐标
    for i in range(6):
        gy = mt + ph * i / 5
        gv = y1 - (y1 - y0) * i / 5
        P.append(f'<line x1="{ml}" y1="{gy:.1f}" x2="{ml + pw}" y2="{gy:.1f}" stroke="#e3e6ea"/>')
        P.append(f'<text x="{ml - 5}" y="{gy + 3:.1f}" text-anchor="end" fill="#888">{gv:.0f}</text>')
    for i in range(9):
        gx = ml + pw * i / 8
        gv = x0 + (x1 - x0) * i / 8
        P.append(f'<line x1="{gx:.1f}" y1="{mt}" x2="{gx:.1f}" y2="{mt + ph}" stroke="#f0f2f4"/>')
        P.append(f'<text x="{gx:.1f}" y="{mt + ph + 14}" text-anchor="middle" fill="#888">{gv:.0f}</text>')
    P.append(f'<rect x="{ml}" y="{mt}" width="{pw}" height="{ph}" fill="none" stroke="#c9ced4"/>')
    # 卡纸区间带
    for i, (a, b) in enumerate(intervals or []):
        xa, xb = X(a), max(X(b), X(a) + 2)
        P.append(f'<rect x="{xa:.1f}" y="{mt}" width="{xb - xa:.1f}" height="{ph}" '
                 f'fill="#ff7f0e" opacity="0.10"/>')
        P.append(f'<line x1="{xa:.1f}" y1="{mt}" x2="{xa:.1f}" y2="{mt + ph}" stroke="#ff7f0e" stroke-dasharray="3,3" opacity="0.7"/>')
        P.append(f'<line x1="{xb:.1f}" y1="{mt}" x2="{xb:.1f}" y2="{mt + ph}" stroke="#ff7f0e" stroke-dasharray="3,3" opacity="0.7"/>')
        P.append(f'<text x="{(xa + xb) / 2:.1f}" y="{mt + 12}" text-anchor="middle" fill="#d2691e" font-weight="bold">卡{i + 1}</text>')
    # 阈值线
    if threshold is not None:
        P.append(f'<line x1="{ml}" y1="{Y(threshold):.1f}" x2="{ml + pw}" y2="{Y(threshold):.1f}" '
                 f'stroke="#d62728" stroke-dasharray="6,4" stroke-width="1.2"/>')
        P.append(f'<text x="{ml + pw - 4}" y="{Y(threshold) - 4:.1f}" text-anchor="end" fill="#d62728">阈值 {threshold}</text>')
    # 分数折线
    pts = " ".join(f"{X(m):.1f},{Y(s):.1f}" for m, s in zip(minutes, scores))
    P.append(f'<polyline points="{pts}" fill="none" stroke="#1f77b4" stroke-width="1.3"/>')
    # 告警点
    aset = set(alerts)
    smap = dict(zip(minutes, scores))
    for a in alerts:
        P.append(f'<circle cx="{X(a):.1f}" cy="{Y(smap[a]):.1f}" r="4" fill="#d62728" stroke="#fff" stroke-width="1"/>')
    # 反例配对弧与时刻
    if pairs:
        yb = mt + ph
        for k, (i, t, a, lead) in enumerate(pairs):
            x1_, x2_ = X(a), X(t)
            lift = 40 + 14 * (k % 4)
            P.append(f'<path d="M {x1_:.1f} {yb} Q {(x1_ + x2_) / 2:.1f} {yb - 2 * lift} {x2_:.1f} {yb}" '
                     f'fill="none" stroke="#2ca02c" stroke-width="1.4" opacity="0.65"/>')
            P.append(f'<circle cx="{x1_:.1f}" cy="{yb}" r="3.5" fill="#2ca02c"/>')
    if combo:
        for t in combo:
            P.append(f'<line x1="{X(t):.1f}" y1="{mt}" x2="{X(t):.1f}" y2="{mt + ph}" stroke="#9467bd" stroke-width="1.4" stroke-dasharray="5,3"/>')
            P.append(f'<circle cx="{X(t):.1f}" cy="{mt + ph}" r="3.5" fill="#9467bd"/>')
    # 回放游标
    P.append(f'<line id="cursor" x1="0" y1="{mt}" x2="0" y2="{mt + ph}" stroke="#333" stroke-width="1" style="display:none"/>')
    # 图例
    lx = ml + 8
    for label, color in [("分数", "#1f77b4"), ("告警", "#d62728"), ("卡纸区间", "#ff7f0e"),
                         ("反例时刻", "#9467bd"), ("配对", "#2ca02c")]:
        P.append(f'<rect x="{lx}" y="6" width="10" height="10" fill="{color}" opacity="0.85"/>')
        P.append(f'<text x="{lx + 14}" y="15" fill="#555">{label}</text>')
        lx += 14 + 9 * len(label) + 22
    P.append("</svg>")
    geom = {"x0": x0, "x1": x1, "ml": ml, "pw": pw}
    return "".join(P), geom


# ---------------------------------------------------------------- 路由

@app.route("/")
def index():
    store = get_store()
    data = store.get("data")
    current = store.get("current")
    svg = geom = replay = None
    if data:
        p = current["params"] if current else {}
        r = current["result"] if current else {}
        counter = r.get("counter") or {}
        svg, geom = build_svg(
            data["minutes"], data["scores"],
            threshold=p.get("threshold"),
            alerts=r.get("alerts", []),
            intervals=p.get("intervals", []),
            combo=counter.get("combo"),
            pairs=counter.get("pairs"))
        replay = json.dumps({
            "minutes": data["minutes"], "scores": data["scores"],
            "alerts": r.get("alerts", []), "geom": geom})
    form = current["params"] if current else {"threshold": 80, "U": 90, "K": 5,
                                              "intervals": []}
    return render_template("index.html", data=data, current=current,
                           history=store["history"], svg=svg, replay=replay,
                           form=form, summarize=summarize,
                           max_jams=MAX_JAMS, max_span=MAX_SPAN + 1,
                           max_rows=MAX_ROWS)


@app.route("/upload", methods=["POST"])
def upload():
    store = get_store()
    f = request.files.get("file")
    if not f or not f.filename:
        flash("请选择要上传的 CSV 文件。")
        return redirect(url_for("index"))
    raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    try:
        minutes, scores = parse_csv_text(text)
    except ValueError as e:
        flash(f"CSV 未通过校验：{e}")
        return redirect(url_for("index"))
    withdraw_current(store, "数据已更正（重新上传 CSV）")
    store["data"] = {"minutes": minutes, "scores": scores, "name": f.filename,
                     "uploaded_at": now_str()}
    flash(f"已加载 {f.filename}：{len(minutes)} 行，分钟 {minutes[0]} ~ {minutes[-1]}，"
          f"分数 {min(scores)} ~ {max(scores)}。")
    return redirect(url_for("index"))


@app.route("/analyze", methods=["POST"])
def analyze():
    store = get_store()
    if not store.get("data"):
        flash("请先上传 CSV 数据。")
        return redirect(url_for("index"))
    def _num(name, cast, text):
        try:
            return cast(text)
        except (TypeError, ValueError):
            raise ValueError(f"{name} 填写有误：{text!r}")

    try:
        threshold = _num("告警阈值", float, request.form.get("threshold", "").strip())
        U = _num("有效提前量上限 U", int, request.form.get("U", "").strip())
        K = _num("至少命中次数 K", int, request.form.get("K", "").strip())
        if U < 1:
            raise ValueError("有效提前量上限 U 需为 ≥ 1 的整数")
        if K < 1:
            raise ValueError("至少命中次数 K 需为 ≥ 1 的整数")
        intervals = parse_intervals(request.form.get("intervals", ""))
    except (ValueError, AttributeError) as e:
        flash(f"参数未通过校验：{e}")
        return redirect(url_for("index"))

    data = store["data"]
    alerts = core.compute_alerts(data["minutes"], data["scores"], threshold)
    t0 = time.perf_counter()
    res = core.solve(alerts, intervals, U, K)
    res["alerts"] = alerts
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    withdraw_current(store, "参数或卡纸区间已更正")
    store["seq"] += 1
    store["current"] = {
        "id": store["seq"], "time": now_str(), "status": "有效",
        "params": {"threshold": threshold, "U": U, "K": K, "intervals": intervals},
        "data_name": data["name"], "data_rows": len(data["minutes"]),
        "result": res,
    }
    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    store = get_store()
    store.update({"data": None, "current": None, "history": [], "seq": 0})
    flash("已清空当前会话的数据与结论。")
    return redirect(url_for("index"))


@app.route("/sample.csv")
def sample():
    return Response(sample_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample_data.csv"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
