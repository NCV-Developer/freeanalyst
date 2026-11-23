"""三档验收报告 —— 文件级门槛 + 字段级置信 + 分层汇总。

    /usr/local/bin/python3.12 -m bench.accept materials/corpus

## 报什么

    文件级拒答：哪几份材料不适用，理由是什么（扫描件/金融业）
    字段级三档：高置信 / 低置信（原因+页码）/ 拒答
    分层指标：每层的高置信覆盖率、高置信正确率、拒答率

## 两档口径

    A 档·有外部对照源：核对一致才给高置信（100% 是平凡的，看的是覆盖率）
    B 档·无外部对照源：**定档不看真值，评估才看** —— 私密材料的真实水平
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

from financials import confidence as cf
from financials import reconcile
from financials.from_pdf import load_pdf_statements
from . import layers


def _bump(stat, tags, mode, level, match, has_truth):
    key = f"{mode}:{level}"
    stat[("总体", "全部")][key] += 1
    for dim, label in tags.items():
        stat[(dim, label)][key] += 1
    if level == cf.HIGH:
        # **没有真值的格子谈不上对错**，单列「未验证」。
        k2 = f"{mode}:未验证" if not has_truth else (f"{mode}:对" if match else f"{mode}:错")
        stat[("总体", "全部")][k2] += 1
        for dim, label in tags.items():
            stat[(dim, label)][k2] += 1


def run(root: Path, year: int = 2025) -> None:
    files = sorted(root.rglob("*.pdf"))
    print(f"三档验收：{len(files)} 份材料\n")

    stat = collections.defaultdict(collections.Counter)
    rejected_files: list[tuple[str, str]] = []
    lows: list[tuple[str, str, str]] = []

    # **缓存**：每份材料的判定结果落盘，改统计口径时不用重跑 40 分钟。
    import json
    cache_path = Path("out/accept_cache.jsonl")
    done: dict[str, dict] = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                done[rec["file"]] = rec
            except Exception:                        # noqa: BLE001
                pass
    cache_fp = cache_path.open("a", encoding="utf-8")

    for idx, p in enumerate(files, 1):
        if p.name in done:                            # 有缓存直接回放
            rec = done[p.name]
            if rec.get("rejected"):
                rejected_files.append((p.name, rec["rejected"]))
            for e in rec.get("events", []):
                _bump(stat, e["tags"], e["mode"], e["level"], e["match"], e["truth"])
            continue
        print(f"[{idx}/{len(files)}] {p.name}", flush=True)   # 进度：中断也能看到跑到哪
        # 代码必须和覆盖矩阵**同口径：从文件名取**。
        # 北交所目录名是旧代码 430047、文件名是新代码 920047，
        # 用目录名会对错公司，取不到真值还被算成「答错」。
        from . import coverage as _cov
        _mk, code = _cov.infer(p)
        if not code:
            code = p.parent.name
        try:
            st = load_pdf_statements(p)
        except Exception as exc:                       # noqa: BLE001
            why = f"载入失败：{type(exc).__name__}"
            rejected_files.append((p.name, why))
            cache_fp.write(json.dumps({"file": p.name, "rejected": why}, ensure_ascii=False) + "\n")
            cache_fp.flush()
            continue

        ok, reasons = cf.file_gate(st)
        tags = layers.classify(code, st)
        if not ok:
            why = "；".join(reasons)
            rejected_files.append((p.name, why))
            cache_fp.write(json.dumps({"file": p.name, "rejected": why}, ensure_ascii=False) + "\n")
            cache_fp.flush()
            continue

        rep = reconcile.compare(st, code, year=year)
        verdict = {i.name: i for i in rep.items}

        events = []
        for item, fld, kind in reconcile.ITEMS:
            blk = getattr(st, kind, None)
            ours = blk.fields.get(fld) if blk else None
            if ours is not None:
                ours *= reconcile.unit_mult(st, kind)
            v = verdict.get(item)
            match = v is not None and v.verdict.name in ("MATCH", "ROUNDING")
            has_truth = v is not None and v.theirs is not None
            for mode in ("A", "B"):
                verified = None if mode == "B" else (
                    True if (has_truth and match) else (False if has_truth else None))
                level, why = cf.item_level(st, item, ours, verified=verified)
                events.append({"tags": tags, "mode": mode, "level": level,
                               "match": match, "truth": has_truth})
                if level == cf.LOW and mode == "A":
                    lows.append((code, item, why[:60]))
        for e in events:
            _bump(stat, e["tags"], e["mode"], e["level"], e["match"], e["truth"])
        cache_fp.write(json.dumps({"file": p.name, "events": events}, ensure_ascii=False) + "\n")
        cache_fp.flush()

    for (dim, label), c in sorted(stat.items()):
        if dim != "总体" and label in ("非金融", "文字版"):
            continue
        print(f"\n### {dim} {label}")
        for mode in ("A", "B"):
            hi, lo, rj = c[f"{mode}:{cf.HIGH}"], c[f"{mode}:{cf.LOW}"], c[f"{mode}:{cf.REJECT}"]
            tot = hi + lo + rj
            if not tot:
                continue
            good, bad = c[f"{mode}:对"], c[f"{mode}:错"]
            unver = c[f"{mode}:未验证"]
            acc = good / (good + bad) if (good + bad) else 0.0
            print(f"  {mode} 档  高置信 {hi:>3}（{hi/tot:>5.0%}）  低置信 {lo:>3}  拒答 {rj:>3}"
                  f"   高置信正确率 {good}/{good+bad} = {acc:.1%}（另有 {unver} 格无对照）")

    print("\n### 文件级拒答（不适用自动解析）")
    for name, why in rejected_files:
        print(f"  {name[:44]:<46}{why[:60]}")
    print("\n### 低置信字段（交人工，前 15 条）")
    for code, item, why in lows[:15]:
        print(f"  {code} {item:<12}{why}")


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("materials/corpus")
    run(root)
