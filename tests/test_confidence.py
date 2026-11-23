"""置信度分层 —— `financials/confidence.py`。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace as NS

from financials import confidence as cf


def st(*, audited="已审计", warnings=(), conflicts=(), labels=()):
    from financials import reconcile as _rc
    # 假材料默认 18 个关键科目都有值，否则会被「取到不足一半」这条文件级规则拒掉
    flds = {fld: 1.0 for _n, fld, _k in _rc.ITEMS}
    blk = NS(rows=[NS(label=x, value=1.0) for x in labels], fields=flds)
    return NS(audited=audited, warnings=list(warnings), conflicts=list(conflicts),
              income=blk, balance=blk, cash_flow=blk)


class TestFileGate(unittest.TestCase):
    def test_normal_file_passes(self):
        ok, reasons = cf.file_gate(st(labels=["营业收入", "营业成本"]))
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_scanned_rejected(self):
        ok, reasons = cf.file_gate(st(audited="已审计（全文走 OCR）"))
        self.assertFalse(ok)
        self.assertIn("扫描件", reasons[0])

    def test_financial_rejected(self):
        ok, reasons = cf.file_gate(st(labels=["利息收入", "已赚保费"]))
        self.assertFalse(ok)
        self.assertIn("金融业", reasons[0])


    def test_empty_template_rows_not_financial(self):
        from financials import reconcile as _rc
        # A 股利润表模板里这些科目**每家公司都有，只是空行** —— 不能据此判金融
        blk = NS(rows=[NS(label="已赚保费", value=None),
                       NS(label="手续费及佣金收入", value=None),
                       NS(label="营业收入", value=1.0)],
                 fields={fld: 1.0 for _n, fld, _k in _rc.ITEMS})
        s = NS(audited="已审计", warnings=[], conflicts=[],
               income=blk, balance=blk, cash_flow=blk)
        ok, _ = cf.file_gate(s)
        self.assertTrue(ok, "空模板行不能把普通公司判成金融业")


class TestItemLevel(unittest.TestCase):
    def test_missing_value_is_reject(self):
        level, _ = cf.item_level(st(), "营业收入", None)
        self.assertEqual(level, cf.REJECT)

    def test_conflict_is_low_confidence(self):
        s = st(conflicts=["「资产总计」出现 2 个取值，取首次出现的 405,689,224.24（第 147 页）"])
        level, why = cf.item_level(s, "资产总计", 405689224.24)
        self.assertEqual(level, cf.LOW)
        self.assertIn("证据不一致", why)

    def test_external_mismatch_is_low(self):
        level, _ = cf.item_level(st(), "营业收入", 1.0, verified=False)
        self.assertEqual(level, cf.LOW)

    def test_agreeing_evidence_is_high(self):
        # 规模法与页序一致 → 有依据，不该标低置信（002572 等正常公司都是这种）
        s = st(conflicts=["「资产总计」出现 2 个取值，取**首次出现**的"
                          "（第 145 页资产总计最大，与首次出现一致）343,678,771.19"])
        level, _ = cf.item_level(s, "资产总计", 343678771.19)
        self.assertEqual(level, cf.HIGH)

    def test_clean_value_is_high(self):
        level, _ = cf.item_level(st(), "营业收入", 1.0, verified=True)
        self.assertEqual(level, cf.HIGH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
