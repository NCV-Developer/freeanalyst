"""取数覆盖矩阵 —— 把「还有多少取数问题」变成可数的数字。

## 它是什么

`材料 × 关键科目` 的二维表，每格记「这一个数，我们取对了没有」。

    36 份材料 × 18 个科目 = 648 格

## 为什么需要它（这是这几天最贵的一课）

修的每一个严重错误，**别的检查一个都抓不到**：

    附注号当金额        勾稽照样平、映射率不变、行数不变、不崩溃
    营业总收入当营业收入  同上
    合并/母公司混页      同上

36 份全量重测：表数 14→14、行数 4,013→4,013、映射率逐份相同 ——
而其中一份的营业收入差了 389 亿。

**能抓住它们的只有：拿独立来源逐项对账，然后把这些格子数出来。**

## 七个状态，其中两个是关键设计

    OK / ROUNDING        没问题
    MISMATCH             取不对（有真值、数值不同）
    MISSING_OURS         取不全（有真值、我们没抽出来）
    NO_TABLE             取不到（整张表没定位到）
    UNVERIFIED           没有真值 —— **既不算通过，也不算失败**
    LOAD_FAILED          材料根本读不进来

**`NO_TABLE` 必须独立于 `MISSING_OURS`**：一次表定位失败会让 6 个科目全缺，
把它算成「6 个科目取不全」会把失败数放大 6 倍，指标就废了。

**`UNVERIFIED` 不许混进通过率**：没有真值的格子，通过率无从谈起。

## 判定从哪来

**不写第二套判定。** 全部调 `financials/reconcile.py` —— 判定规则分两处，
是最容易漂的地方。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from financials import reconcile
from financials.canonical import Field
from financials.from_pdf import load_pdf_statements

from . import truth as truthmod


class Status(Enum):
    OK = "一致"
    ROUNDING = "四舍五入"
    MISMATCH = "取不对"
    MISSING_OURS = "取不全"
    NO_TABLE = "取不到"
    UNVERIFIED = "未验证"
    LOAD_FAILED = "载入失败"
    UNSUPPORTED = "格式不支持"


#: 算「失败」的状态。**UNVERIFIED 不在里面** —— 没有真值谈不上对错。
FAILED = {Status.MISMATCH, Status.MISSING_OURS, Status.NO_TABLE,
          Status.LOAD_FAILED, Status.UNSUPPORTED}


@dataclass
class Cell:
    file: str
    market: str
    code: str
    statement: str
    item: str
    ours: float | None = None
    truth: float | None = None
    status: Status = Status.UNVERIFIED
    note: str = ""

    def csv_row(self) -> list:
        return [self.file, self.market, self.code, self.statement, self.item,
                "" if self.ours is None else f"{self.ours:.2f}",
                "" if self.truth is None else f"{self.truth:.2f}",
                self.status.value, self.note]


@dataclass
class Matrix:
    cells: list[Cell] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: 每份材料的分层标签（载体/行业/板块/规模/盈亏），见 `layers.py`
    meta: dict[str, dict[str, str]] = field(default_factory=dict)

    def by_layer(self) -> dict[str, dict[str, list["Cell"]]]:
        """按分层维度分组 —— **每层单独报通过率，不许只看总平均**。"""
        out: dict[str, dict[str, list[Cell]]] = {}
        for c in self.cells:
            for dim, label in (self.meta.get(c.file) or {}).items():
                out.setdefault(dim, {}).setdefault(label, []).append(c)
        return out

    @property
    def files(self) -> int:
        return len({c.file for c in self.cells})

    def count(self, status: Status) -> int:
        return sum(1 for c in self.cells if c.status is status)

    def verifiable(self) -> list[Cell]:
        """能判对错的格子 —— 排掉没有真值的、载入失败的和格式不支持的。"""
        return [c for c in self.cells
                if c.status not in (Status.UNVERIFIED, Status.LOAD_FAILED,
                                    Status.UNSUPPORTED)]

    def failed(self) -> list[Cell]:
        return [c for c in self.cells if c.status in FAILED]

    def by_market(self) -> dict[str, list[Cell]]:
        out: dict[str, list[Cell]] = {}
        for c in self.cells:
            out.setdefault(c.market, []).append(c)
        return out


CODE_CN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
CODE_HK = re.compile(r"(?<!\d)(\d{5})(?!\d)")
#: 美股申报表名 —— **必须优先于 6 位数字**。
#: 实测坑：`320193_2025-10-31_10-K.htm` 里的 320193 是 **CIK**，
#: 按"6 位数字 = A 股代码"判会把它标成 cn，然后用 A 股的对照源去对账 ✗
FORM_US = re.compile(r"(?:^|_)(10-[KQ]|20-F|40-F|6-K)(?:_|\.|$)", re.I)
#: CIK 长度不固定（6–10 位都有），不能套用「6 位数字」那条正则。
CIK = re.compile(r"(?<!\d)(\d{4,10})(?!\d)")


def infer(path: Path) -> tuple[str, str]:
    """从文件名猜市场和代码。

    ⚠️ 猜不出来就返回 ("", "")，调用方把这些格子标 UNVERIFIED —— **不许瞎猜代码**，
    因为拿错公司的数据对账比不对账更坏（会把对的判成错的）。
    """
    name = path.name
    if FORM_US.search(name):
        m = CIK.search(name)
        return "us", (m.group(1) if m else "")
    m = CODE_CN.search(name)
    if m:
        return "cn", m.group(1)
    m = CODE_HK.search(name)
    if m:
        return "hk", m.group(1)
    return "", ""


def build(files: list[Path], *, year: int | None = None,
          providers: dict[str, truthmod.Provider] | None = None) -> Matrix:
    """跑整个矩阵。会联网（A 股取对照数据）。"""
    m = Matrix()
    for p in files:
        market, code = infer(p)
        if not market:
            m.notes.append(f"{p.name}：文件名里认不出市场/代码 → 全部标未验证")
        # HTML 报表：**三表载入还没做。** 美股 10-K 就是 .htm —— 这不是词表问题，
        # 是连载入都还没有。标成「格式不支持」，别混进「载入失败」。
        if p.suffix.lower() in (".htm", ".html"):
            note = "HTML 三表载入还没做（美股 10-K 走这条路）"
            m.notes.append(f"{p.name}：{note}")
            for name, _f, kind in reconcile.ITEMS:
                m.cells.append(Cell(p.name, market or "?", code, kind, name,
                                    status=Status.UNSUPPORTED, note=note))
            continue
        try:
            st = load_pdf_statements(p)
        except Exception as exc:                        # noqa: BLE001
            m.notes.append(f"{p.name}：载入失败（{type(exc).__name__}: {exc}）")
            for name, _f, kind in reconcile.ITEMS:
                m.cells.append(Cell(p.name, market or "?", code, kind, name,
                                    status=Status.LOAD_FAILED,
                                    note=f"{type(exc).__name__}: {exc}"))
            continue

        rep = reconcile.compare(st, code, year=year) if (market and code) else None
        from . import layers
        m.meta[p.name] = layers.classify(code, st)
        by_name = {i.name: i for i in (rep.items if rep else [])}
        if rep:
            m.notes.extend(f"{p.name}：{n}" for n in rep.notes)

        provider = (providers or {}).get(market)
        has_truth = bool(rep and rep.items)

        for name, fld, kind in reconcile.ITEMS:
            block = getattr(st, kind, None)
            if block is None:
                m.cells.append(Cell(p.name, market or "?", code, kind, name,
                                    status=Status.NO_TABLE,
                                    note="这张表没定位到 —— 别算成 6 个科目都缺"))
                continue
            ours = block.fields.get(fld)
            if fld is Field.NET_INCOME and ours is None and st.cash_flow:
                ours = st.cash_flow.fields.get(fld)
            if ours is not None:
                # 报表可能是千元/万元（比亚迪就是千元），显示前统一成元
                ours = ours * reconcile.unit_mult(st, kind)

            it = by_name.get(name)
            if it is None or not has_truth:
                m.cells.append(Cell(p.name, market or "?", code, kind, name,
                                    ours=ours, status=Status.UNVERIFIED,
                                    note="没有独立真值 —— 谈不上对错" if not has_truth
                                    else "两边都没这一项"))
                continue

            status = {
                reconcile.Verdict.MATCH: Status.OK,
                reconcile.Verdict.ROUNDING: Status.ROUNDING,
                reconcile.Verdict.MISMATCH: Status.MISMATCH,
                reconcile.Verdict.YEAR_SHIFT: Status.MISMATCH,
                reconcile.Verdict.OURS_MISSING: Status.MISSING_OURS,
                reconcile.Verdict.THEIRS_MISSING: Status.UNVERIFIED,
            }[it.verdict]
            m.cells.append(Cell(p.name, market or "?", code, kind, name,
                                ours=it.ours, truth=it.theirs, status=status,
                                note=it.note if status is not Status.OK else ""))
            _ = provider          # 真值由 reconcile 内部取，这里只用来判"有没有"
    return m


def write_csv(m: Matrix, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "market", "code", "statement", "item",
                    "ours", "truth", "status", "note"])
        for c in m.cells:
            w.writerow(c.csv_row())


def summarise(m: Matrix) -> dict:
    """按市场汇总。**未验证单独一列，不许并进通过率。**"""
    out = {}
    for mk, cells in m.by_market().items():
        # ⚠️ 这里必须和 `Matrix.verifiable()` 用同一套排除规则。
        # 之前这里只排了 UNVERIFIED，于是"格式不支持"的格子被算成"能验"——
        # 美股那一行就出现了「能验 216 / 一致 0 / 不支持 216」这种自相矛盾的数。
        _skip = (Status.UNVERIFIED, Status.LOAD_FAILED, Status.UNSUPPORTED)
        verifiable = [c for c in cells if c.status not in _skip]
        bad = [c for c in cells if c.status in FAILED]
        out[mk] = dict(
            files=len({c.file for c in cells}),
            # ⚠️ **公司数 ≠ 份数。** 港股那 6 份材料里 5 份是同一家银行，
            # 按份数统计会得出"港股科目取到率 24%"这种被带偏的结论。
            companies=len({c.code for c in cells if c.code}),
            cells=len(cells),
            verifiable=len(verifiable),
            coverage=len(verifiable) / len(cells) if cells else 0.0,
            ok=sum(1 for c in cells if c.status is Status.OK),
            rounding=sum(1 for c in cells if c.status is Status.ROUNDING),
            mismatch=sum(1 for c in cells if c.status is Status.MISMATCH),
            missing=sum(1 for c in cells if c.status is Status.MISSING_OURS),
            no_table=sum(1 for c in cells if c.status is Status.NO_TABLE),
            unsupported=sum(1 for c in cells if c.status is Status.UNSUPPORTED),
            unverified=sum(1 for c in cells if c.status is Status.UNVERIFIED),
            load_failed=sum(1 for c in cells if c.status is Status.LOAD_FAILED),
            # ── 第二层：**取到没取到，不需要真值** ────────────────────────
            #
            # 这一层是所有市场都能算的。之前只有 A 股有数，是因为我把
            # "能不能判对错"和"有没有取到"混成了一件事 —— 其实材料读进来了、
            # 这一格填上了值，跟有没有对照源无关。
            #
            # `expected` = 有机会取的格子（排掉格式不支持、载入失败、整表没定位）
            # `present`  = 其中真的取到值的
            expected=sum(1 for c in cells if c.status not in (
                Status.UNSUPPORTED, Status.LOAD_FAILED, Status.NO_TABLE)),
            present=sum(1 for c in cells if c.ours is not None),
            pass_rate=(len(verifiable) - len([c for c in verifiable if c in bad])) / len(verifiable)
            if verifiable else 0.0,
        )
        d = out[mk]
        d["item_rate"] = d["present"] / d["expected"] if d["expected"] else 0.0
    return out


def render(m: Matrix) -> list[str]:
    s = summarise(m)
    out = [f"覆盖矩阵：{m.files} 份材料，{len(m.cells)} 格", ""]
    head = ("市场", "材料", "公司", "格数", "科目取到", "能验", "真值覆盖", "一致",
            "取不对", "取不全", "取不到", "不支持", "载入失败", "未验证")
    out.append("  " + "".join(f"{h:<11}" for h in head))
    for mk, d in sorted(s.items()):
        row = (mk or "?",
               d["files"],
               d["companies"],
               d["cells"],
               f"{d['present']}/{d['expected']}",          # 第二层：取到没取到
               d["verifiable"],
               f"{d['coverage']:.0%}",
               d["ok"], d["mismatch"], d["missing"], d["no_table"],
               d["unsupported"], d["load_failed"], d["unverified"])
        out.append("  " + "".join(f"{str(v):<11}" for v in row))
    lay = m.by_layer()
    if lay:
        out += ["", "  分层通过率（分母=能判对错的格子；困难样本不许被总平均盖掉）"]
        for dim, groups in lay.items():
            out.append(f"    {dim}")
            for label, cells in sorted(groups.items()):
                ver = [c for c in cells if c.status not in (
                    Status.UNVERIFIED, Status.LOAD_FAILED, Status.UNSUPPORTED)]
                if not ver:
                    continue
                nbad = sum(1 for c in ver if c.status in FAILED)
                out.append(f"      {label:<10}{len(ver)-nbad}/{len(ver)} = "
                           f"{(len(ver)-nbad)/len(ver):.1%}   失败 {nbad}")
    bad = m.failed()
    if bad:
        out += ["", f"失败的 {len(bad)} 格（前 12）："]
        for c in bad[:12]:
            out.append(f"  ✗ {c.file[:26]:<28}{c.item:<22}{c.status.value}"
                       f"　{c.note[:44]}")
    if m.notes:
        out += [""] + [f"  ※ {n}" for n in m.notes[:8]]
    return out


def gate(m: Matrix, *, max_bad: int = 0) -> bool:
    """流水线闸门：失败格超过阈值就不通过。"""
    return len(m.failed()) <= max_bad
