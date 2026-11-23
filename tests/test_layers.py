"""分层 —— `bench/layers.classify()` 与矩阵的按层通过率。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace as NS

from bench import coverage, layers


class F:
    """可哈希的假字段（SimpleNamespace 不可哈希，不能当 dict 键）。"""

    def __init__(self, name):
        self.name = name


def fake_st(*, labels=(), audited="已审计", warnings=(), assets=None, ni=None):
    def blk(flds):
        return NS(rows=[NS(label=x, value=1.0) for x in labels], fields=flds)
    inc = blk({F("NET_INCOME"): ni} if ni is not None else {})
    bal = blk({F("TOTAL_ASSETS"): assets} if assets is not None else {})
    return NS(income=inc, balance=bal, cash_flow=blk({}),
              audited=audited, warnings=list(warnings))


class TestBoard(unittest.TestCase):
    def test_boards(self):
        self.assertEqual(layers.board_of("600519"), "沪市主板")
        self.assertEqual(layers.board_of("688316"), "科创板")
        self.assertEqual(layers.board_of("300588"), "创业板")
        self.assertEqual(layers.board_of("002572"), "深市主板")
        self.assertEqual(layers.board_of("832566"), "北交所")
        self.assertEqual(layers.board_of("00939"), "港股")


class TestClassify(unittest.TestCase):
    def test_financial_detected_from_labels(self):
        st = fake_st(labels=["利息收入", "已赚保费"])
        self.assertEqual(layers.classify("601398", st)["行业"], "金融")

    def test_non_financial(self):
        st = fake_st(labels=["营业收入", "营业成本"])
        self.assertEqual(layers.classify("600519", st)["行业"], "非金融")

    def test_scanned_from_ocr_warning(self):
        st = fake_st(audited="已审计（全文走 OCR，数字需人工复核）")
        self.assertEqual(layers.classify("600519", st)["载体"], "扫描件")

    def test_size_and_loss(self):
        st = fake_st(assets=5e11, ni=-1.0)
        d = layers.classify("600519", st)
        self.assertEqual(d["规模"], "大型")
        self.assertEqual(d["盈亏"], "亏损")


class TestMatrixLayers(unittest.TestCase):
    def test_by_layer_groups_cells(self):
        m = coverage.Matrix(cells=[
            coverage.Cell("a.pdf", "cn", "600519", "income", "营业收入",
                          ours=1.0, status=coverage.Status.OK),
            coverage.Cell("b.pdf", "cn", "601398", "income", "营业收入",
                          ours=1.0, status=coverage.Status.MISMATCH),
        ], meta={"a.pdf": {"行业": "非金融"}, "b.pdf": {"行业": "金融"}})
        g = m.by_layer()["行业"]
        self.assertEqual(len(g["非金融"]), 1)
        self.assertEqual(len(g["金融"]), 1)
        text = "\n".join(coverage.render(m))
        self.assertIn("分层通过率", text)
        self.assertIn("金融", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
