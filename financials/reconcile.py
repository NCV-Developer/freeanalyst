"""报表对账 —— 把从 PDF 抽出来的数字，跟另一份独立来源逐项比。

## 为什么必须有这一步（三次实测，全都没被别的检查发现）

    附注号被当成金额        营业成本 44.00  vs  148.9 亿     ✗
    「营业总收入」当营业收入   172.1 亿    vs  168.8 亿      ✗ 差 32.2 亿
    合并/母公司混页          单项对不上，合计却对            ✗

这三个错误**都不违反勾稽、不崩溃、数字形状完全正常**。
更关键的是：**映射率、行数、表识别数一个都不会动** ——
36 份全量重测，表数 14→14、行数 4,013→4,013、映射率逐份相同，
而其中一份的营业收入差了 389 亿。

**能抓住它们的只有一件事：拿另一份独立数据源逐项对账。**

## 这不是「数据来源」

对照源（新浪）没有页码，指不回原始披露。所以：

    ✅ 它证明「我们抽的数对不对」
    ❌ 它不提供「可以写进报告的数」

本模块只输出判定，**不修改任何数值**。数字错了要回去修抽取，不是在这儿覆盖。

## 差异要分类，不能只报「不一致」

「不一致」太粗：口径不同、取错年份、真取错，处置完全不同。四档：

    MATCH        一致
    ROUNDING     差额在四舍五入范围内（相对 0.1% 以内）—— 不算问题
    YEAR_SHIFT   我们的数等于**上期**的数 —— 取错年份，这是硬错
    MISMATCH     其它不一致
    OURS_MISSING 我们没取到（对方有）
    THEIRS_MISSING 对方没有这一项（不算我们的错）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from financials.canonical import Field
from financials import statements as stm

#: 对账哪些科目。**只挑「错了一定会影响估值结论」的那些**，
#: 全表对账会淹没在明细行里，反而看不出问题。
ITEMS: tuple[tuple[str, Field, str], ...] = (
    # 利润表
    ("营业收入", Field.REVENUE, "income"),
    ("营业成本", Field.COST_OF_REVENUE, "income"),
    ("营业利润", Field.OPERATING_INCOME, "income"),
    ("净利润", Field.NET_INCOME, "income"),
    # 资产负债表
    ("货币资金", Field.CASH, "balance"),
    ("应收账款", Field.ACCOUNTS_RECEIVABLE, "balance"),
    ("存货", Field.INVENTORY, "balance"),
    ("流动资产合计", Field.TOTAL_CURRENT_ASSETS, "balance"),
    ("非流动资产合计", Field.TOTAL_NONCURRENT_ASSETS, "balance"),
    ("资产总计", Field.TOTAL_ASSETS, "balance"),
    ("流动负债合计", Field.TOTAL_CURRENT_LIABILITIES, "balance"),
    ("负债合计", Field.TOTAL_LIABILITIES, "balance"),
    # 现金流量表
    ("销售商品、提供劳务收到的现金", Field.CF_SALES, "cash_flow"),
    ("购买商品、接受劳务支付的现金", Field.CF_PURCHASE, "cash_flow"),
    ("经营活动产生的现金流量净额", Field.CFO, "cash_flow"),
    ("投资活动产生的现金流量净额", Field.CFI, "cash_flow"),
    ("筹资活动产生的现金流量净额", Field.CFF, "cash_flow"),
    ("期末现金及现金等价物余额", Field.CASH_END, "cash_flow"),
)

ABS_TOL = 0.01          # 分
REL_TOL = 0.001         # 0.1% 以内算四舍五入


class Verdict(Enum):
    MATCH = "一致"
    ROUNDING = "四舍五入"
    YEAR_SHIFT = "取错年份"
    MISMATCH = "不一致"
    OURS_MISSING = "我们没取到"
    THEIRS_MISSING = "对方没有"


OK = {Verdict.MATCH, Verdict.ROUNDING, Verdict.THEIRS_MISSING}


@dataclass
class Item:
    name: str
    ours: float | None
    theirs: float | None
    verdict: Verdict
    diff: float | None = None
    note: str = ""

    @property
    def mark(self) -> str:
        return {"一致": "✓", "四舍五入": "≈", "取错年份": "✗",
                "不一致": "✗", "我们没取到": "✗", "对方没有": "—"}[self.verdict.value]

    @property
    def failed(self) -> bool:
        return self.verdict not in OK


@dataclass
class Report:
    code: str
    period: str = ""
    counterpart: str = ""
    items: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[Item]:
        return [i for i in self.items if i.failed]

    def clean(self) -> bool:
        """**有硬错就不算干净。**「对方没有」不算错。"""
        return not self.failed

    def headline(self) -> str:
        n, bad = len(self.items), len(self.failed)
        if not self.items:
            return "对账没有可比的科目"
        if not bad:
            return f"对账通过：{n} 项全部一致"
        names = "、".join(i.name for i in self.failed[:3])
        more = f" 等 {bad} 项" if bad > 3 else ""
        return f"对账未通过：{n} 项里 {bad} 项对不上（{names}{more}）"

    def render(self) -> list[str]:
        out = [self.headline()]
        if self.period:
            line = f"  报告期 {self.period}"
            if self.counterpart:
                line += f"　对照 {self.counterpart}"
            out.append(line)
        for i in self.items:
            ours = "—" if i.ours is None else f"{i.ours:,.2f}"
            theirs = "—" if i.theirs is None else f"{i.theirs:,.2f}"
            out.append(f"  {i.mark} {i.name:<24}{ours:>22}{theirs:>22}"
                       + (f"　{i.diff:+,.2f}" if i.diff else ""))
            if i.note and i.failed:
                out.append(f"      {i.note}")
        for n in self.notes:
            out.append(f"  ※ {n}")
        return out


def _get(st, kind: str, f: Field) -> float | None:
    set_ = getattr(st, kind, None)
    if set_ is None:
        return None
    v = set_.fields.get(f)
    if v is None and f is Field.NET_INCOME:
        # 净利润可能只出现在现金流量表的起首行
        cf = getattr(st, "cash_flow", None)
        if cf is not None:
            v = cf.fields.get(f)
    return v


def unit_mult(st, kind: str) -> float:
    """这张表的数值要乘多少才是**元**。

    ## 为什么必须换算（2026-11-22 实测）
    比亚迪 002594 年报写「单位为：千元」，解析层把单位识别对了（`unit=千元`），
    但对账直接拿原始值跟对照源的「元」比 → **17 格全部差 1000 倍**，
    看起来像是解析全错，其实一个单位换算就解决。
    """
    blk = getattr(st, kind, None)
    u = str(getattr(blk, "unit", "") or getattr(st, "unit", "") or "")
    for key, m in (("百万元", 1e6), ("亿元", 1e8), ("万元", 1e4), ("千元", 1e3), ("元", 1.0)):
        if key in u:
            return m
    return 1.0


def compare(st: stm.Statements, code: str, *, periods: int = 2,
            year: int | None = None) -> Report:
    """跑一次对账。会联网取对照数据。

    `year` 不给时，用报表自己的期间；再不行用最新一期并**写一句说明**。
    """
    from datasources import sina

    rep = Report(code=code)
    fetched: dict[str, list[sina.SinaPeriod]] = {}
    for kind in ("income", "balance", "cash_flow"):
        try:
            fetched[kind] = sina.fetch(code, kind, periods=periods)
        except Exception as exc:                        # noqa: BLE001
            rep.notes.append(f"{kind} 对照数据没取到（{type(exc).__name__}: {exc}）")
            fetched[kind] = []

    # 选报告期：优先用报表自己认出来的年份
    def pick(lst: list[sina.SinaPeriod]) -> sina.SinaPeriod | None:
        if not lst:
            return None
        if year:
            for p in lst:
                if p.year == year:
                    return p
        return lst[0]

    picked = {k: pick(v) for k, v in fetched.items()}
    first = next((p for p in picked.values() if p), None)
    if first:
        rep.period = first.date
        rep.counterpart = first.scope
        if year and first.year != year:
            rep.notes.append(
                f"报表期间是 {year}，但对照数据里没有这一年，用的是 {first.year} ——"
                f"**这时对不上的每一项都可能只是年份不同，不能当错误**。")

    def prior(kind: str) -> sina.SinaPeriod | None:
        lst = fetched.get(kind) or []
        if len(lst) < 2:
            return None
        return lst[1]

    noted_units: set[str] = set()
    for name, fld, kind in ITEMS:
        p = picked.get(kind)
        if p is None:
            continue
        ours = _get(st, kind, fld)
        mult = unit_mult(st, kind)
        if ours is not None and mult != 1.0:
            ours = ours * mult
            if kind not in noted_units:
                noted_units.add(kind)
                rep.notes.append(f"{kind}：报表单位不是元，已按 {mult:g} 倍换算成元再对账")
        theirs = p.items.get(name)
        if ours is None and theirs is None:
            continue
        if ours is None:
            rep.items.append(Item(name, None, theirs, Verdict.OURS_MISSING,
                                  note="对方有这一项，我们没抽出来 —— 回去查这一行"))
            continue
        if theirs is None:
            rep.items.append(Item(name, ours, None, Verdict.THEIRS_MISSING,
                                  note="对照源收录口径里没有这一项，不算错"))
            continue

        diff = ours - theirs
        if abs(diff) <= ABS_TOL:
            rep.items.append(Item(name, ours, theirs, Verdict.MATCH, diff))
            continue
        rel = abs(diff) / max(abs(theirs), 1.0)
        if rel <= REL_TOL:
            rep.items.append(Item(name, ours, theirs, Verdict.ROUNDING, diff,
                                  note=f"相对差 {rel:.3%}，四舍五入范围内"))
            continue
        # **取错年份**：我们的数正好等于上一期
        pp = prior(kind)
        if pp is not None:
            pv = pp.items.get(name)
            if pv is not None and abs(ours - pv) <= ABS_TOL:
                rep.items.append(Item(
                    name, ours, theirs, Verdict.YEAR_SHIFT, diff,
                    note=f"我们的数等于 {pp.date}（上期）的 {pv:,.2f} —— "
                         f"取错列了，把上期当成了本期"))
                continue
        rep.items.append(Item(
            name, ours, theirs, Verdict.MISMATCH, diff,
            note=f"差 {diff:+,.2f}（相对 {rel:.1%}）—— 回去核原件这一行；"
                 f"也可能是口径不同（合并/母公司、含/不含受限资金）"))
    return rep
