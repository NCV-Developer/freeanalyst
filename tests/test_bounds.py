"""页边界修正 —— `financials/bounds.fix_bounds()`。

钉五件事：
    ① 前一页有合并表题 → 左扩
    ② 「母公司现金流量表」**不算**表题，不能挡住左扩（600930 实测）
    ③ 找不到表题页就原样返回，不硬改
    ④ 缺收尾行时右扩，但下一页是母公司表题就停
    ⑤ 现金流整段没有「销售商品」→ 改取直接法页
"""

from __future__ import annotations

import unittest

from financials import bounds


class Pg:
    def __init__(self, n, text):
        self.number = n
        self.raw_text = text


class Doc:
    def __init__(self, texts):
        self.pages = [Pg(i + 1, t) for i, t in enumerate(texts)]


class TestLeftExtend(unittest.TestCase):
    def test_extend_to_consolidated_title(self):
        doc = Doc(["其他", "合并利润表 一、营业总收入", "二、营业总成本"])
        out, notes = bounds.fix_bounds(doc, "income", [3])
        self.assertEqual(out, [2, 3])
        self.assertTrue(notes)

    def test_parent_title_does_not_block(self):
        # p2 印着「母公司现金流量表」，p1 才是合并表首页
        doc = Doc(["合并现金流量表 销售商品", "母公司现金流量表 投资活动", "筹资活动"])
        out, _ = bounds.fix_bounds(doc, "cash_flow", [2, 3])
        self.assertEqual(out, [1, 2, 3])

    def test_no_title_no_change(self):
        doc = Doc(["其他", "其他", "二、营业总成本"])
        out, _ = bounds.fix_bounds(doc, "income", [3])
        self.assertEqual(out, [3])


class TestRightExtend(unittest.TestCase):
    def test_extend_to_closing_row(self):
        doc = Doc(["合并现金流量表 销售商品", "投资活动", "期末现金及现金等价物余额"])
        out, _ = bounds.fix_bounds(doc, "cash_flow", [1])
        self.assertEqual(out, [1, 2, 3])

    def test_stop_at_parent_statement(self):
        doc = Doc(["合并现金流量表 销售商品", "母公司现金流量表 期末现金"])
        out, _ = bounds.fix_bounds(doc, "cash_flow", [1])
        self.assertEqual(out, [1], "母公司表开头不能并进来")


class TestDirectMethod(unittest.TestCase):
    def test_indirect_note_replaced(self):
        doc = Doc(["其他"] * 9 +
                  ["合并现金流量表 销售商品、提供劳务收到的现金", "投资活动 筹资活动 期末现金"] +
                  ["其他"] * 5 + ["将净利润调节为经营活动现金流量 经营性应收项目的减少"])
        out, notes = bounds.fix_bounds(doc, "cash_flow", [16])
        self.assertEqual(out, [10, 11])
        self.assertTrue(any("直接法" in n for n in notes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
