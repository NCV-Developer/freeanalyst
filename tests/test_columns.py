"""列角色判定 —— `ingest/columns.detect()`。

★ 钉四件事：

    ① 沪市 4 列（科目+附注+本期+上期）→ 附注=1，金额=[2,3]
    ② 深市 3 列（科目+本期+上期）      → 附注=None，金额=[1,2]
    ③ 附注写成 `131.00` 也认得出
    ④ 判不出时 `ok=False` —— 调用方退回旧规则，不许硬猜
"""

from __future__ import annotations

import unittest

from ingest import columns


def mk(rows: list[list[str]]) -> list[list[str]]:
    return rows


class TestShanghaiFourColumns(unittest.TestCase):
    def test_note_then_two_amounts(self):
        rows = [
            ["一、营业总收入", "", "172,054,171,890.91", "182,645,203,339.89"],
            ["其中:营业收入", "44", "168,838,102,514.79", "171,234,000,000.00"],
            ["利息收入", "45", "3,216,069,376.12", "4,000,000,000.00"],
            ["已赚保费", "", "", ""],
            ["营业成本", "44", "14,892,277,570.91", "15,100,000,000.00"],
            ["税金及附加", "46", "3,000,000,000.00", "2,800,000,000.00"],
            ["销售费用", "47", "5,000,000,000.00", "4,800,000,000.00"],
            ["管理费用", "48", "4,000,000,000.00", "3,900,000,000.00"],
            ["研发费用", "49", "1,000,000,000.00", "900,000,000.00"],
            ["营业利润", "", "114,808,950,164.24", "120,000,000,000.00"],
            ["净利润", "", "85,310,324,833.67", "88,000,000,000.00"],
        ]
        r = columns.detect(rows)
        self.assertTrue(r.ok)
        self.assertEqual(r.note, 1)
        self.assertEqual(r.amounts, [2, 3])
        self.assertEqual(r.primary, 2)


class TestShenzhenThreeColumns(unittest.TestCase):
    def test_no_note_column(self):
        rows = [
            ["货币资金", "1,234,490,445.13", "2,218,042,519.60"],
            ["应收票据", "3,797,630.62", "22,629,122.05"],
            ["应收账款", "1,182,153,549.52", "1,359,296,476.29"],
            ["应收款项融资", "19,407,346.81", "1,882,614.48"],
            ["预付款项", "53,895,738.36", "60,095,217.36"],
            ["其他应收款", "24,181,724.10", "29,906,544.00"],
            ["存货", "409,740,947.27", "421,000,000.00"],
            ["流动资产合计", "5,166,806,041.09", "5,300,000,000.00"],
            ["非流动资产合计", "8,474,406,046.46", "8,000,000,000.00"],
            ["资产总计", "13,641,212,087.55", "13,000,000,000.00"],
        ]
        r = columns.detect(rows)
        self.assertTrue(r.ok)
        self.assertIsNone(r.note)
        self.assertEqual(r.amounts, [1, 2])
        self.assertEqual(r.primary, 1)


class TestNoteWithDecimals(unittest.TestCase):
    def test_131_00_counts_as_note(self):
        # 统一 4 列：[科目, 附注, 本期, 上期]。附注列大多数行是空的，只有零星几个号。
        rows = [
            ["销售商品收到的现金", "", "183,990,403,487.80", "182,645,203,339.89"],
            ["收到的税费返还", "52", "4,281,303.70", "4,000,000.00"],
            ["收到其他与经营活动有关的现金", "131.00", "184,664,375.14", "170,000,000.00"],
            ["购买商品支付的现金", "", "11,450,143,940.22", "12,000,000,000.00"],
            ["支付给职工的现金", "53", "946,098,324.67", "900,000,000.00"],
            ["支付的各项税费", "54", "490,633,578.83", "480,000,000.00"],
            ["支付其他与经营活动有关的现金", "", "448,434,196.97", "430,000,000.00"],
            ["经营活动净额", "", "631,571,513.56", "600,000,000.00"],
        ]
        r = columns.detect(rows)
        self.assertTrue(r.ok)
        self.assertEqual(r.note, 1, "131.00 这种写法也得认成附注列")
        self.assertEqual(r.amounts, [2, 3])


class TestInconclusive(unittest.TestCase):
    def test_too_few_rows(self):
        self.assertFalse(columns.detect([["a", "1.00", "2.00"]]).ok)

    def test_only_one_amount_column(self):
        rows = [[f"科目{i}", "1,234.00", ""] for i in range(10)]
        self.assertFalse(columns.detect(rows).ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
