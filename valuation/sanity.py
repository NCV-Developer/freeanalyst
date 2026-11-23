"""估值结果的自检 —— **算完之后回过头看这个数像不像真的**。

## 为什么需要（跟「勾稽」不是一回事）

    勾稽        表内算术自洽：资产 = 负债 + 权益
    自检        结果落在合理区间吗：EBITDA 率 68%（白酒正常）vs 68,000%（单位错）

**勾稽查的是「抄错了没有」，自检查的是「这数像不像真的」。**

## 为什么这件事比多算一个方法更重要

估值错有三种，**都不会抛异常**：

    单位错   财报写「千美元」，引擎按「元」算 → 全部大 1000 倍
    科目错   「归属于母公司净利润」当成「净利润」→ 少数股东权益被吞
    口径错   单体 vs 合并 → 收入差一半

三种都只会给你一个**看起来很正常的数**。所以必须有不相干的几条常识在最后兜着。

## 判据从哪来

一部分来自公开的估值实务惯例（Dexter 这个开源 agent 的 DCF skill 里也是这三条），
一部分是这个项目自己踩过的坑（终值占比、敏感性跨度）。

## ★ 硬规矩：不过就不出结论

自检的价值不在于「列出来给人看」✗ —— 而在于**不过就标成不可用** ✓
把三条 ✗ 混在一堆数字里，等于让人自己发现，那就是没做。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["ok", "warn", "fail", "na"]


@dataclass
class Check:
    """一条自检。`verdict` 只有四档，`detail` 必须是人话。"""

    name: str
    verdict: Verdict
    detail: str
    value: str = ""

    @property
    def mark(self) -> str:
        return {"ok": "✓", "warn": "⚠", "fail": "✗", "na": "—"}[self.verdict]


@dataclass
class SanityReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == "fail"]

    @property
    def warned(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == "warn"]

    def usable(self) -> bool:
        """**有 fail 就不可用。** 这是这个模块存在的全部理由。"""
        return not self.failed

    def headline(self) -> str:
        if self.failed:
            names = "、".join(c.name for c in self.failed[:3])
            return f"**估值结论不可用** —— {len(self.failed)} 项自检未通过（{names}）"
        if self.warned:
            return f"估值结论可用，但有 {len(self.warned)} 项需要留意"
        return "估值结论可用"

    def render(self) -> list[str]:
        out = [self.headline()]
        for c in self.checks:
            line = f"  {c.mark} {c.name}"
            if c.value:
                line += f"　{c.value}"
            out.append(line)
            if c.verdict in ("warn", "fail") and c.detail:
                out.append(f"      {c.detail}")
        return out


#: 各行业 **EBITDA 率** 的常见区间（百分数）。**只是参考区间，不是标准。**
#:
#: ⚠️ 用它判「像不像真的」，不判「好不好」。落在区间外先怀疑取数，
#: 再怀疑公司确实特殊 —— **不要反过来**（先信自己的计算，再信公司特殊）。
#:
#: 这份表比业界的粗略口径偏宽 —— 宽一点才能只抓真错，不误伤。
EBITDA_MARGIN = {
    "白酒": (35, 75),
    "食品饮料": (10, 30),
    "医药": (10, 35),
    "医疗器械": (8, 30),
    "半导体": (10, 40),
    "软件": (5, 35),
    "互联网": (5, 40),
    "家电": (5, 20),
    "汽车": (3, 15),
    "机械": (5, 20),
    "化工": (5, 20),
    "建材": (5, 25),
    "钢铁": (3, 15),
    "有色金属": (3, 20),
    "煤炭": (10, 35),
    "电力": (10, 40),
    "房地产": (5, 30),
    "建筑": (2, 12),
    "零售": (2, 12),
    "物流": (5, 20),
    "银行": None,          # 银行没有 EBITDA 概念
    "保险": None,
    "证券": None,
}

#: 默认（认不出行业时）—— 只抓「差一个数量级」这种错，不抓细的
EBITDA_MARGIN_DEFAULT = (0, 90)


def _fmt(x: float | None, unit: str = "") -> str:
    if x is None:
        return "—"
    if abs(x) >= 1e8:
        return f"{x / 1e8:,.2f} 亿{unit}"
    if abs(x) >= 1e4:
        return f"{x / 1e4:,.2f} 万{unit}"
    return f"{x:,.2f} {unit}"


def check_all(*, ev_low: float | None = None, ev_mid: float | None = None,
              ev_high: float | None = None, market_cap: float | None = None,
              net_debt: float | None = None,
              terminal_share: float | None = None,
              value_per_share: float | None = None,
              fcf_per_share: float | None = None,
              ebitda: float | None = None, revenue: float | None = None,
              industry: str = "", wacc: float | None = None,
              roic: float | None = None, unit: str = "") -> SanityReport:
    """跑全部自检。给不出的项标 `na`，**不猜、不填默认值**。

    每个参数都是「可有可无」—— 缺什么就少一条检查，而不是拿默认值凑一条。
    """
    rep = SanityReport()

    # ① 算出的企业价值 vs 市场—— **这条最有用**
    # 不是「发现了别人没发现的价值」，而是「我算错了」。
    if ev_mid is not None and market_cap:
        ratio = ev_mid / market_cap
        if 0.5 <= ratio <= 1.5:
            rep.checks.append(Check(
                "算出的企业价值 / 市值", "ok", "", f"{ratio:.2f}×"))
        elif 0.25 <= ratio <= 2.5:
            rep.checks.append(Check(
                "算出的企业价值 / 市值", "warn",
                "偏离正常范围（0.5–1.5×）：净债或市值口径可能不一致，"
                "也可能公司确实处在特殊阶段。用之前先核净债。",
                f"{ratio:.2f}×"))
        else:
            rep.checks.append(Check(
                "算出的企业价值 / 市值", "fail",
                "差一个量级以上（正常 0.5–1.5×）—— 这不是「发现低估」，"
                "是**某一步算错了**：先查单位（千元/百万元）、再查净债正负号。",
                f"{ratio:.2f}×"))
    else:
        rep.checks.append(Check("算出的企业价值 / 市值", "na", "缺市值"))

    # ② 终值占比
    if terminal_share is not None:
        s = terminal_share
        if 0.5 <= s <= 0.8:
            rep.checks.append(Check("终值占企业价值", "ok", "", f"{s:.0%}"))
        elif s > 0.9:
            rep.checks.append(Check(
                "终值占企业价值", "fail",
                "超过 90%：等于在说「未来五年的现金流不重要，全靠无限远的未来」"
                "—— 增长率或折现率至少有一个不合理。",
                f"{s:.0%}"))
        elif s < 0.4:
            rep.checks.append(Check(
                "终值占企业价值", "warn",
                "低于 40%：近期预测相对终值过高，通常意味着短期增长率设得太激进。",
                f"{s:.0%}"))
        else:
            rep.checks.append(Check(
                "终值占企业价值", "warn",
                "落在 50–80% 之外、但没到极端 —— 交叉验证一下终值算法。",
                f"{s:.0%}"))
    else:
        rep.checks.append(Check("终值占企业价值", "na", "没用 DCF 或未算终值"))

    # ③ 每股价值 vs 每股自由现金流
    # 粗糙但有效：好公司的 P/FCF 极少长期在 15 倍以下或 25 倍以上太多。
    if value_per_share and fcf_per_share and fcf_per_share > 0:
        mult = value_per_share / fcf_per_share
        if 8 <= mult <= 35:
            rep.checks.append(Check(
                "每股价值 / 每股自由现金流", "ok", "", f"{mult:.1f}×"))
        elif 3 <= mult <= 60:
            rep.checks.append(Check(
                "每股价值 / 每股自由现金流", "warn",
                "落在 15–25× 的常见区间之外（放宽到 8–35 仍算通过）—— "
                "检查增长率是否与这个倍数矛盾。",
                f"{mult:.1f}×"))
        else:
            rep.checks.append(Check(
                "每股价值 / 每股自由现金流", "fail",
                f"约 {mult:.0f} 倍 —— 要么每股价值错了，要么每股 FCF 取错了科目。",
                f"{mult:.1f}×"))
    else:
        rep.checks.append(Check("每股价值 / 每股自由现金流", "na",
                                "缺每股价值或每股 FCF"))

    # ④ EBITDA 率是否落在行业区间 —— **专门抓单位错和科目错**
    if ebitda is not None and revenue:
        margin = ebitda / revenue * 100
        if industry in EBITDA_MARGIN and EBITDA_MARGIN[industry] is None:
            # 金融业没有 EBITDA 概念 —— 这条不适用，不是失败
            rep.checks.append(Check(
                f"EBITDA 率（{industry}）", "na",
                "金融业没有 EBITDA 概念，这条不适用"))
        elif not industry:
            # ⚠️ **认不出行业就不给判据。** 第一版用了一个 0–90% 的宽区间当默认，
            # 于是某营销公司 0.5% 的 EBITDA 率也被打了 ✓ ✗ ——
            # 那等于告诉人「这个数没问题」，而实际上根本没检查。
            # `na` 的语义是「查不了」，**不是「通过了」**。
            rep.checks.append(Check(
                "EBITDA 率", "na",
                "没填行业 —— 没有可比区间，这条查不了"
                "（在配置里给 `industry` 就能查）"))
        else:
            lo, hi = EBITDA_MARGIN.get(industry, EBITDA_MARGIN_DEFAULT)
            if lo <= margin <= hi:
                rep.checks.append(Check(
                    f"EBITDA 率（{industry}）", "ok", "", f"{margin:.1f}%"))
            elif 0 < margin <= 100:
                rep.checks.append(Check(
                    f"EBITDA 率（{industry}）", "warn",
                    f"落在常见区间 {lo}–{hi}% 之外。先怀疑科目取错"
                    f"（比如把「营业总收入」当成「营业收入」），再怀疑公司确实特殊。",
                    f"{margin:.1f}%"))
            else:
                rep.checks.append(Check(
                    f"EBITDA 率（{industry}）", "fail",
                    "超出 0–100% —— **几乎一定是单位或科目错了**。",
                    f"{margin:.1f}%"))
    else:
        rep.checks.append(Check("EBITDA 率", "na", "缺 EBITDA 或营业收入"))

    # ⑤ WACC vs ROIC
    if wacc is not None and roic is not None:
        gap = roic - wacc
        if 0.02 <= gap <= 0.04:
            rep.checks.append(Check(
                "ROIC − WACC", "ok", "落在「创造价值」的典型区间", f"{gap:.1%}"))
        elif gap > 0:
            rep.checks.append(Check(
                "ROIC − WACC", "warn",
                "ROIC 高于 WACC 但差距不在 2–4% 的典型区间 —— "
                "如果差得很大，可能 ROIC 用了期末而 WACC 用了平均，口径要对齐。",
                f"{gap:.1%}"))
        else:
            rep.checks.append(Check(
                "ROIC − WACC", "warn",
                "ROIC 低于 WACC —— 公司在毁灭价值。"
                "这个结论本身可能对，但要跟行业和同行比过再说。",
                f"{gap:.1%}"))
    else:
        rep.checks.append(Check("ROIC − WACC", "na", "缺 ROIC 或 WACC"))

    # ⑥ 区间跨度（这个项目自己踩过的坑：跨度太大时单点结论没意义）
    if ev_low is not None and ev_high is not None and ev_mid:
        span = (ev_high - ev_low) / abs(ev_mid)
        if span <= 0.5:
            rep.checks.append(Check("估值区间跨度", "ok", "", f"{span:.0%}"))
        elif span <= 1.0:
            rep.checks.append(Check(
                "估值区间跨度", "warn",
                "跨度超过中枢的 50% —— 谈判时应给区间，不要给一个点。",
                f"{span:.0%}"))
        else:
            rep.checks.append(Check(
                "估值区间跨度", "warn",
                "跨度超过中枢的 100% —— 这个模型对假设太敏感，"
                "**单点结论不可靠**，只把它当边界看。",
                f"{span:.0%}"))

    return rep
