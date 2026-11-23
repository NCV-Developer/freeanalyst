"""每条映射规则**实际服务几份材料 / 哪个市场** —— 用来判断规则是不是过拟合。

## 为什么要有它

这个项目的规则是「某份材料踩到 → 加一条」长出来的。长到 653 条之后，
**看代码分不出哪条是承重墙、哪条是给港股准备的、哪条谁都没要** ✗

加新规则时也看不出「我在补真缺口，还是在堆」✗

## 判据（跑一次就知道分布）

    服务 ≥3 份材料        → 真在泛化
    服务 1 份             → 过拟合候选（只为那一份存在）
    英文写法、零触发        → 服务 IFRS / 美股材料（合理，不是死规则）
    繁体写法、零触发        → 服务港股材料（合理）
    简体、零触发           → **真待查**（可能是老准则科目或常用简称）

## 一条经验教训（别重犯）

第一版把「被更长写法覆盖」判反了 —— 以为短的冗余、可删。
**真相相反**：标签正好是「固定资产」时，「固定资产净额」匹配不上，
删短的会丢整行。**同 field 内长的才冗余；短的删不得。**

所以这个工具**只报告，不删任何东西**。

## 用法

    python3 bench/rule_coverage.py [--labels 缓存.json]

`--labels` 指一份 `{文件名: [行标签…]}` 的 JSON（可用 `measure_parse.py` 的思路生成）。
不给就会现场解析当前材料目录（慢）。
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench import stamp                        # noqa: E402
from financials import canonical as cn         # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")

#: 只在繁体里出现的字（简体写法不会有）
TRAD_CHARS = set("資產負債權益現金結餘應收應付貿易存貨廠房設備營運額淨利潤總虧損"
                 "關聯聯營攤銷遞延稅項預計負擔調整匯兌帳面撇減")

BUCKETS = [
    ("真在泛化（≥3 份材料）", "generalizing"),
    ("两份材料", "two"),
    ("单份材料（过拟合候选）", "single"),
    ("英文写法 —— 服务 IFRS/美股", "english"),
    ("繁体写法 —— 服务港股", "trad"),
    ("简体但一次没触发（真待查）", "suspect"),
]


def classify(labels_by_file: dict[str, list[str]]) -> dict:
    all_labels = [lab for v in labels_by_file.values() for lab in v]
    seen: set[tuple[str, str]] = set()
    dup = 0
    rules: list[tuple[str, str]] = []
    for m in cn.MAPPINGS:
        f = str(m.field).split(".")[-1]
        for n in m.names:
            if (f, n) in seen:
                dup += 1
                continue
            seen.add((f, n))
            rules.append((f, n))

    out: dict[str, list] = defaultdict(list)
    for f, n in rules:
        uses = sum(1 for lab in all_labels if n in lab)
        if uses >= 3:
            out["generalizing"].append((f, n, uses))
        elif uses == 2:
            out["two"].append((f, n, uses))
        elif uses == 1:
            out["single"].append((f, n, uses))
        elif not CJK.search(n):
            out["english"].append((f, n, 0))
        elif any(ch in TRAD_CHARS for ch in n):
            out["trad"].append((f, n, 0))
        else:
            out["suspect"].append((f, n, 0))

    return {"dup": dup, "total": len(rules), "buckets": dict(out),
            "files": len(labels_by_file),
            "labels": len(all_labels)}


def main(argv: list[str]) -> int:
    if "--labels" in argv:
        path = Path(argv[argv.index("--labels") + 1]).expanduser()
        labels_by_file = json.loads(path.read_text(encoding="utf-8"))
    else:
        print("  没给 --labels，现场解析材料目录（慢）…")
        from bench.measure_parse import collect
        from financials.from_pdf import load_pdf_statements

        base = Path(argv[0]).expanduser() if argv and not argv[0].startswith("-") else \
            ROOT / "materials"
        labels_by_file = {}
        for p in collect([str(base)]):
            st = load_pdf_statements(str(p))
            labs: list[str] = []
            for part in (st.balance, st.income, st.cash_flow):
                for r in (part.rows if part else []):
                    if r.label:
                        labs.append(r.label)
            labels_by_file[p.name] = labs
            print(f"    ✓ {p.name[:30]:<32} {len(labs):>4} 标签")

    r = classify(labels_by_file)
    print(f"\n  材料 {r['files']} 份 · 标签 {r['labels']} 个 · "
          f"规则 {r['total']} 条（另有 {r['dup']} 条完全重复）\n")
    print(f"  {'分类':<32}{'条数':>6}{'占比':>8}")
    print("  " + "─" * 48)
    for label, key in BUCKETS:
        n = len(r["buckets"].get(key, []))
        print(f"  {label:<32}{n:>6}{100 * n / max(r['total'], 1):>7.1f}%")

    sus = r["buckets"].get("suspect", [])
    if sus:
        print(f"\n  「简体但一次没触发」{len(sus)} 条 —— 逐条看是不是老准则/简称：")
        for f, n, _ in sus[:20]:
            print(f"    {f:<28} {n!r}")

    r["_stamp"] = stamp.make(__file__, [ROOT / "financials" / "canonical.py"],
                             files=r["files"], rules=r["total"])
    print(f"\n  {stamp.show(r)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
