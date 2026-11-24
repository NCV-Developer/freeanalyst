"""台湾 MOPS 财报解析 —— `financials/tw.extract()`。

钉三件事：
    ① 中英双语标签按英文标准名映射（不猜繁体字形）
    ② 「Net cash flows from (used in) operating activities」中间插了 (used in)
    ③ 间接法调整项「Adjustments for decrease (increase) in inventories」
       不能当成资产负债表的存货
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from financials import tw
from ingest.html import extract_html

HTML = """
<html><body>
<table>
<tr><th>項目 Item</th><th>2025</th><th>2024</th></tr>
<tr><td>現金及約當現金 Cash and cash equivalents</td><td>2,767,856,402</td><td>2,127,627,043</td></tr>
<tr><td>存貨 Current inventories</td><td>288,109,485</td><td>287,868,810</td></tr>
<tr><td>資產總計 Total assets</td><td>7,933,023,878</td><td>6,691,938,000</td></tr>
<tr><td>負債總計 Total liabilities</td><td>2,472,228,595</td><td>2,368,362,135</td></tr>
<tr><td>負債及權益總計 Total liabilities and equity</td><td>7,933,023,878</td><td>6,691,938,000</td></tr>
<tr><td>營業利益(損失)Net operating income (loss)</td><td>1,936,091,677</td><td>1,322,053,050</td></tr>
<tr><td>本期淨利(淨損) Profit (loss)</td><td>1,715,396,780</td><td>1,172,431,759</td></tr>
<tr><td>營業活動之淨現金流入(流出)Net cash flows from (used in) operating activities</td>
    <td>2,274,975,625</td><td>1,826,177,068</td></tr>
<tr><td>投資活動之淨現金流入(流出) Net cash flows from (used in) investing activities</td>
    <td>(1,144,393,407)</td><td>(864,842,769)</td></tr>
<tr><td>存貨(增加)減少 Adjustments for decrease (increase) in inventories</td>
    <td>(240,675)</td><td>(36,871,722)</td></tr>
</table>
</body></html>
"""


class TestTwExtract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.NamedTemporaryFile("w", suffix=".htm", delete=False,
                                         encoding="utf-8") as f:
            f.write(HTML)
            cls.path = Path(f.name)
        cls.vals = tw.extract(extract_html(cls.path))

    def test_bilingual_label_mapping(self):
        self.assertEqual(self.vals["货币资金"], 2767856402)
        self.assertEqual(self.vals["资产总计"], 7933023878)

    def test_used_in_does_not_break_cash_flow(self):
        self.assertEqual(self.vals["经营活动产生的现金流量净额"], 2274975625)

    def test_parentheses_mean_negative(self):
        self.assertEqual(self.vals["投资活动产生的现金流量净额"], -1144393407)

    def test_indirect_method_adjustment_not_inventories(self):
        # 存货必须取资产负债表的 288,109,485，不是间接法调整项的 -240,675
        self.assertEqual(self.vals["存货"], 288109485)

    def test_total_liabilities_excludes_equity_total(self):
        self.assertEqual(self.vals["负债合计"], 2472228595)

    def test_net_operating_income(self):
        self.assertEqual(self.vals["营业利润"], 1936091677)

    def test_direct_method_items_not_applicable(self):
        # 台湾是间接法，这两行不存在 —— 不适用，不是解析失败
        self.assertNotIn("销售商品、提供劳务收到的现金", self.vals)
        self.assertIn("销售商品、提供劳务收到的现金", tw.NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
