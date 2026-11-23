"""列角色判定 —— 哪一列是附注、哪一列是本期、哪一列是上期。

## 为什么需要这一层（三次实测，全是静默错）

    附注号被当成金额      营业成本 44.00  vs  148.9 亿
    附注号写成 131.00     收到其他筹资现金 131.00  vs  62.5 万
    取到上期当本期        年报两列，左边才是本期

**根子不是"行解析错"，是"不知道哪一列是什么"。**

## 判据：整列统计，不看单个值

「这一列 70% 的格子都是 1–3 位的裸整数」→ 那是附注列。
「这一列 40% 以上的格子有千分位或小数」→ 那是金额列。

**整列判，不逐行判** —— 单个行里「44」和「131.00」都没法区分，
但一整列的统计特征非常稳定。这也是它和旧版「形状 + 量级」兜底的关系：
旧版保留作**整列判不出时的兜底**，新版优先。

## 表格列序（中文报表惯例）

    深市 3 列  [科目, 本期, 上期]            无附注列
    沪市 4 列  [科目, 附注, 本期, 上期]      有附注列

金额列**靠左的是本期**，附注列永远在它左边。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NOTE = re.compile(r"^\d{1,3}(?:\.0+)?$")
_HAS_AMOUNT = re.compile(r"[,.]")


@dataclass
class Roles:
    """整表的列角色。下标从 1 起（0 是科目列）。"""

    note: int | None = None        # 附注列下标，没有就 None
    amounts: list[int] = field(default_factory=list)  # 金额列，从左到右 = 本期、上期
    ok: bool = False
    evidence: str = ""

    @property
    def primary(self) -> int | None:
        return self.amounts[0] if self.amounts else None


def _note_ratio(col: list[str]) -> float:
    cells = [c for c in col if c]
    if len(cells) < 3:
        return 0.0
    hits = sum(1 for c in cells if _NOTE.fullmatch(c.strip()))
    return hits / len(cells)


def _amount_ratio(col: list[str]) -> float:
    cells = [c for c in col if c]
    if not cells:
        return 0.0
    # 有千分位或小数点、**且不是附注形状** —— 附注写成 131.00 时也带点，
    # 不排除掉它，这一列就会同时被算成金额列，角色就判反了。
    hits = sum(1 for c in cells
               if _HAS_AMOUNT.search(c) and not _NOTE.fullmatch(c.strip()))
    return hits / len(cells)


def detect(rows: list[list[str]]) -> Roles:
    """从一组 `[科目, 值1, 值2, …]` 的行里判列角色。

    **返回 `ok=False` 表示判不出** —— 调用方退回旧的取值规则，不许硬猜。
    """
    if len(rows) < 8:
        return Roles(ok=False, evidence="行太少")

    ncols = max((len(r) for r in rows), default=0)
    if ncols < 3:                       # 至少要有 [科目, 本期, 上期]
        return Roles(ok=False, evidence="列数不足 3")

    cols = [[(r[i] if i < len(r) and r[i] is not None else "").strip()
             for r in rows] for i in range(1, ncols)]
    stats = [(_note_ratio(c), _amount_ratio(c)) for c in cols]

    note = None
    amounts = []
    for i, (nr, ar) in enumerate(stats, start=1):
        if nr >= 0.6 and ar < 0.2:
            if note is None:
                note = i
        elif ar >= 0.35:
            amounts.append(i)

    if len(amounts) < 2:
        return Roles(ok=False, note=note, amounts=amounts,
                     evidence="金额列不足 2 列")
    evidence = f"附注列={note}；金额列={amounts}"
    return Roles(note=note, amounts=amounts, ok=True, evidence=evidence)
