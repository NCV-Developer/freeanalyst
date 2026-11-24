"""台湾 MOPS（公开资讯观测站）财报下载。

    https://mopsov.twse.com.tw/server-java/t164sb01
        ?step=1&CO_ID=2330&SYEAR=2025&SSEASON=4&REPORT_ID=C

- 主站 `mops.twse.com.tw` 对直接查询返回「因安全性考量无法呈现」，**镜像站 mopsov 可以**。
- 返回 Big5 编码的 HTML，含三张表与 XBRL 标签。
- `REPORT_ID=C` 是**合并**报表（consolidated）。
- `SSEASON=4` 是**年度**（Q4 累计）。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
HOST = "https://mopsov.twse.com.tw"


def fetch(code: str, year: int, *, season: int = 4, report: str = "C",
          out_dir: str | Path = "materials/tw") -> Path:
    """下载一家台湾公司的年度合并财报，返回 UTF-8 HTML 路径。"""
    url = (f"{HOST}/server-java/t164sb01?step=1&CO_ID={code}"
           f"&SYEAR={year}&SSEASON={season}&REPORT_ID={report}")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": f"{HOST}/mops/web/t164sb01",
    })
    with urllib.request.urlopen(req, timeout=40) as r:
        raw = r.read()
    if len(raw) < 50_000:
        raise RuntimeError(f"MOPS 返回内容过短（{len(raw)} 字节），可能是安全拦截或没有这一期")

    out = Path(out_dir) / str(code)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{code}_{year}_annual.htm"
    path.write_text(raw.decode("big5", errors="replace"), encoding="utf-8")
    return path
