"""误报相似走势检索 · 历史回放网页（Flask）。

用法见 README.md。核心计算在 core.py，本文件负责：
  - 上传/校验逐分钟模型分数 CSV（≤3000 行、分钟号连续、整数分数）
  - 录入样本起止分钟（连续 8~30 分钟）、距离上限、复核名额（1~10）
  - 调用 core.find_candidates / core.select_segments 得到候选与复核名单
  - 把名单中的片段标为已复核后，用原查询在全部候选上重选；重选失败
    （重选后没有任何可选片段）则回滚，保留原名单和原有标记
  - 成功换 CSV 或清空回放后清除名单与标记；旧列表提交复核时要求重新检索
"""
import csv
import io
import os
import re
import time
import uuid
from datetime import datetime

from flask import (Flask, Response, flash, redirect, render_template,
                   request, session, url_for)

import core
from sample_data import to_csv as sample_csv

MAX_ROWS = 3000  # CSV 最多行数

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
    return STORE.setdefault(sid, {"data": None, "query": None,
                                  "reviewed": [], "seq": 0})


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def minute_to_index(data, minute):
    """分钟号 -> 0 基下标；不存在则报错。"""
    idx = minute - data["minutes"][0]
    if idx < 0 or idx >= len(data["minutes"]):
        raise ValueError(f"分钟号 {minute} 超出数据范围 "
                         f"{data['minutes'][0]}~{data['minutes'][-1]}")
    return idx


def run_selection(store):
    """用 store['query'] 的参数从全部候选重选，返回新名单（含分钟号与距离）。"""
    q = store["query"]
    blocked = [tuple(x) for x in store["reviewed"]]
    picks = core.select_segments(q["candidates"], blocked, q["params"]["quota"])
    return [{
        "start_min": store["data"]["minutes"][p["start"]],
        "end_min": store["data"]["minutes"][p["end"]],
        "start": p["start"], "end": p["end"],
        "distance": p["distance"],
    } for p in picks]


# ---------------------------------------------------------------- 图表

def build_svg(data, query=None, selection=None, reviewed_min=()):
    """服务端生成回放图：分数折线、样本带、候选带、已复核带与回放游标。"""
    minutes, scores = data["minutes"], data["scores"]
    W, H = 1160, 340
    ml, mr, mt, mb = 52, 18, 34, 38
    pw, ph = W - ml - mr, H - mt - mb
    x0, x1 = minutes[0], minutes[-1]
    if x1 <= x0:
        x1 = x0 + 1
    y0, y1 = min(scores), max(scores)
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

    def band(a, b, fill, opacity, label, ink):
        xa, xb = X(a), max(X(b), X(a) + 2)
        P.append(f'<rect x="{xa:.1f}" y="{mt}" width="{xb - xa:.1f}" height="{ph}" '
                 f'fill="{fill}" opacity="{opacity}"/>')
        P.append(f'<line x1="{xa:.1f}" y1="{mt}" x2="{xa:.1f}" y2="{mt + ph}" '
                 f'stroke="{fill}" stroke-dasharray="3,3" opacity="0.9"/>')
        P.append(f'<line x1="{xb:.1f}" y1="{mt}" x2="{xb:.1f}" y2="{mt + ph}" '
                 f'stroke="{fill}" stroke-dasharray="3,3" opacity="0.9"/>')
        P.append(f'<text x="{(xa + xb) / 2:.1f}" y="{mt + 12}" text-anchor="middle" '
                 f'fill="{ink}" font-weight="bold">{label}</text>')

    # 已复核区段（最先画，样本/候选带可叠在其上）
    for i, (a, b) in enumerate(reviewed_min or ()):
        band(a, b, "#8250df", 0.12, f"复核{i + 1}", "#6f3dc0")
    if query:
        # 全部候选（淡色）与入选名单（深色描边）
        picked = {(c["start"], c["end"]) for c in (selection or ())}
        for c in query["candidates"]:
            a = minutes[c["start"]]
            b = minutes[c["end"]]
            if (c["start"], c["end"]) in picked:
                continue
            band(a, b, "#1f77b4", 0.07, "", "#1f6feb")
        for i, c in enumerate(selection or ()):
            band(c["start_min"], c["end_min"], "#2da44e", 0.16,
                 f"名单{i + 1}", "#1a7f37")
        band(query["sample_start_min"], query["sample_end_min"],
             "#d62728", 0.10, "样本", "#cf222e")

    pts = " ".join(f"{X(m):.1f},{Y(s):.1f}" for m, s in zip(minutes, scores))
    P.append(f'<polyline points="{pts}" fill="none" stroke="#1f77b4" stroke-width="1.3"/>')
    P.append(f'<line id="cursor" x1="0" y1="{mt}" x2="0" y2="{mt + ph}" '
             f'stroke="#333" stroke-width="1" style="display:none"/>')
    lx = ml + 8
    for label, color in [("分数", "#1f77b4"), ("样本", "#d62728"),
                         ("候选", "#1f77b4"), ("名单", "#2da44e"),
                         ("已复核", "#8250df")]:
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
    query = store.get("query")
    svg = geom = replay = None
    if data:
        reviewed_min = [(data["minutes"][a], data["minutes"][b])
                        for a, b in store["reviewed"]]
        svg, geom = build_svg(data, query, query.get("selection") if query else None,
                              reviewed_min)
        replay = {"minutes": data["minutes"], "scores": data["scores"], "geom": geom}
    form = query["params"] if query else {"start_min": "", "end_min": "",
                                          "max_dist": "", "quota": 5}
    return render_template("index.html", data=data, query=query,
                           reviewed=[(data["minutes"][a], data["minutes"][b])
                                     for a, b in store["reviewed"]] if data else [],
                           svg=svg, replay=replay, form=form,
                           min_len=core.MIN_LEN, max_len=core.MAX_LEN,
                           min_quota=core.MIN_QUOTA, max_quota=core.MAX_QUOTA,
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
    # 成功换 CSV：清掉名单、候选与已复核标记
    store.update({"data": {"minutes": minutes, "scores": scores, "name": f.filename,
                           "uploaded_at": now_str()},
                  "query": None, "reviewed": [], "seq": 0})
    flash(f"已加载 {f.filename}：{len(minutes)} 行，分钟 {minutes[0]} ~ {minutes[-1]}，"
          f"分数 {min(scores)} ~ {max(scores)}。旧名单与复核标记已清空。")
    return redirect(url_for("index"))


@app.route("/search", methods=["POST"])
def search():
    store = get_store()
    if not store.get("data"):
        flash("请先上传 CSV 数据。")
        return redirect(url_for("index"))
    data = store["data"]
    try:
        start_min = _to_int(request.form.get("start_min", ""))
        end_min = _to_int(request.form.get("end_min", ""))
        max_dist = _to_int(request.form.get("max_dist", ""))
        quota = _to_int(request.form.get("quota", ""))
        s = minute_to_index(data, start_min)
        e = minute_to_index(data, end_min)
        if s > e:
            raise ValueError(f"样本起点 {start_min} 晚于终点 {end_min}")
        length = e - s + 1
        if not (core.MIN_LEN <= length <= core.MAX_LEN):
            raise ValueError(f"样本长度需为 {core.MIN_LEN}~{core.MAX_LEN} 分钟，"
                             f"当前为 {length} 分钟")
        if max_dist < 0:
            raise ValueError("距离上限需为 ≥ 0 的整数")
        if not (core.MIN_QUOTA <= quota <= core.MAX_QUOTA):
            raise ValueError(f"复核名额需为 {core.MIN_QUOTA}~{core.MAX_QUOTA} 的整数")
    except ValueError as exc:
        flash(f"参数未通过校验：{exc}")
        return redirect(url_for("index"))

    t0 = time.perf_counter()
    candidates = core.find_candidates(data["scores"], s, e, max_dist, core.BAND)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)
    store["seq"] += 1
    token = f"q{store['seq']}-{uuid.uuid4().hex[:8]}"
    store["query"] = {
        "token": token,
        "time": now_str(),
        "params": {"start_min": start_min, "end_min": end_min,
                   "max_dist": max_dist, "quota": quota},
        "sample_start_min": start_min, "sample_end_min": end_min,
        "sample_start": s, "sample_end": e, "length": length,
        "candidates": candidates, "elapsed_ms": elapsed,
        "selection": [],
    }
    store["query"]["selection"] = run_selection(store)
    q = store["query"]
    if q["selection"]:
        total = sum(c["distance"] for c in q["selection"])
        flash(f"检索完成：候选 {len(candidates)} 段，名单入选 {len(q['selection'])} 段"
              f"（名额 {quota}），总距离 {total}，DTW 耗时 {elapsed}ms。")
    else:
        flash(f"检索完成：候选 {len(candidates)} 段，但没有可入选的片段"
              f"（距离上限 {max_dist} 或已复核区段限制）。名单为空。")
    return redirect(url_for("index"))


@app.route("/review", methods=["POST"])
def review():
    store = get_store()
    data = store.get("data")
    q = store.get("query")
    if not data or not q:
        flash("请先上传 CSV 并执行检索。")
        return redirect(url_for("index"))
    # 旧列表提交（换 CSV/清空后又用旧表单提交）：要求重新检索
    if request.form.get("token") != q["token"]:
        flash("该名单已失效（数据已更换或回放已清空），请用当前参数重新检索后再提交复核。")
        return redirect(url_for("index"))
    try:
        start = _to_int(request.form.get("start", ""))
        end = _to_int(request.form.get("end", ""))
    except ValueError as exc:
        flash(f"复核提交未通过校验：{exc}")
        return redirect(url_for("index"))
    if not any(c["start"] == start and c["end"] == end for c in q["selection"]):
        flash("要复核的片段不在当前名单中，可能已被重选替换，请刷新页面核对后再操作。")
        return redirect(url_for("index"))

    old_selection = q["selection"]
    old_reviewed = list(store["reviewed"])
    if (start, end) not in store["reviewed"]:
        store["reviewed"].append((start, end))
        store["reviewed"].sort()
    new_selection = run_selection(store)

    if not new_selection:
        # 重选失败：回滚标记与名单，保留原列表和原有标记
        store["reviewed"] = [tuple(x) for x in old_reviewed]
        q["selection"] = old_selection
        a = data["minutes"][start]
        b = data["minutes"][end]
        flash(f"重选失败：把 {a}~{b} 标为已复核后，原查询在全部候选中已选不出任何片段，"
              f"已保留原名单和原有标记，该次复核未生效。")
        return redirect(url_for("index"))

    q["selection"] = new_selection
    a = data["minutes"][start]
    b = data["minutes"][end]
    total = sum(c["distance"] for c in new_selection)
    flash(f"已将 {a}~{b} 标为已复核，并按原查询从全部 {len(q['candidates'])} 个候选重选："
          f"新名单 {len(new_selection)} 段，总距离 {total}。")
    return redirect(url_for("index"))


@app.route("/reset", methods=["POST"])
def reset():
    store = get_store()
    # 清空回放：数据、列表与标记一并清掉
    store.update({"data": None, "query": None, "reviewed": [], "seq": 0})
    flash("已清空回放：数据、候选名单与复核标记全部清除。")
    return redirect(url_for("index"))


@app.route("/sample.csv")
def sample():
    return Response(sample_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sample_data.csv"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
