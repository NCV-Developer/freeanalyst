"""IFRS 英文报表抽取 —— `financials/ifrs_en.extract_text()`。

词表与台湾 MOPS 共用（都是 IFRS 英文标准名），这里只测「PDF 文本 → 数值」。
"""

from __future__ import annotations

import unittest

from financials import ifrs_en

TEXT = """Consolidated Statement of Financial Position
Cash and cash equivalents 235,290,082 210,000,000
Inventories 67,234,559 60,000,000
Total current assets 397,456,191 350,000,000
Total assets 743,784,805 700,000,000
Total liabilities 334,589,784 300,000,000
Total liabilities and equity 743,784,805 700,000,000
Operating revenue 595,965,682 500,000,000
Net operating income (loss) 103,469,695 90,000,000
Profit (loss) 106,117,575 95,000,000
Net cash flows from (used in) operating activities 162,792,872 140,000,000
Net cash flows from (used in) investing activities (37,753,968) (30,000,000)
Adjustments for decrease (increase) in inventories (240,675) (36,871,722)
Total assets
  999,999 888,888
"""


class TestIfrsEn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vals = ifrs_en.extract_text(TEXT)

    def test_basic_items(self):
        self.assertEqual(self.vals["货币资金"], 235290082)
        self.assertEqual(self.vals["存货"], 67234559)
        self.assertEqual(self.vals["营业收入"], 595965682)

    def test_first_occurrence_wins(self):
        # 后面还有一处 Total assets 999,999，不能覆盖第一次的
        self.assertEqual(self.vals["资产总计"], 743784805)

    def test_liabilities_excludes_liabilities_and_equity(self):
        self.assertEqual(self.vals["负债合计"], 334589784)

    def test_used_in_and_parentheses(self):
        self.assertEqual(self.vals["经营活动产生的现金流量净额"], 162792872)
        self.assertEqual(self.vals["投资活动产生的现金流量净额"], -37753968)

    def test_indirect_adjustment_not_inventories(self):
        self.assertEqual(self.vals["存货"], 67234559)

    def test_label_on_its_own_line(self):
        text = "Total assets\n  743,784,805 700,000,000\n"
        self.assertEqual(ifrs_en.extract_text(text)["资产总计"], 743784805)


if __name__ == "__main__":
    unittest.main(verbosity=2)
