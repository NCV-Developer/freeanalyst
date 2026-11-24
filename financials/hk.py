"""港股繁体中文年报 —— 词表与主表定位。

## 与台湾 MOPS 的关系

同为繁体中文，但**写法不同**，不能直接共用：

    台湾 MOPS   資產總計 / 營業收入合計 / 本期淨利（淨損）
    港股        資產總額 / 收入 / 年度盈利 / 年末的現金及現金等價物

## 实测（腾讯 00700 2025 年报）

    資產總額 2,038,986   負債總額 797,921   年度盈利 229,801
    現金及現金等價物 141,041 = 年末的現金及現金等價物（跨表勾稽一致）
    单位：人民币百万元

## 小米 01810 的文字层是 `(cid:xxxxx)` 编码（字体子集无 Unicode 映射），
**抽不出可读文字**，与解析器无关 —— 这类材料只能走 OCR，单列。
"""

from __future__ import annotations

#: 港股繁体科目 → （匹配模式, 排除模式）
ITEM_MAP_HK: dict[str, tuple[str, str | None]] = {
    "货币资金": (r"現金及現金等價物", r"年末|年初|滙兌|匯兌|增加|減少"),
    "应收账款": (r"應收賬款|應收帳款|貿易應收款項|應收貿易款項", None),
    "存货": (r"存貨", None),
    "流动资产合计": (r"流動資產總額|流動資產合計|流動資產總值", None),
    "非流动资产合计": (r"非流動資產總額|非流動資產合計|非流動資產總值", None),
    "资产总计": (r"資產總額|總資產|資產總值", None),
    "流动负债合计": (r"流動負債總額|流動負債合計|流動負債總值", None),
    "负债合计": (r"負債總額", r"權益及負債總額|權益總額"),
    "营业收入": (r"^收入$|收入$", r"收入成本|利息收入|其他收入|遞延收入|租金收入"),
    "营业成本": (r"收入成本|銷售成本", None),
    "营业利润": (r"經營盈利|經營利潤|營業利潤|經營溢利", None),
    "净利润": (r"年度盈利|年內溢利|本期溢利|年度利潤", r"全面收益|其他全面"),
    "经营活动产生的现金流量净额": (r"經營活動.*現金流量淨額", None),
    "投资活动产生的现金流量净额": (r"投資活動.*現金流量淨額", None),
    "筹资活动产生的现金流量净额": (r"融資活動.*現金流量淨額|籌資活動.*現金流量淨額", None),
    "期末现金及现金等价物余额": (r"年末的現金及現金等價物|期末現金及現金等價物", None),
}

#: 主表标题（港股繁体）
TITLES_HK = ("綜合收益表", "綜合財務狀況表", "綜合現金流量表",
             "綜合全面收益表", "財務狀況表", "現金流量表")


def _unlabeled_before(rows, kw: str):
    """小计行经常**没有标签**：取 `kw` 之前最近的「无标签但有值」的行。

    腾讯实测：非流动资产合计 1,443,526（在「存貨」前）、
    流动资产合计 595,460（在「資產總額」前），两者相加 = 資產總額 ✓。
    """
    for i, row in enumerate(rows):
        if kw in str(row[0]):
            for prev in reversed(rows[:i]):
                lab = str(prev[0])
                if "没有标签" in lab or not lab.strip():
                    from . import ifrs_en
                    v = ifrs_en._row_value(prev, 1)
                    if v is not None:
                        return v
            break
    return None


def extract(doc) -> dict[str, float]:
    from . import from_pdf as _fp
    from . import ifrs_en

    vals = ifrs_en.extract_pdf(doc, item_map=ITEM_MAP_HK, titles=TITLES_HK)

    # **收入合计行经常没有标签**（腾讯：〔此行没有标签〕 6 751,766）。
    # 而且「收入」这个标签太宽泛，会误中「其他收入」「利息收入」（实测取出 1,797）。
    # 所以港股的营业收入**一律用「收入成本上一行」**，没有成本行就保持缺失。
    vals.pop("营业收入", None)
    for pg in doc.pages:
        rows = _fp.page_rows(pg)
        for i, row in enumerate(rows):
            if "收入成本" in str(row[0]):
                for prev in reversed(rows[:i]):
                    v = ifrs_en._row_value(prev, 1)
                    if v is not None and abs(v) > 0:
                        vals["营业收入"] = v
                        break
                break
        if "营业收入" in vals:
            break

    # 小计行也常无标签：用段落结构补。
    #   非流动资产合计 = 「存貨」前最后一行无标签值（1,443,526）
    #   流动资产合计   = 「資產總額」前最后一行无标签值（595,460）
    #   流动负债合计   = 「負債總額」前最后一行无标签值
    for pg in doc.pages:
        rows = _fp.page_rows(pg)
        if "非流动资产合计" not in vals:
            v = _unlabeled_before(rows, "存貨")
            if v is not None:
                vals["非流动资产合计"] = v
        if "流动资产合计" not in vals:
            v = _unlabeled_before(rows, "資產總額")
            if v is not None:
                vals["流动资产合计"] = v
        if "流动负债合计" not in vals:
            v = _unlabeled_before(rows, "負債總額")
            if v is not None:
                vals["流动负债合计"] = v
    return vals
