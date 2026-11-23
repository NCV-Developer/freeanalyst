"""置信度分层 —— 能判就判，不能判就明说。

## 规则（2026-11-22 用户定）

    ① 哪类文件适合目前的解析方法 → 文件级门槛
    ② 文件中哪些字段适合         → 字段级门槛
    ③ 符合①②的字段要 ≥99% 正确
    ④ 解析不出的文件、低置信字段 → 明确告诉用户人工检查

**不再追求覆盖率。** 给出答案的必须对，给不出就拒答。

## 为什么这条比"继续修解析"快

实测（10 家文字版非金融，公司级）：给出答案 177 格，答错 3 格 ——
**这 3 格系统本来就知道有问题**（`_resolve_conflicts` 已把多页取值冲突
记进 `st.conflicts`），只是记完照样把值输出了。

把已有证据变成置信度，比再修一个月解析快得多。
"""

from __future__ import annotations

import re

HIGH = "高置信"
LOW = "低置信"
REJECT = "拒答"

# ⚠️ 不能有「利息收入」——普通公司的财务费用明细里也有它，会大面积误判。
_FIN_MARKERS = ("已赚保费", "手续费及佣金", "利息净收入", "吸收存款", "发放贷款")
_FIN_VALUE = re.compile(
    r"(已赚保费|利息净收入|吸收存款|发放贷款及垫款|手续费及佣金净收入)"
    r"[^\n]{0,40}?[\d,]{7,}")


def is_financial(st) -> bool:
    """是不是金融业报表 —— **必须看有没有实际金额，不能看标签在不在**。

    A 股利润表是统一模板：「已赚保费」「手续费及佣金收入」「利息净收入」
    这些科目**每家公司都有，只是空行**（301072 第 103 页实测）。
    按标签判，会把所有中国年报都判成金融业 → 文件级全拒答。
    """
    text = str(getattr(st, "raw_text", "") or "")
    if text:
        # 原文里找「金融业科目 + 后面跟着真实金额」。
        # 空模板行后面没数字，不会命中；「手续费及佣金收入」（普通公司也有）
        # 不在标志里，只有「手续费及佣金净收入」才是金融业写法。
        return bool(_FIN_VALUE.search(text))
    for kind in ("income", "balance", "cash_flow"):
        blk = getattr(st, kind, None)
        for row in getattr(blk, "rows", []) or []:
            if not any(m in str(getattr(row, "label", "") or "") for m in _FIN_MARKERS):
                continue
            v = getattr(row, "value", None)
            if v:
                return True
    return False


def file_gate(st) -> tuple[bool, list[str]]:
    """文件级门槛：这份材料适不适合目前的解析方法。

    只看**载体**和**行业** —— 这两条不满足，后面再努力也是错的方向。
    """
    reasons: list[str] = []
    audited = str(getattr(st, "audited", "") or "")
    warns = " ".join(str(w) for w in (getattr(st, "warnings", None) or []))
    if "OCR" in audited or "OCR" in warns:
        reasons.append("扫描件/OCR 材料：数字识别可能错，目前不适用自动解析")

    if is_financial(st):
        reasons.append("金融业报表：科目体系不同（无营业收入—营业成本），目前不适用")

    # **解析不出来就是解析不出来**，不必先认出它是金融业。
    # 实测：平安、中信的利润表写法没被金融标志命中，但 18 个关键科目
    # 一个只取到 2 个、一个 7 个 —— 这种材料放过去只会产出大量空格和错值。
    from . import reconcile as _rc
    have = 0
    for _item, fld, kind in _rc.ITEMS:
        blk = getattr(st, kind, None)
        if blk is not None and blk.fields.get(fld) is not None:
            have += 1
    if have < 9:
        reasons.append(
            f"18 个关键科目只取到 {have} 个（不足一半）—— 这份材料目前解析不出来，"
            "请人工处理，不要用它的自动结果")
    return (not reasons), reasons


def _conflict_texts(st) -> list[str]:
    """冲突记录挂在**各张表**上（balance/income/cash_flow），不在整体上。"""
    out: list[str] = []
    for kind in ("income", "balance", "cash_flow"):
        blk = getattr(st, kind, None)
        for s in (getattr(blk, "conflicts", None) or []):
            out.append(str(s))
    for s in (getattr(st, "conflicts", None) or []):   # 有的版本也放整体
        out.append(str(s))
    return out


def item_level(st, item: str, value, *, verified: bool | None = None) -> tuple[str, str]:
    """字段级门槛。返回 `(档位, 原因)`。

    value 为 None        → 拒答（没抽到，不硬给）
    有未决的多页取值冲突 → 低置信（**这就是 688316 那 3 格的情形**）
    外部核对不一致       → 低置信
    其余                 → 高置信
    """
    if value is None:
        return REJECT, "没抽到这一行，不硬给值"
    # **外部证据最强**：独立对照源核对一致，就不用再靠内部推断猜合并口径。
    if verified is True:
        return HIGH, "已与独立对照源核对一致"
    if verified is False:
        return LOW, "与外部对照源不一致，需人工核对"
    # 没有外部真值时（例如用户自己的私密材料）才退回内部证据，**保守**。
    for s in _conflict_texts(st):
        if item not in s:
            continue
        # **两种证据一致就不算未决**：合并/母公司两套数同时出现是常态，
        # 只要「规模法」和「页序」指向同一页，取值有依据，可以高置信。
        # （002572 等 18/18 全对的公司也都有冲突记录，不能一律标低置信。）
        if "与首次出现一致" in s:
            continue
        # 规模法**推翻了页序**（688316 那 3 格就是这种），或者根本没依据比较 ——
        # 这时候不替用户决定，交人工。
        page = re.search(r"第 (\d+) 页", s)
        where = f"（见第 {page.group(1)} 页附近）" if page else ""
        return LOW, f"合并/母公司取值冲突且证据不一致{where}：{s[:80]}"
    if verified is None:
        return HIGH, "无外部对照源；内部证据一致（未经外部核对）"
    return HIGH, ""
