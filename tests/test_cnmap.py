"""巨潮全市场代码表 —— `filings.cn_org_map()` / `resolve_cn()` 的代码路径。

★ 钉四件事（都是「静默出错」的高危点）：

    ① 表取不到 → 抛 `LookupError`，**不许返回空 dict**
       空 dict 跟「这家没上市」长得一样，下游会把「请求失败」当成「事实」
    ② 缓存过期 → 仍可用，但必须往 NOTES 写一句（陈旧也是数据，不能装作新的）
    ③ refresh=True 失败 → **不许回落旧缓存**（用户明确要新的，给旧的比报错更坏）
    ④ 代码命中本地表 → 一次网络都不发；表里没有 → 回落 POST，不报假错

全部不联网：monkeypatch `_get` / `_post`，缓存指到临时目录。
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from datasources import filings


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cninfo_org_map.json"
        self._p = mock.patch.object(filings, "_CN_MAP_CACHE", self.cache)
        self._p.start()
        filings.NOTES.clear()

    def tearDown(self):
        self._p.stop()
        filings.NOTES.clear()
        self.tmp.cleanup()

    def _write_cache(self, rows: dict, age_days: float = 0.0):
        self.cache.write_text(json.dumps({
            "fetched_at": time.time() - age_days * 86400,
            "url": "http://example.invalid",
            "rows": rows,
        }, ensure_ascii=False), encoding="utf-8")

    def _raw(self, lst: list[dict]) -> bytes:
        return json.dumps({"stockList": lst}, ensure_ascii=False).encode()


ROWS = {
    "600519": {"orgId": "gssh0600519", "zwjc": "某白酒公司", "pinyin": "gzmt", "category": "A股"},
    "300750": {"orgId": "GD165627", "zwjc": "宁德时代", "pinyin": "ndsd", "category": "A股"},
}


class TestMapFetch(_Base):
    def test_fetches_and_caches(self):
        with mock.patch.object(filings, "_get", return_value=self._raw([
            {"code": "600519", "orgId": "gssh0600519", "zwjc": "某白酒公司"},
        ])):
            m = filings.cn_org_map(refresh=True)
        self.assertEqual(m["600519"]["orgId"], "gssh0600519")
        self.assertTrue(self.cache.is_file(), "取到之后必须落盘，否则每次都重下")

    def test_cache_hit_makes_no_request(self):
        self._write_cache(ROWS)
        with mock.patch.object(filings, "_get", side_effect=AssertionError("不该发请求")):
            m = filings.cn_org_map()
        self.assertEqual(len(m), 2)

    def test_bad_orgid_rows_are_dropped(self):
        with mock.patch.object(filings, "_get", return_value=self._raw([
            {"code": "600519", "orgId": "gssh0600519"},
            {"code": "00700", "orgId": "x"},        # 非 6 位 → 丢
            {"code": "000002", "orgId": ""},        # 无 orgId → 丢
        ])):
            m = filings.cn_org_map(refresh=True)
        self.assertEqual(list(m), ["600519"])


class TestNoSilentEmpty(_Base):
    """① 取不到就抛，不许返回空 dict。"""

    def test_fetch_failure_without_cache_raises(self):
        with mock.patch.object(filings, "_get", side_effect=OSError("网络断了")):
            with self.assertRaises(LookupError) as cm:
                filings.cn_org_map(refresh=True)
        self.assertIn("不等于", str(cm.exception))
        self.assertIn("没上市", str(cm.exception))

    def test_empty_payload_raises(self):
        with mock.patch.object(filings, "_get", return_value=self._raw([])):
            with self.assertRaises(LookupError):
                filings.cn_org_map(refresh=True)


class TestStaleCache(_Base):
    """②③ 过期可用但要说；refresh 失败不许回落。"""

    def test_stale_cache_used_with_note(self):
        self._write_cache(ROWS, age_days=filings._CN_MAP_TTL_DAYS + 5)
        with mock.patch.object(filings, "_get", side_effect=AssertionError("不该重取")):
            m = filings.cn_org_map()
        self.assertEqual(len(m), 2)
        self.assertTrue(any("天前" in n for n in filings.NOTES),
                        f"缓存过期必须说出来，NOTES={filings.NOTES}")

    def test_refresh_failure_falls_back_but_says_so(self):
        self._write_cache(ROWS, age_days=1)
        with mock.patch.object(filings, "_get", side_effect=OSError("503")):
            m = filings.cn_org_map(refresh=True)
        self.assertEqual(len(m), 2)
        self.assertTrue(any("退回用旧表" in n for n in filings.NOTES),
                        f"回落到旧表必须说出来，NOTES={filings.NOTES}")


class TestResolveCn(_Base):
    """④ 代码命中零请求；表里没有回落 POST。"""

    def test_code_hits_local_map_without_network(self):
        self._write_cache(ROWS)
        with mock.patch.object(filings, "_post", side_effect=AssertionError("不该发 POST")):
            ids = filings.resolve_cn("300750")
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0].key, "GD165627")
        self.assertEqual(ids[0].name, "宁德时代")

    def test_missing_code_falls_back_to_post(self):
        self._write_cache(ROWS)
        with mock.patch.object(filings, "_post",
                               return_value=json.dumps([
                                   {"code": "002143", "orgId": "9900003283",
                                    "zwjc": "印纪传媒"}])):
            ids = filings.resolve_cn("002143")
        self.assertEqual([i.key for i in ids], ["9900003283"])

    def test_map_failure_still_tries_post(self):
        """表拿不到时不能直接判死 —— 还有 POST 这条路。"""
        with mock.patch.object(filings, "_get", side_effect=OSError("断网")), \
             mock.patch.object(filings, "_post",
                               return_value=json.dumps([
                                   {"code": "600519", "orgId": "gssh0600519",
                                    "zwjc": "某白酒公司"}])):
            ids = filings.resolve_cn("600519")
        self.assertEqual([i.key for i in ids], ["gssh0600519"])

    def test_name_query_uses_post(self):
        with mock.patch.object(filings, "_post",
                               return_value=json.dumps([
                                   {"code": "600519", "orgId": "gssh0600519",
                                    "zwjc": "某白酒公司"}])):
            ids = filings.resolve_cn("茅台")
        self.assertEqual(ids[0].code, "600519")


if __name__ == "__main__":
    unittest.main(verbosity=2)
