"""下载财报全文 —— 按**公司名称或代码**、按**报告期或披露窗口**，三个市场。

## 一句话

```python
find("某白酒公司", period=2025)                    # A 股
find("建設銀行", filed="2025-01-01:2026-12-31")   # 港股
find("Western Union", form="10-K", period=2024)  # 美股
```

## ★ 「时间段」有两个意思，**必须分开**

    报告期 period   2025      → 这份报告讲的是哪一年（2025-12-31 为期末）
    披露日 filed    2026-04-17 → 市场第一次看到它的那天

**2025 年报的披露日是 2026-04-17。** 所以「2025 年的财报」这句话有两种读法，
答案完全相反：按披露日筛 2025 会拿到 **2024 年报** ✗

所以接口分两个参数，**不许混**：`--period` 和 `--filed`。
混用的后果不是报错，是**静静地给你一份看起来正常的财报**。

## ★ 名称解析的规矩

    唯一精确匹配 → 直接用
    多个候选     → **全部列出来，让用户选，不自动挑**
    一个没有     → 报「没查到」，给近似建议

自动挑一个"最像的"是最危险的做法 —— 名称相近的两家公司财报结构完全不同，
挑错了后面所有数字都是另一家的。

## 三个市场的实测状态

    美股  ✓ 列表 + PDF      SEC EDGAR，能严格还原「当时披露的版本」
    A股   ✓ 列表 + PDF      巨潮（查询必须 POST）
    港股  ✓ 列表 + PDF      披露易（今天刚打通）

## 四个踩过的坑

**① 东财的 PDF CDN 有 JS 反爬。** `pdf.dfcfw.com` 返回 988 字节的混淆脚本，
`Content-Type` 却写着 `application/pdf` —— 骗人的。所以必须验文件头。

**② 巨潮的查询和编号查询都只认 POST。** GET 直接 500。

**③ 披露易的 `type=A` 是开关，不是 `type=<代码>`。** 少了它返回 2 字节空响应 ✗
   而且两个接口格式不同：`prefix.do` 是 JSONP（结尾带分号），
   `titleSearchServlet.do` 是纯 JSON，且 `result` 是**字符串**要二次 loads。

**④ 披露易搜中文名会被「窝轮」淹没。** 搜「騰訊」前 10 条全是
   「騰訊中銀七三購A」这类衍生品，真公司排在后面 ✗ —— 必须按代码前导 0 过滤。

**⑤ 披露易只认繁体。** 「腾讯」0 条，「騰訊」才有 → 查询前要转繁体。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import net

#: 这个模块要用到的主机。**逐个显式列出，不用通配。**
HOSTS = frozenset({
    "www.cninfo.com.cn",            # 巨潮：查询（POST）
    "static.cninfo.com.cn",         # 巨潮：PDF 文件服务器
    "np-anotice-stock.eastmoney.com",   # 东财：港股公告列表（备源）
    "searchapi.eastmoney.com",      # 东财：名称 → 代码（三个市场共用的入口）
    "www1.hkexnews.hk",             # 披露易：港股主源
    "data.sec.gov",                 # SEC：submissions
    "www.sec.gov",                  # SEC：归档文件 + 代码表
})

UA = "FreeAnalyst/0.60 (noreply@newchapterventures.com)"
_HK_REF = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh"


class FailReason(Enum):
    """**失败必须分类。** 三种原因报同一句「没找到」，只能靠猜 ✗"""

    RETIRED = "该公司已退市或停止披露 —— 不是错误，是事实"
    NOT_FILED = "该市场该表种它不报（例如 ADR 报 20-F，不报 10-K）"
    BLOCKED = "源端反爬或地域限制 —— 换源，或人工下载"
    MISSING_ID = "需要内部编号才能查（orgId / stockId / CIK）"
    NO_MATCH = "查到了公司，但没有符合条件的报告"
    AMBIGUOUS = "名称匹配到多家，必须你来选"
    NETWORK = "网络错误 —— 可以重试"


@dataclass
class Identity:
    """一家公司。`key` 是那个源要的内部编号。"""

    market: str
    code: str
    name: str
    key: str = ""
    exchange: str = ""
    note: str = ""


@dataclass
class Filing:
    market: str
    code: str
    title: str
    filed_at: str = ""          # 披露日 YYYY-MM-DD
    period: str = ""            # 报告期 YYYY-MM-DD（拿得到才有）
    url: str = ""
    kind: str = "other"
    fmt: str = "pdf"
    source: str = ""            # cninfo / hkexnews / sec / eastmoney
    note: str = ""


@dataclass
class Result:
    """一次查询的完整结果 —— **成功和失败都保留**，不静默丢。"""

    query: str
    identities: list[Identity] = field(default_factory=list)
    filings: list[Filing] = field(default_factory=list)
    failures: list[tuple[FailReason, str]] = field(default_factory=list)

    def fail(self, r: FailReason, msg: str) -> None:
        self.failures.append((r, msg))


# ══════════════════════════ 出网 ══════════════════════════

def _q(params: dict, purpose: str) -> net.PublicQuery:
    return net.PublicQuery(params=params, purpose=purpose, extra_hosts=HOSTS)


def _get(url: str, params: dict, purpose: str,
         headers: dict | None = None) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    return net.guarded_get(url, _q(params, purpose), headers=h)


def _post(url: str, params: dict, purpose: str, referer: str = "") -> bytes:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if referer:
        h["Referer"] = referer
    return net.guarded_post(url, _q(params, purpose), headers=h)


def _jsonp(raw: bytes) -> dict:
    """披露易两个接口格式不同：JSONP（结尾带 `;`）和纯 JSON。"""
    s = raw.decode("utf-8", "replace").strip()
    if not s:
        raise ValueError("空响应（多半参数不对）")
    if s.startswith("{") or s.startswith("["):
        return json.loads(s)
    m = re.search(r"callback\((.*?)\)\s*;?\s*$", s, re.S)
    if not m:
        raise ValueError(f"既不是 JSON 也不是 JSONP：{s[:120]!r}")
    return json.loads(m.group(1))


def _to_hant(s: str) -> str:
    """简体 → 繁体。**披露易只认繁体**，「腾讯」查不到，「騰訊」才有。"""
    try:
        import zhconv
        return zhconv.convert(s, "zh-hant")
    except Exception:                              # noqa: BLE001
        return s


# ══════════════════════════ 名称 → 候选 ══════════════════════════

#: 东财 suggest 返回的「证券类型」→ 我们的市场。**不在表里的（指数/板块/期货/期权）直接丢** ✗
_EM_MARKET = {
    "港股": "hk",
    "沪A": "cn", "深A": "cn", "京A": "cn", "沪B": "cn", "深B": "cn",
    "美股": "us",
}


def resolve_suggest(query: str, limit: int = 10) -> list[Identity]:
    """**名称 → 候选公司的正路：东财 suggest。**

    为什么不用各市场自己的搜索接口：
      ① 披露易 `prefix.do` 搜中文名会被**窝轮淹没** —— 搜「騰訊」前 10 条
         全是「騰訊中銀七三購A」这类衍生品，真公司 00700 排在后面 ✗
      ② 披露易**只认繁体**，「腾讯」0 条 ✗
      ③ 巨潮 topSearch 会把同一家公司的多个市场代码都返回

    东财 suggest 一次就给出「代码 + 市场 + 名称」，**三个市场用同一个入口** ✓
    实测：「腾讯」→ 00700 港股腾讯控股（第一条就是对的）。

    ⚠️ 它**只认简体** —— 繁体要先转。
    """
    try:
        import zhconv
        kw = zhconv.convert(str(query).strip(), "zh-cn")
    except Exception:                              # noqa: BLE001
        kw = str(query).strip()
    try:
        raw = _get("https://searchapi.eastmoney.com/api/suggest/get",
                   {"input": kw, "type": "14", "count": "20"},
                   f"查 {query} 的代码")
        data = (json.loads(raw).get("QuotationCodeTable") or {}).get("Data") or []
    except Exception:                              # noqa: BLE001
        return []
    out: list[Identity] = []
    for d in data:
        mk = _EM_MARKET.get(str(d.get("SecurityTypeName") or "").strip())
        if not mk:
            continue                               # 指数、板块、期货、期权 → 丢
        code = str(d.get("Code") or "").strip()
        if not code:
            continue
        # **港股还要剔窝轮/牛熊证/人民币柜台。**
        # 东财把窝轮的类型也标成「港股」✗ —— 搜「小米」会返回
        # 「小米信证七二沽A」这类衍生品。判据：正股代码一律 0 开头
        # （00700 / 00939 / 01810 / 09988），窝轮是 1xxxx / 2xxxx，
        # 人民币柜台是 8xxxx ✗
        if mk == "hk":
            c5 = code.zfill(5)
            if not c5.startswith("0"):
                continue
            code = c5
        out.append(Identity(mk, code.upper(), str(d.get("Name") or ""),
                            exchange=str(d.get("SecurityTypeName") or "")))
        if len(out) >= limit:
            break
    # 精确代码命中的排最前
    q = str(query).strip().upper()
    out.sort(key=lambda x: x.code != q and not x.code.lstrip("0") == q.lstrip("0"))
    return out


_CN_ORG: dict[str, str] = {}          # code -> orgId
_HK_SID: dict[str, str] = {}          # code -> stockId
_US_CIK: dict[str, str] = {}          # ticker -> cik
_US_NAMES: list[tuple[str, str, str, str]] = []   # (ticker, cik, name, exchange)


# ══════════════ 巨潮全市场代码表（一次拿全，取代逐家 POST） ══════════════
#
# ## 为什么必须是这张表，不能自己拼 orgId
#
# 实测这批 code → orgId 的对应关系**没有任何统一规则**：
#
#     000001 → gssz0000001        600519 → gssh0600519
#     300750 → GD165627           688316 → nssc1000259
#     601398 → jjxt0000019        920001 → 9900037542
#
# 「gs + 市场前缀 + 0 + 代码」这个想当然的规则只对部分主板成立；
# 创业板、科创板、北交所，甚至部分银行（601398 工商银行）全都不同 ✗。
# 靠正则拼，拼出来的 orgId 会让查询返回空 —— 而空列表跟「这家没发年报」长得一样。
#
# ## 为什么值得换掉原来的逐家 POST
#
# 原来每个代码查一次 `topSearch/query`：三市场各 10 家就是 30 次，
# 全市场筛选是几千次，还会撞限流。这张表**一次 592KB 全给**（2026-10-26 实测
# HTTP 200 · 592,685 字节 · 6,261 家），并且**不依赖那个 POST 端点活着** ——
# 查询接口挂了，取数就整个停摆，这是单点。
_CN_MAP_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
_CN_MAP_TTL_DAYS = 30
_CN_MAP_CACHE = Path(__file__).resolve().parent.parent / "cache" / "cninfo_org_map.json"

#: 这次调用里值得报出来的话（缓存过期、降级到旧表等）。**不静默。**
NOTES: list[str] = []


def cn_org_map(refresh: bool = False) -> dict[str, dict[str, str]]:
    """巨潮全市场 ``code -> {orgId, 简称, 拼音, 类别}``。带磁盘缓存（`cache/` 不入库）。

    三条纪律：

    ① **取不到就抛 `LookupError`** —— 不许返回空 dict。空 dict 跟
       「这家没上市」长得一模一样，下游会把「请求没成功」当成「事实」✗
    ② **缓存过期仍可用，但必须往 `NOTES` 里写一句** —— 陈旧数据也是数据，
       不能装作是新的；也不能因为它旧就直接罢工（那会把可用的工具变成不可用）
    ③ `refresh=True` 时**一定重取**，失败也不回落旧缓存 —— 用户明确要新的，
       悄悄给旧的比报错更坏
    """
    now = time.time()
    if not refresh and _CN_MAP_CACHE.is_file():
        try:
            blob = json.loads(_CN_MAP_CACHE.read_text(encoding="utf-8"))
            rows = blob.get("rows") or {}
            if isinstance(rows, dict) and rows:
                age_days = (now - float(blob.get("fetched_at") or 0)) / 86400
                if age_days <= _CN_MAP_TTL_DAYS:
                    return rows
                NOTES.append(
                    f"巨潮代码表是 {age_days:.0f} 天前的（阈值 {_CN_MAP_TTL_DAYS} 天），"
                    f"新上市的代码可能查不到；要最新就 `refresh=True` 重取。")
                return rows
        except (OSError, ValueError, TypeError):
            pass          # 缓存坏了就当没有 —— 下面重取

    try:
        raw = _get(_CN_MAP_URL, {}, "取巨潮全市场代码表")
        blob = json.loads(raw.decode("utf-8", "replace"))
        lst = blob.get("stockList") or []
    except Exception as exc:                           # noqa: BLE001
        # 重取失败：能退回旧缓存就退，但**必须说出来**
        if _CN_MAP_CACHE.is_file():
            try:
                blob = json.loads(_CN_MAP_CACHE.read_text(encoding="utf-8"))
                rows = blob.get("rows") or {}
                if rows:
                    NOTES.append(
                        f"巨潮代码表重取失败（{type(exc).__name__}: {exc}），"
                        f"退回用旧表（{len(rows)} 家）。新代码可能查不到。")
                    return rows
            except (OSError, ValueError):
                pass
        raise LookupError(
            f"巨潮全市场代码表取不到，本地也没有缓存可用"
            f"（{type(exc).__name__}: {exc}）。"
            f"**这不等于「这家没上市」** —— 是这张表没拿到。") from exc

    rows: dict[str, dict[str, str]] = {}
    for r in lst:
        code = str(r.get("code") or "")
        org = str(r.get("orgId") or "")
        if not re.fullmatch(r"\d{6}", code) or not org:
            continue
        rows[code] = {
            "orgId": org,
            "zwjc": str(r.get("zwjc") or ""),
            "pinyin": str(r.get("pinyin") or ""),
            "category": str(r.get("category") or ""),
        }
    if not rows:
        raise LookupError("巨潮代码表拿到了但是空的 —— 解析口径变了，去核原始响应")

    try:
        _CN_MAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CN_MAP_CACHE.write_text(
            json.dumps({"fetched_at": now, "url": _CN_MAP_URL, "rows": rows},
                       ensure_ascii=False),
            encoding="utf-8")
    except OSError as exc:
        NOTES.append(f"代码表拿到了但没存成缓存（{exc}）—— 下次还要再下一次。")
    return rows


def resolve_cn(query: str, limit: int = 10) -> list[Identity]:
    """A 股：先查本地全市场表（代码命中就零请求），否则巨潮 topSearch 按名称模糊查。

    ⚠️ topSearch **只认 POST**（GET 直接抛错）。
    """
    q = str(query).strip()

    # ① 6 位纯代码 → 本地表，一次网络都不用发
    if re.fullmatch(r"\d{6}", q):
        try:
            hit = cn_org_map().get(q)
        except LookupError:
            hit = None                       # 表拿不到 → 落到下面的 POST 兜底
        if hit:
            _CN_ORG[q] = hit["orgId"]
            return [Identity("cn", q, hit.get("zwjc", ""), hit["orgId"])]
        # 表里没有（已退市 / 更名 / 新上市）→ 继续走 POST，可能还查得到

    try:
        raw = _post("http://www.cninfo.com.cn/new/information/topSearch/query",
                    {"keyWord": query, "maxNum": str(limit)},
                    f"查 {query} 的巨潮编号", referer="http://www.cninfo.com.cn/")
        rows = json.loads(raw) or []
    except Exception:                              # noqa: BLE001
        return []
    out = []
    for r in rows:
        code = str(r.get("code") or "")
        # 巨潮会把同一家公司的多个市场代码都返回（如 300750 / 03750）✗
        if not re.fullmatch(r"\d{6}", code):
            continue
        org = str(r.get("orgId") or "")
        if not org:
            continue
        _CN_ORG[code] = org
        out.append(Identity("cn", code, str(r.get("zwjc") or ""), org))
    return out[:limit]


def resolve_hk(query: str, limit: int = 10) -> list[Identity]:
    """港股：披露易 prefix.do 按**代码**查 stockId（这是可靠的用法）。

    ⚠️ 按名称查会被窝轮淹没，所以名称解析走 `resolve_suggest`，
    这里只负责「代码 → stockId」。
    """
    code = str(query).strip().upper().replace(".HK", "").zfill(5)
    try:
        j = _jsonp(_get("https://www1.hkexnews.hk/search/prefix.do",
                        {"callback": "callback", "lang": "ZH", "name": code,
                         "market": "SEHK", "type": "A"},
                        f"查 {code} 的披露易编号", {"Referer": _HK_REF}))
    except Exception:                              # noqa: BLE001
        return []
    out = []
    for x in (j.get("stockInfo") or []):
        c = str(x.get("code") or "").zfill(5)
        if c != code:                              # **精确匹配，不猜**
            continue
        sid = str(x.get("stockId") or "")
        if sid:
            _HK_SID[c] = sid
            out.append(Identity("hk", c, str(x.get("name") or ""), sid))
    return out[:limit]


def _load_us_index() -> list[tuple[str, str, str, str]]:
    """SEC 全量代码表（带交易所）。**本地缓存。**"""
    global _US_NAMES
    if _US_NAMES:
        return _US_NAMES
    try:
        raw = _get("https://www.sec.gov/files/company_tickers_exchange.json",
                   {}, "取美股全量代码表")
        j = json.loads(raw)
        f = j["fields"]
        ti, ci, ni, ei = (f.index("ticker"), f.index("cik"),
                          f.index("name"), f.index("exchange"))
        _US_NAMES = [(str(r[ti]), str(r[ci]), str(r[ni]), str(r[ei] or ""))
                     for r in j["data"]]
        for t, c, _n, _e in _US_NAMES:
            _US_CIK[t.upper()] = c
    except Exception:                              # noqa: BLE001
        _US_NAMES = []
    return _US_NAMES


def resolve_us(query: str, limit: int = 10) -> list[Identity]:
    """美股：本地按代码或名称模糊匹配。**主流交易所优先**（OTC 是小盘聚集地）。"""
    q = str(query).upper().strip()
    out: list[Identity] = []
    idx = _load_us_index()
    for t, c, n, e in idx:
        if t.upper() == q:
            out.append(Identity("us", t, n, c, e))
            break
    ql = str(query).lower()
    for t, c, n, e in idx:
        if len(out) >= limit * 2:
            break
        if ql and ql in n.lower() and not any(o.code == t for o in out):
            out.append(Identity("us", t, n, c, e))
    out.sort(key=lambda x: (x.exchange not in ("NYSE", "Nasdaq"), x.code))
    return out[:limit]


RESOLVERS = {"cn": resolve_cn, "hk": resolve_hk, "us": resolve_us}


def guess_market(query: str) -> str:
    """按样子猜市场。**猜不出就返回空，由调用方决定要不要问人。**"""
    q = str(query).strip()
    if re.fullmatch(r"\d{6}", q):
        return "cn"
    if re.fullmatch(r"\d{1,5}", q):
        return "hk"
    if re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,6}", q):
        return "us"
    return ""                                      # 中文名 / 全称 → 走 suggest


def resolve(query: str, market: str = "", limit: int = 10) -> list[Identity]:
    """代码或名称 → 候选公司。

    **先走东财 suggest**（三个市场一个入口，名称解析最可靠），
    查不到再按代码形态回落到各市场自己的接口。
    """
    q = str(query).strip()
    if not q:
        return []

    ids = resolve_suggest(q, limit)
    if market:
        ids = [i for i in ids if i.market == market] or ids
    elif ids:
        # **繁体名 → 港股优先。** 用户写「建設銀行」多半指 H 股，
        # 写「建设银行」多半指 A 股 —— 同一家公司两个市场都有，不该随机挑。
        try:
            import zhconv as _zc
            if _zc.convert(q, "zh-hans") != q:      # 本身是繁体
                ids.sort(key=lambda i: i.market != "hk")
            else:
                ids.sort(key=lambda i: i.market == "hk")
        except Exception:                          # noqa: BLE001
            pass

    if ids:
        # 补上各源要的内部编号（orgId / stockId / cik）
        for i in ids:
            try:
                if i.market == "cn" and not i.key:
                    hit = resolve_cn(i.code, 3)
                    i.key = hit[0].key if hit else ""
                elif i.market == "hk" and not i.key:
                    hit = resolve_hk(i.code, 3)
                    i.key = hit[0].key if hit else ""
                elif i.market == "us" and not i.key:
                    i.key = _US_CIK.get(i.code.upper(), "")
            except Exception:                      # noqa: BLE001
                continue
        return ids

    # suggest 没结果 → 按代码形态回落到原生接口
    mk = market or guess_market(q)
    order = [mk] if mk else ["cn", "hk", "us"]
    out: list[Identity] = []
    for m in order:
        try:
            out.extend(RESOLVERS[m](q, limit))
        except Exception:                          # noqa: BLE001
            continue
    return out[:limit]


# ══════════════════════════ A 股 · 巨潮 ══════════════════════════

CN_KINDS = {
    "annual": ("category_ndbg_szsh", ("年度报告", "年報")),
    "interim": ("category_bndbg_szsh", ("半年度报告", "中期报告")),
    "quarterly": ("category_yjdbg_szsh", ("季度报告",)),
}


def _cn_org(code: str) -> str:
    code6 = re.sub(r"\.(SH|SZ|BJ)$", "", str(code).upper()).zfill(6)
    if code6 in _CN_ORG:
        return _CN_ORG[code6]
    hits = resolve_cn(code6, 5)
    if not hits:
        raise LookupError(f"查不到 {code6} 的巨潮编号")
    return hits[0].key


def find_cn(code: str, kind: str = "annual", period: int | None = None,
            filed: tuple[str, str] | None = None, limit: int = 20) -> list[Filing]:
    """巨潮：按报告期或披露窗口取 A 股公告。

    ⚠️ **报告期和披露日不是一回事**。2025 年报是 2026-04-17 披露的 ——
    用 `filed_at.startswith("2025")` 去筛会一条不剩 ✗
    """
    code6 = re.sub(r"\.(SH|SZ|BJ)$", "", str(code).upper()).zfill(6)
    org = _cn_org(code6)
    category, _ = CN_KINDS.get(kind, CN_KINDS["annual"])

    if filed:
        span = f"{filed[0]}~{filed[1]}"
    elif period:
        # 报告期 N 年的报告，总在 N+1 年披露 → 窗口取到次年末
        span = f"{period - 1}-01-01~{period + 1}-12-31"
    else:
        now = time.localtime().tm_year
        span = f"{now - 3}-01-01~{now}-12-31"

    raw = _post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                {"pageNum": "1", "pageSize": str(max(limit * 3, 30)),
                 "column": "szse", "tabName": "fulltext", "plate": "",
                 "stock": f"{code6},{org}", "searchkey": "", "secid": "",
                 "category": category, "trade": "", "seDate": span,
                 "sortName": "", "sortType": "", "isHLtitle": "true"},
                f"查 {code6} 的{kind}", referer="http://www.cninfo.com.cn/")
    j = json.loads(raw)
    out: list[Filing] = []
    for a in (j.get("announcements") or []):
        adj = a.get("adjunctUrl") or ""
        title = a.get("announcementTitle") or ""
        if not adj:
            continue
        if period and str(period) not in title:
            continue                               # 年份靠标题判，不靠披露日
        ts = a.get("announcementTime")
        when = (time.strftime("%Y-%m-%d", time.localtime(ts / 1000))
                if isinstance(ts, (int, float)) else "")
        out.append(Filing(
            market="cn", code=code6, title=title, kind=kind, filed_at=when,
            period=f"{period}-12-31" if period else "",
            url=f"http://static.cninfo.com.cn/{adj}",
            fmt="pdf", source="cninfo",
            note="巨潮给的是最新一版，不是披露当日的快照"))
        if len(out) >= limit:
            break
    return out


# ══════════════════════════ 港股 · 披露易 ══════════════════════════

HK_KINDS = {
    # ⚠️ **标题写法五花八门**：小米写「2025年度報告」，建行写「二零二五年年報」，
    # 还有「Annual Report」。所以关键词要全，且**优先看 LONG_TEXT 的规范类别**
    # （`[財務報表／環境、社會及管治資料 - [年報]]`）—— 那个是交易所打的标，最可靠。
    "annual": ("年報", "年报", "年度報告", "年度报告", "Annual Report"),
    "interim": ("中期報告", "中期报告", "中期業績", "半年度報告", "半年度报告",
                "Interim Report"),
    "quarterly": ("季度報告", "季度报告", "季度業績", "Quarterly Report"),
}

HK_TAGS = {                                   # LONG_TEXT 里的规范类别 → kind
    "annual": ("[年報]", "[年报]", "Annual Report"),
    "interim": ("[中期報告]", "[中期报告]", "Interim Report"),
    "quarterly": ("[季度報告]", "[季度报告]", "Quarterly Report"),
}


def _hk_sid(code: str) -> str:
    code5 = str(code).upper().replace(".HK", "").zfill(5)
    if code5 in _HK_SID:
        return _HK_SID[code5]
    hits = resolve_hk(code5, 10)
    if not hits:
        raise LookupError(f"查不到 {code5} 的披露易编号")
    return hits[0].key


def find_hk(code: str, kind: str = "annual", period: int | None = None,
            filed: tuple[str, str] | None = None, limit: int = 20) -> list[Filing]:
    """披露易：按报告期或披露窗口取港股公告（含真 PDF 直链）。

    ⚠️ 三个坑：
      ① `rowRange` 100 不够 —— 小米 228 条公告里，2026 年发的 2025 年报
         被 100 条月报表/翌日披露报表挤出窗口 ✗ → 用 300
      ② 标题写法不统一：小米「2025年度報告」、建行「二零二五年年報」✗
      ③ **优先用 LONG_TEXT 的规范类别判**（交易所打的标），标题只当辅助
    """
    code5 = str(code).upper().replace(".HK", "").zfill(5)
    sid = _hk_sid(code5)

    if filed:
        f, t = filed[0].replace("-", ""), filed[1].replace("-", "")
    elif period:
        f, t = f"{period}-01-01".replace("-", ""), f"{period + 1}-12-31".replace("-", "")
    else:
        now = time.localtime().tm_year
        f, t = f"{now - 3}0101", f"{now}1231"

    j = _jsonp(_get("https://www1.hkexnews.hk/search/titleSearchServlet.do",
                    {"sortDir": "0", "sortByOptions": "DateTime",
                     "category": "0", "market": "SEHK", "stockId": sid,
                     "documentType": "-1", "fromDate": f, "toDate": t,
                     "title": "", "searchType": "0", "t1code": "",
                     "t2Gcode": "", "t2code": "", "rowRange": "300",
                     "lang": "ZH"},
                    f"查 {code5} 的港股公告", {"Referer": _HK_REF}))
    rows = json.loads(j.get("result") or "[]")
    kws = HK_KINDS.get(kind, HK_KINDS["annual"])
    tags = HK_TAGS.get(kind, HK_TAGS["annual"])
    all_tags = {t for v in HK_TAGS.values() for t in v}
    out: list[Filing] = []
    for r in rows:
        title = r.get("TITLE") or ""
        # LONG_TEXT 里是 HTML 转义的（`&#x2f;` 就是 `/`）
        long = (r.get("LONG_TEXT") or "").replace("&#x2f;", "/")

        # ★ **规范类别和标题必须同时满足。**
        # 只用类别不够：建行把「中信証券…關於建設銀行…持續督導報告」也归在
        # `[年報]` 类别下 ✗ —— 那是保荐机构写的，不是建行的年报 ✗
        # 只用标题也不够：「半年度報告」含「年度報告」✗
        if any(t in long for t in all_tags) and not any(t in long for t in tags):
            continue                               # 类别明确写了别的类型 → 排除
        if not any(k in title for k in kws):
            continue                                   # 标题不像 → 排除
        if kind == "annual" and ("半年度" in title or "中期" in title
                                 or "Interim" in title):
            continue                               # 防子串：半年度 ≠ 年度
        # 第三方的「关于…的报告」也要排掉（持续督导 / 保荐 / 独立意见）
        if any(x in title for x in ("持續督導", "持续督导", "保薦", "保荐",
                                    "獨立意見", "独立意见", "關於", "关于")) \
                and not title.startswith(str(period or "")):
            continue

        # **报告期过滤**：标题里带年份（阿拉伯或中文数字）
        if period and not (_year_in(title, period)):
            continue

        link = r.get("FILE_LINK") or ""
        if not link:
            continue
        dt = (r.get("DATE_TIME") or "").strip()
        out.append(Filing(
            market="hk", code=code5, title=title, kind=kind,
            filed_at=_hk_date(dt), period=f"{period}-12-31" if period else "",
            url=_hk_url(link), fmt="pdf", source="hkexnews",
            note="披露易按披露日存 —— 能还原「当时市场看到的版本」"))
        if len(out) >= limit:
            break
    return out


def _year_in(title: str, year: int) -> bool:
    """标题里有没有这个年份 —— 阿拉伯数字或中文数字都认。

    港股标题两种写法都有：「2025年度報告」/「二零二五年年報」✗
    """
    if str(year) in title:
        return True
    digits = "零一二三四五六七八九"
    cn = "".join(digits[int(d)] for d in str(year))
    return cn in title or cn.replace("零", "〇") in title


def _hk_date(s: str) -> str:
    """披露易给的是 `27/04/2026 17:37` → `2026-04-27`。"""
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def _hk_url(link: str) -> str:
    return ("https://www1.hkexnews.hk" + link
            if link.startswith("/") else link)


# ══════════════════════════ 美股 · SEC ══════════════════════════

def resolve_us_cik(ticker: str) -> str:
    t = str(ticker).upper().strip()
    if t in _US_CIK:
        return _US_CIK[t]
    _load_us_index()
    if t in _US_CIK:
        return _US_CIK[t]
    hits = resolve_us(t, 3)
    if hits:
        return hits[0].key
    raise LookupError(f"查不到 {ticker} 的 CIK")


def find_us(cik: str, form: str = "10-K", period: int | None = None,
            filed: tuple[str, str] | None = None, limit: int = 20) -> list[Filing]:
    """SEC EDGAR：按表种取申报文件。

    ⚠️ **只认 10-K 会漏掉所有 ADR** —— 外国私人发行人报 20-F。
    `form` 可以给空格分隔的多个，按顺序回退：`"10-K 20-F 40-F"`。
    """
    cik10 = str(cik).lstrip("0").zfill(10)
    raw = _get(f"https://data.sec.gov/submissions/CIK{cik10}.json", {},
               f"取 {cik} 的申报索引")
    j = json.loads(raw)
    rec = (j.get("filings") or {}).get("recent") or {}
    forms = rec.get("form") or []
    want = form.split()
    out: list[Filing] = []
    for i, f in enumerate(forms):
        if f not in want or len(out) >= limit:
            continue
        date = rec["filingDate"][i] or ""
        if filed and not (filed[0] <= date <= filed[1]):
            continue
        acc = (rec["accessionNumber"][i] or "").replace("-", "")
        doc = rec["primaryDocument"][i] or ""
        rept = (rec.get("reportDate") or [""] * len(forms))[i] or ""
        if period and not str(rept).startswith(str(period)):
            continue
        cik_num = str(j.get("cik") or cik10).lstrip("0")
        out.append(Filing(
            market="us", code=str(j.get("cik") or cik), title=doc, kind=f,
            filed_at=date, period=str(rept), fmt="html" if doc.lower().endswith(
                (".htm", ".html")) else "pdf", source="sec",
            url=f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc}/{doc}",
            note="SEC 按 filing date 存 —— 能还原「当时市场看到的版本」"))
    return out


# ══════════════════════════ 统一入口 ══════════════════════════

def find(query: str, market: str = "", kind: str = "annual",
         period: int | None = None, filed: tuple[str, str] | None = None,
         limit: int = 20) -> Result:
    """**名称或代码 → 候选 → 公告列表。** 失败也留在 `Result` 里，不静默丢。"""
    r = Result(query=str(query))
    if not str(query).strip():
        r.fail(FailReason.NO_MATCH, "查询是空的")
        return r

    ids = resolve(query, market, limit=10)
    r.identities = ids
    if not ids:
        r.fail(FailReason.NO_MATCH,
               f"没查到「{query}」这家公司。可能是代码写错、名称不规范，"
               f"或者这家不在公开披露体系里（非上市公司）。")
        return r
    if len(ids) > 1:
        # **多个候选 → 报出来让人知道，但不阻断**（还是把第一个的结果给出）。
        # 判据：跨市场，或者查询不像代码。
        looks_code = bool(re.fullmatch(r"[A-Za-z0-9.\-]+", str(query).strip()))
        markets = {i.market for i in ids}
        if len(markets) > 1 or not looks_code:
            names = "、".join(f"{i.market}:{i.code} {i.name}" for i in ids[:5])
            r.fail(FailReason.AMBIGUOUS,
                   f"「{query}」匹配到 {len(ids)} 家：{names}"
                   f"　—— 如果要的不是第一家，用准确代码重查")

    for ident in ids:
        try:
            if ident.market == "cn":
                got = find_cn(ident.code, kind, period, filed, limit)
            elif ident.market == "hk":
                got = find_hk(ident.code, kind, period, filed, limit)
            else:
                form = kind if kind.upper() == kind else "10-K 20-F 40-F 10-Q"
                got = find_us(ident.key, form, period, filed, limit)
        except LookupError as exc:
            r.fail(FailReason.MISSING_ID, str(exc))
            continue
        except Exception as exc:                   # noqa: BLE001
            r.fail(FailReason.NETWORK, f"{type(exc).__name__}: {exc}")
            continue
        if got:
            r.filings.extend(got)
            break                                  # 第一个命中的市场就够了
        r.fail(FailReason.NO_MATCH,
               f"{ident.market}:{ident.code} {ident.name} 没有符合条件的报告")
    return r


def same_kind(a: Identity, b: Identity) -> bool:
    return a.market == b.market


# ══════════════════════════ 下载 ══════════════════════════

def download(f: Filing, dest_dir: str | Path) -> Path:
    """下载一份公告。**验文件头 + 不许静默覆盖。**

    东财的 PDF CDN 会把 988 字节的混淆脚本伪装成 `application/pdf`，
    所以**绝不能只看状态码和 Content-Type**。
    """
    if not f.url:
        raise ValueError(f"这条没有直链：{f.title[:40]!r}")

    dest_dir = Path(dest_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    # ⚠️ 文件名不能只用 code+filed_at+kind —— 「年度报告」和「年度报告（英文版）」
    # 三者全同，会**静默覆盖** ✗ 加标题指纹：同一份重下同名（幂等），不同文档分开
    tag = hashlib.sha1(f.title.encode("utf-8")).hexdigest()[:6]
    stem = re.sub(r"[^\w\-.]", "_", f"{f.code}_{f.filed_at}_{f.kind}")[:70]
    suffix = ".pdf" if f.fmt == "pdf" else ".htm"
    path = dest_dir / f"{stem}_{tag}{suffix}"

    body = _get(f.url, {}, f"下载 {f.code} 的 {f.kind}")
    if f.fmt == "pdf" and not body[:4] == b"%PDF":
        raise ValueError(
            f"下回来的不是 PDF（{len(body)} 字节，头 {body[:12]!r}）—— 多半撞上反爬")
    if f.fmt == "html" and b"<html" not in body[:400].lower():
        raise ValueError(f"下回来的不像 HTML（{len(body)} 字节）")
    if path.exists() and path.stat().st_size != len(body):
        raise FileExistsError(
            f"{path.name} 已存在且大小不同（{path.stat().st_size:,} vs "
            f"{len(body):,}）—— 拒绝覆盖")

    path.write_bytes(body)
    (dest_dir / f"{path.stem}.source.json").write_text(json.dumps({
        "market": f.market, "code": f.code, "title": f.title, "kind": f.kind,
        "filed_at": f.filed_at, "period": f.period, "source": f.source,
        "url": f.url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
        "note": f.note,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def sources_in(directory: str | Path) -> list[dict]:
    """读出这个材料目录里所有 `.source.json` —— 即「这些材料是从哪来的」。"""
    d = Path(directory).expanduser()
    if not d.is_dir():
        d = d.parent
    out: list[dict] = []
    for p in sorted(d.glob("*.source.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rec["_file"] = p.name.replace(".source.json", "")
        out.append(rec)
    return out


def render_sources(directory: str | Path) -> list[str]:
    """把来源记录渲染成几行 —— 给 `Materials.render()` 用。"""
    recs = sources_in(directory)
    if not recs:
        return []
    lines = ["  材料来源　**公开披露**（不是用户提供，注意口径）"]
    for r in recs:
        mk = {"cn": "A股", "hk": "港股", "us": "美股"}.get(
            r.get("market", ""), r.get("market", "?"))
        lines.append(
            f"    · {r.get('_file', '?')}　{mk} {r.get('code', '')}"
            f"　披露 {r.get('filed_at') or '?'}"
            f"　下载 {str(r.get('fetched_at') or '?')[:10]}")
        if r.get("note"):
            lines.append(f"      ⚠ {r['note']}")
    lines.append("    ⚠ **这些是「现在能拿到的最新版」** —— 不是披露当日的快照。"
                 "跟基准日对齐时按 `披露` 那列判断。")
    return lines
