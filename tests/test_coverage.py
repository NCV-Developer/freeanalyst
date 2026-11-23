"""覆盖矩阵 —— `bench/coverage.py`（全部离线：patch 载入与对账）。

★ 钉四件事：

    ① `NO_TABLE` 必须独立于 `MISSING_OURS` —— 一次表定位失败不能放大成 6 个缺
    ② `UNVERIFIED` 不许进通过率 —— 没有真值谈不上对错
    ③ 文件名认不出市场/代码 → 标未验证，**不许瞎猜**（拿错公司对账比不对账更坏）
    ④ 载入失败要记成格子，不许静静少几个文件
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bench import coverage
from financials import reconcile
from financials.canonical import Field


def stmt(fields: dict | None):
    return None if fields is None else SimpleNamespace(fields=fields, rows=[])


def fake_statements(*, income=..., balance=..., cash_flow=...):
    return SimpleNamespace(
        income=stmt(income) if income is not ... else None,
        balance=stmt(balance) if balance is not ... else None,
        cash_flow=stmt(cash_flow) if cash_flow is not ... else None,
    )


def item(name, ours, theirs, verdict, note=""):
    return reconcile.Item(name, ours, theirs, verdict, note=note)


class TestInfer(unittest.TestCase):
    def test_a_share(self):
        self.assertEqual(coverage.infer(Path("600519_2026-04-17_annual.pdf")),
                         ("cn", "600519"))

    def test_hk(self):
        self.assertEqual(coverage.infer(Path("00939_2026-04-27_annual.pdf")),
                         ("hk", "00939"))

    def test_unrecognised_returns_empty(self):
        self.assertEqual(coverage.infer(Path("annual-report-2025.pdf")), ("", ""))
        self.assertEqual(coverage.infer(Path("R2.htm")), ("", ""))

    def test_us_form_recognised_without_cik(self):
        """看得出是美股申报，但文件名里没有 CIK —— 市场对、代码空。
        代码空的后果是这些格子落 UNVERIFIED，**不会拿错的对照去对账**。"""
        self.assertEqual(coverage.infer(Path("AAPL_10-K.htm")), ("us", ""))

    def test_us_form_beats_six_digits(self):
        """`320193_..._10-K.htm` 里的 320193 是 **CIK**，不是 A 股代码。

        判成 cn 的后果：拿 A 股的对照源去对美股 —— 会把对的判成错的。
        """
        self.assertEqual(coverage.infer(Path("320193_2025-10-31_10-K.htm")),
                         ("us", "320193"))
        self.assertEqual(coverage.infer(Path("1840425_2026-03-31_10-K_x.htm")),
                         ("us", "1840425"))


class TestHtmlIsUnsupportedNotFailedLoad(unittest.TestCase):
    def test_html_marked_unsupported(self):
        """HTML 三表载入还没做 —— 这是「格式不支持」，不是「文件坏了」。"""
        with mock.patch.object(coverage, "load_pdf_statements",
                               side_effect=AssertionError("不该走 PDF 载入")):
            m = coverage.build([Path("320193_2025-10-31_10-K.htm")], year=2025)
        self.assertTrue(all(c.status is coverage.Status.UNSUPPORTED for c in m.cells))
        self.assertTrue(any("还没做" in n for n in m.notes))
        self.assertEqual(m.verifiable(), [], "不支持的格式不算能验")


class TestStatuses(unittest.TestCase):
    def _build(self, statements, report_items, files=("600519_a.pdf",)):
        paths = [Path(f) for f in files]
        with mock.patch.object(coverage, "load_pdf_statements", return_value=statements), \
             mock.patch.object(coverage.reconcile, "compare",
                               return_value=SimpleNamespace(items=report_items,
                                                            notes=[], period="20251231")):
            return coverage.build(paths, year=2025)

    def test_match_and_rounding(self):
        st = fake_statements(income={Field.REVENUE: 1000.0})
        m = self._build(st, [item("营业收入", 1000.0, 1000.0, reconcile.Verdict.MATCH)])
        by = {c.item: c for c in m.cells}
        self.assertIs(by["营业收入"].status, coverage.Status.OK)

    def test_mismatch(self):
        st = fake_statements(income={Field.REVENUE: 61.0})
        m = self._build(st, [item("营业收入", 61.0, 1000.0, reconcile.Verdict.MISMATCH)])
        by = {c.item: c for c in m.cells}
        self.assertIs(by["营业收入"].status, coverage.Status.MISMATCH)

    def test_year_shift_counts_as_mismatch(self):
        st = fake_statements(income={Field.REVENUE: 900.0})
        m = self._build(st, [item("营业收入", 900.0, 1000.0,
                                  reconcile.Verdict.YEAR_SHIFT)])
        by = {c.item: c for c in m.cells}
        self.assertIs(by["营业收入"].status, coverage.Status.MISMATCH)

    def test_missing_ours(self):
        st = fake_statements(income={})
        m = self._build(st, [item("营业收入", None, 1000.0,
                                  reconcile.Verdict.OURS_MISSING)])
        by = {c.item: c for c in m.cells}
        self.assertIs(by["营业收入"].status, coverage.Status.MISSING_OURS)

    def test_theirs_missing_is_unverified_not_failure(self):
        st = fake_statements(income={Field.REVENUE: 1000.0})
        m = self._build(st, [item("营业收入", 1000.0, None,
                                  reconcile.Verdict.THEIRS_MISSING)])
        by = {c.item: c for c in m.cells}
        self.assertIs(by["营业收入"].status, coverage.Status.UNVERIFIED)
        self.assertNotIn(by["营业收入"].status, coverage.FAILED)


class TestNoTableIsSeparate(unittest.TestCase):
    def test_whole_statement_missing_is_one_status_not_six(self):
        """现金流量表没定位到 → 6 格都是 NO_TABLE，不是 6 格 MISSING_OURS。"""
        st = fake_statements(income={Field.REVENUE: 1000.0}, balance={},
                             cash_flow=None)
        n_cf = sum(1 for _, _, k in reconcile.ITEMS if k == "cash_flow")
        self.assertEqual(n_cf, 6)
        with mock.patch.object(coverage, "load_pdf_statements", return_value=st), \
             mock.patch.object(coverage.reconcile, "compare",
                               return_value=SimpleNamespace(items=[], notes=[],
                                                            period="")):
            m = coverage.build([Path("600519_a.pdf")], year=2025)
        cf = [c for c in m.cells if c.statement == "cash_flow"]
        self.assertEqual(len(cf), n_cf)
        self.assertTrue(all(c.status is coverage.Status.NO_TABLE for c in cf))
        self.assertTrue(any("别算成" in c.note for c in cf))


class TestUnverifiedExcludedFromPassRate(unittest.TestCase):
    def test_pass_rate_ignores_unverified(self):
        st = fake_statements(income={Field.REVENUE: 1000.0}, balance={},
                             cash_flow={})
        with mock.patch.object(coverage, "load_pdf_statements", return_value=st), \
             mock.patch.object(coverage.reconcile, "compare",
                               return_value=SimpleNamespace(
                                   items=[item("营业收入", 1000.0, 1000.0,
                                               reconcile.Verdict.MATCH)],
                                   notes=[], period="20251231")):
            m = coverage.build([Path("600519_a.pdf")], year=2025)
        s = coverage.summarise(m)["cn"]
        self.assertEqual(s["status"]["ok"] if "status" in s else s["ok"], 1)
        self.assertEqual(s["pass_rate"], 1.0, "只算能验的格子")
        self.assertGreater(s["unverified"], 0)


class TestLoadFailureIsRecorded(unittest.TestCase):
    def test_crash_becomes_cells_not_silence(self):
        with mock.patch.object(coverage, "load_pdf_statements",
                               side_effect=OSError("坏文件")):
            m = coverage.build([Path("600519_a.pdf")], year=2025)
        self.assertEqual(len(m.cells), len(reconcile.ITEMS))
        self.assertTrue(all(c.status is coverage.Status.LOAD_FAILED for c in m.cells))
        self.assertTrue(any("载入失败" in n for n in m.notes))

    def test_no_code_means_unverified(self):
        st = fake_statements(income={Field.REVENUE: 1000.0}, balance={},
                             cash_flow={})
        with mock.patch.object(coverage, "load_pdf_statements", return_value=st):
            m = coverage.build([Path("annual-report-2025.pdf")], year=2025)
        self.assertTrue(all(c.status is coverage.Status.UNVERIFIED for c in m.cells))
        self.assertTrue(any("认不出市场" in n for n in m.notes))


class TestLayer2ItemCoverage(unittest.TestCase):
    """第二层：**取到没取到，不需要真值**。

    这一层之前被我漏了 —— 只有 A 股有"数值对错"的数，就以为别的市场什么都没测。
    其实"这一格填上值了没有"跟有没有对照源无关。
    """

    def test_present_vs_expected(self):
        cells = [
            coverage.Cell("a.pdf", "hk", "00939", "income", "营业收入",
                          ours=100.0, status=coverage.Status.UNVERIFIED),
            coverage.Cell("a.pdf", "hk", "00939", "income", "营业成本",
                          ours=None, status=coverage.Status.UNVERIFIED),
            coverage.Cell("b.pdf", "hk", "01810", "balance", "资产总计",
                          ours=None, status=coverage.Status.NO_TABLE),
            coverage.Cell("c.htm", "hk", "x", "income", "营业收入",
                          ours=None, status=coverage.Status.UNSUPPORTED),
        ]
        s = coverage.summarise(coverage.Matrix(cells=cells))["hk"]
        self.assertEqual(s["present"], 1, "只有一格真的取到值")
        self.assertEqual(s["expected"], 2, "NO_TABLE / UNSUPPORTED 不算有机会取")
        self.assertEqual(s["item_rate"], 0.5)

    def test_render_shows_item_column(self):
        m = coverage.Matrix(cells=[
            coverage.Cell("a.pdf", "hk", "00939", "income", "营业收入",
                          ours=1.0, status=coverage.Status.UNVERIFIED)])
        text = "\n".join(coverage.render(m))
        self.assertIn("科目取到", text)
        self.assertIn("1/1", text)

    def test_companies_not_files(self):
        """**公司数 ≠ 份数。** 港股 6 份材料里 5 份是同一家银行 ——
        按份数统计会得出「港股科目取到率 24%」这种被样本带偏的结论。"""
        cells = [
            coverage.Cell("00939_a.pdf", "hk", "00939", "income", "营业收入"),
            coverage.Cell("00939_b.pdf", "hk", "00939", "income", "营业成本"),
            coverage.Cell("01810_c.pdf", "hk", "01810", "income", "营业收入"),
        ]
        s = coverage.summarise(coverage.Matrix(cells=cells))["hk"]
        self.assertEqual(s["files"], 3)
        self.assertEqual(s["companies"], 2)


class TestCsvAndGate(unittest.TestCase):
    def test_csv_roundtrip(self):
        m = coverage.Matrix(cells=[
            coverage.Cell("a.pdf", "cn", "600519", "income", "营业收入",
                          1.0, 2.0, coverage.Status.MISMATCH, "差"),
        ])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.csv"
            coverage.write_csv(m, p)
            text = p.read_text(encoding="utf-8")
        self.assertIn("file,market,code,statement,item,ours,truth,status,note", text)
        self.assertIn("取不对", text)

    def test_gate(self):
        clean = coverage.Matrix()
        dirty = coverage.Matrix(cells=[
            coverage.Cell("a.pdf", "cn", "600519", "income", "营业收入",
                          1.0, 2.0, coverage.Status.MISMATCH)])
        self.assertTrue(coverage.gate(clean))
        self.assertFalse(coverage.gate(dirty))
        self.assertTrue(coverage.gate(dirty, max_bad=1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
