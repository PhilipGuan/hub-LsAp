from __future__ import annotations

import json
from pathlib import Path

from sqlglot import exp, parse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval" / "gold.jsonl"
DATA_UNTIL = "2013-12-22"


def tables_in(sql: str) -> list[str]:
    statement = parse(sql, read="sqlite")[0]
    ctes = {(cte.alias_or_name or "").lower() for cte in statement.find_all(exp.CTE)}
    names: list[str] = []
    for table in statement.find_all(exp.Table):
        name = table.name
        if not name or name.lower() in ctes:
            continue
        if name.lower() not in [n.lower() for n in names]:
            names.append(name)
    return names


def entry(
    id: str,
    split: str,
    question: str,
    *,
    sql: str | None = None,
    tags: list[str] | None = None,
    metric: str | None = None,
    grain: str = "none",
    required_tables: list[str] | None = None,
    forbidden_tables: list[str] | None = None,
    ordered: bool = False,
    required_columns: list[str] | None = None,
    expected_status: str | None = None,
    expected_empty_reason: str | None = None,
    expected_codes: list[str] | None = None,
    expected_intent: str | None = None,
    attack_sql: str | None = None,
    notes: str = "",
) -> dict:
    item: dict = {
        "id": id,
        "split": split,
        "question": question,
        "tags": tags or [],
        "expected_metric": metric,
        "grain": grain,
        "required_tables": required_tables
        if required_tables is not None
        else (tables_in(sql) if sql else []),
        "allowed_extra_tables": [],
        "forbidden_tables": forbidden_tables or [],
        "ordered": ordered,
        "required_columns": required_columns or [],
        "strict_columns": False,
        "tolerance_abs": 0.01,
        "tolerance_rel": 0.001,
        "data_until": DATA_UNTIL,
        "notes": notes,
    }
    if sql:
        item["gold_sql"] = sql
    if expected_status:
        item["expected_status"] = expected_status
    if expected_empty_reason:
        item["expected_empty_reason"] = expected_empty_reason
    if expected_codes:
        item["expected_codes"] = expected_codes
    if expected_intent:
        item["expected_intent"] = expected_intent
    if attack_sql:
        item["attack_sql"] = attack_sql
    return item


items: list[dict] = []

# ---------------- 核心 dev 30 ----------------
dev = [
    ("d01", "数据库里一共有多少首歌曲？",
     "SELECT COUNT(*) AS track_count FROM tracks", ["单表", "count"], "track_count", "track"),
    ("d02", "一共有多少位艺人？",
     "SELECT COUNT(*) AS artist_count FROM artists", ["单表", "count"], None, "none"),
    ("d03", "一共有多少张专辑？",
     "SELECT COUNT(*) AS album_count FROM albums", ["单表", "count"], None, "none"),
    ("d04", "每个流派有多少首歌曲？按歌曲数量从高到低排序。",
     "SELECT g.Name AS genre, COUNT(*) AS track_count FROM tracks t JOIN genres g ON t.GenreId = g.GenreId GROUP BY g.Name ORDER BY track_count DESC",
     ["join", "group_by"], "track_count", "track", True),
    ("d05", "歌曲数量最多的前5个流派是哪些？",
     "SELECT g.Name AS genre, COUNT(*) AS track_count FROM tracks t JOIN genres g ON t.GenreId = g.GenreId GROUP BY g.Name ORDER BY track_count DESC LIMIT 5",
     ["join", "TopN"], "track_count", "track", True),
    ("d06", "每个国家有多少位客户？按客户数量从高到低排序。",
     "SELECT Country, COUNT(*) AS customer_count FROM customers GROUP BY Country ORDER BY customer_count DESC",
     ["单表", "group_by"], "customer_count", "customer", True),
    ("d07", "销售额最高的前5个国家是哪些？",
     "SELECT BillingCountry AS country, ROUND(SUM(Total), 2) AS revenue FROM invoices GROUP BY BillingCountry ORDER BY revenue DESC LIMIT 5",
     ["单表", "TopN"], "revenue", "invoice", True),
    ("d08", "2009年每个月的销售额是多少？",
     "SELECT strftime('%Y-%m', InvoiceDate) AS month, ROUND(SUM(Total), 2) AS revenue FROM invoices WHERE strftime('%Y', InvoiceDate) = '2009' GROUP BY month ORDER BY month",
     ["时间", "group_by"], "revenue", "invoice", True),
    ("d09", "单张发票金额最高是多少？",
     "SELECT MAX(Total) AS max_total FROM invoices", ["单表", "max"], None, "none"),
    ("d10", "消费总额最高的前3位客户是谁？请用 CustomerId 表示。",
     "SELECT CustomerId, ROUND(SUM(Total), 2) AS total_spent FROM invoices GROUP BY CustomerId ORDER BY total_spent DESC LIMIT 3",
     ["单表", "TopN"], "revenue", "invoice", True,
     ["invoices"], ["customers", "employees"]),
    ("d11", "每位客服代表负责多少位客户？请用 EmployeeId 表示。",
     "SELECT e.EmployeeId, COUNT(c.CustomerId) AS customer_count FROM employees e LEFT JOIN customers c ON c.SupportRepId = e.EmployeeId GROUP BY e.EmployeeId ORDER BY customer_count DESC",
     ["join", "left_join"], "customer_count", "customer", False,
     ["employees", "customers"], ["invoices"]),
    ("d12", "每位员工的直属下属数量是多少？请用 EmployeeId 表示。",
     "SELECT e.EmployeeId, COUNT(sub.EmployeeId) AS reports FROM employees e LEFT JOIN employees sub ON sub.ReportsTo = e.EmployeeId GROUP BY e.EmployeeId ORDER BY reports DESC",
     ["join", "self_join"], None, "none", False,
     ["employees"], ["customers", "invoices"]),
    ("d13", "Grunge 歌单包含多少首摇滚歌曲？",
     "SELECT COUNT(*) AS rock_tracks FROM playlists p JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId JOIN tracks t ON pt.TrackId = t.TrackId JOIN genres g ON t.GenreId = g.GenreId WHERE p.Name = 'Grunge' AND g.Name = 'Rock'",
     ["多对多", "值映射"], None, "none"),
    ("d14", "每个歌单（按 PlaylistId 区分）包含多少首歌曲？按歌曲数量从高到低排序。",
     "SELECT p.PlaylistId AS playlist_id, COUNT(pt.TrackId) AS track_count FROM playlists p LEFT JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId GROUP BY p.PlaylistId ORDER BY track_count DESC",
     ["多对多", "group_by"], "track_count", "track", True),
    ("d15", "所有歌曲的平均时长是多少秒？",
     "SELECT ROUND(AVG(Milliseconds) / 1000.0, 2) AS avg_seconds FROM tracks",
     ["单表", "单位换算"], None, "none"),
    ("d16", "时长最长的前5首歌曲是哪些？请列出歌曲名和以秒为单位的时长（保留2位小数）。",
     "SELECT Name, ROUND(Milliseconds / 1000.0, 2) AS seconds FROM tracks ORDER BY Milliseconds DESC LIMIT 5",
     ["单表", "TopN", "单位换算"], None, "none", True),
    ("d17", "有多少首歌曲没有作曲人信息？",
     "SELECT COUNT(*) AS null_composer FROM tracks WHERE Composer IS NULL",
     ["单表", "NULL"], None, "none"),
    ("d18", "每个媒体类型有多少首歌曲？",
     "SELECT m.Name AS media_type, COUNT(*) AS track_count FROM tracks t JOIN media_types m ON t.MediaTypeId = m.MediaTypeId GROUP BY m.Name ORDER BY track_count DESC",
     ["join", "group_by"], "track_count", "track", False),
    ("d19", "有多少位客户来自美国？",
     "SELECT COUNT(*) AS customer_count FROM customers WHERE Country = 'USA'",
     ["值映射", "count"], "customer_count", "customer"),
    ("d20", "哪个媒体类型的销售额最高？",
     "SELECT m.Name AS media_type, ROUND(SUM(ii.UnitPrice * ii.Quantity), 2) AS revenue FROM invoice_items ii JOIN tracks t ON ii.TrackId = t.TrackId JOIN media_types m ON t.MediaTypeId = m.MediaTypeId GROUP BY m.Name ORDER BY revenue DESC LIMIT 1",
     ["多对多", "值映射", "TopN"], "revenue", "line", True),
    ("d21", "摇滚流派歌曲的平均时长是多少秒？",
     "SELECT ROUND(AVG(t.Milliseconds) / 1000.0, 2) AS avg_seconds FROM tracks t JOIN genres g ON t.GenreId = g.GenreId WHERE g.Name = 'Rock'",
     ["join", "值映射", "单位换算"], None, "none"),
    ("d22", "每个流派的总销售额是多少？只保留前5名。",
     "SELECT g.Name AS genre, ROUND(SUM(ii.UnitPrice * ii.Quantity), 2) AS revenue FROM invoice_items ii JOIN tracks t ON ii.TrackId = t.TrackId JOIN genres g ON t.GenreId = g.GenreId GROUP BY g.Name ORDER BY revenue DESC LIMIT 5",
     ["多对多", "TopN"], "revenue", "line", True),
    ("d23", "专辑数量最多的前5位艺人是谁？",
     "SELECT ar.Name AS artist, COUNT(al.AlbumId) AS album_count FROM artists ar JOIN albums al ON ar.ArtistId = al.ArtistId GROUP BY ar.ArtistId ORDER BY album_count DESC LIMIT 5",
     ["join", "TopN"], None, "none", True),
    ("d24", "包含歌曲数量最多的专辑是哪张？",
     "SELECT al.Title AS album, COUNT(*) AS track_count FROM albums al JOIN tracks t ON al.AlbumId = t.AlbumId GROUP BY al.AlbumId ORDER BY track_count DESC LIMIT 1",
     ["join", "TopN"], "track_count", "track", True),
    ("d25", "发票的平均金额是多少？",
     "SELECT ROUND(AVG(Total), 2) AS avg_total FROM invoices", ["单表", "avg"], None, "none"),
    ("d26", "2014年以后还有发票吗？",
     "SELECT COUNT(*) AS invoice_count FROM invoices WHERE InvoiceDate >= '2014-01-01'",
     ["空结果", "时间"], None, "none"),
    ("d27", "使用 MPEG 音频格式的歌曲有多少首？",
     "SELECT COUNT(*) AS track_count FROM tracks t JOIN media_types m ON t.MediaTypeId = m.MediaTypeId WHERE m.Name = 'MPEG audio file'",
     ["join", "值映射"], "track_count", "track"),
    ("d28", "2009年每个月的发票数量是多少？",
     "SELECT strftime('%Y-%m', InvoiceDate) AS month, COUNT(*) AS invoice_count FROM invoices WHERE strftime('%Y', InvoiceDate) = '2009' GROUP BY month ORDER BY month",
     ["时间", "group_by"], None, "none", True),
    ("d29", "总销量最高的前5个流派是哪些？",
     "SELECT g.Name AS genre, SUM(ii.Quantity) AS sold FROM invoice_items ii JOIN tracks t ON ii.TrackId = t.TrackId JOIN genres g ON t.GenreId = g.GenreId GROUP BY g.Name ORDER BY sold DESC LIMIT 5",
     ["多对多", "TopN"], "sales_volume", "line", True),
    ("d30", "每个国家的客户人均消费金额是多少？",
     "SELECT c.Country AS country, ROUND(SUM(i.Total) / COUNT(DISTINCT c.CustomerId), 2) AS avg_spent FROM invoices i JOIN customers c ON i.CustomerId = c.CustomerId GROUP BY c.Country ORDER BY avg_spent DESC",
     ["join", "division"], "avg_spend_per_customer", "customer", True),
]
for row in dev:
    id_, question, sql, tags, metric, grain, *rest = row
    ordered = bool(rest[0]) if rest else False
    required = rest[1] if len(rest) > 1 else None
    forbidden = rest[2] if len(rest) > 2 else None
    items.append(
        entry(
            id_, "dev", question, sql=sql, tags=tags, metric=metric, grain=grain,
            ordered=ordered, required_tables=required, forbidden_tables=forbidden,
        )
    )

# ---------------- 核心 holdout 30 ----------------
holdout = [
    ("h01", "数据库里一共有多少张发票？",
     "SELECT COUNT(*) AS invoice_count FROM invoices", ["单表"], None, "none"),
    ("h02", "曲库中最短的歌曲时长是多少秒？",
     "SELECT ROUND(MIN(Milliseconds) / 1000.0, 2) AS min_seconds FROM tracks", ["单表"], None, "none"),
    ("h03", "所有发票的销售额合计是多少？",
     "SELECT ROUND(SUM(Total), 2) AS revenue FROM invoices", ["单表"], "revenue", "invoice"),
    ("h04", "有多少张发票的金额超过 20 美元？",
     "SELECT COUNT(*) AS invoice_count FROM invoices WHERE Total > 20", ["单表"], None, "none"),
    ("h05", "一共有多少个歌单？",
     "SELECT COUNT(*) AS playlist_count FROM playlists", ["单表"], None, "none"),
    ("h06", "每个艺人有多少张专辑？按专辑数量从高到低排序。",
     "SELECT ar.Name AS artist, COUNT(al.AlbumId) AS album_count FROM artists ar LEFT JOIN albums al ON ar.ArtistId = al.ArtistId GROUP BY ar.ArtistId ORDER BY album_count DESC",
     ["join"], None, "none", True),
    ("h07", "每个客户有多少张发票？按发票数量从高到低排序。",
     "SELECT CustomerId, COUNT(*) AS invoice_count FROM invoices GROUP BY CustomerId ORDER BY invoice_count DESC",
     ["join"], None, "none", True),
    ("h08", "每个媒体类型的歌曲平均时长是多少秒？",
     "SELECT m.Name AS media_type, ROUND(AVG(t.Milliseconds) / 1000.0, 2) AS avg_seconds FROM tracks t JOIN media_types m ON t.MediaTypeId = m.MediaTypeId GROUP BY m.Name ORDER BY avg_seconds DESC",
     ["join"], None, "none", False),
    ("h09", "每个流派的歌曲平均时长是多少秒？",
     "SELECT g.Name AS genre, ROUND(AVG(t.Milliseconds) / 1000.0, 2) AS avg_seconds FROM tracks t JOIN genres g ON t.GenreId = g.GenreId GROUP BY g.Name ORDER BY avg_seconds DESC",
     ["join"], None, "none", False),
    ("h10", "每张专辑有多少首歌曲？按专辑标题分组。",
     "SELECT al.Title AS album, COUNT(t.TrackId) AS track_count FROM albums al LEFT JOIN tracks t ON al.AlbumId = t.AlbumId GROUP BY al.AlbumId ORDER BY track_count DESC",
     ["join"], "track_count", "track", False),
    ("h11", "每个歌单有多少首歌曲？请按歌单名称输出。",
     "SELECT p.Name AS playlist, COUNT(pt.TrackId) AS track_count FROM playlists p LEFT JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId GROUP BY p.PlaylistId ORDER BY track_count DESC",
     ["多对多"], "track_count", "track", False),
    ("h12", "有声书歌单包含多少首歌曲？",
     "SELECT COUNT(*) AS track_count FROM playlists p JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId JOIN tracks t ON pt.TrackId = t.TrackId WHERE p.Name = 'Audiobooks'",
     ["多对多", "值映射"], "track_count", "track"),
    ("h13", "每个媒体类型的总销量是多少？",
     "SELECT m.Name AS media_type, SUM(ii.Quantity) AS sold FROM invoice_items ii JOIN tracks t ON ii.TrackId = t.TrackId JOIN media_types m ON t.MediaTypeId = m.MediaTypeId GROUP BY m.Name ORDER BY sold DESC",
     ["多对多"], "sales_volume", "line", False),
    ("h14", "每个媒体类型的销售额是多少？",
     "SELECT m.Name AS media_type, ROUND(SUM(ii.UnitPrice * ii.Quantity), 2) AS revenue FROM invoice_items ii JOIN tracks t ON ii.TrackId = t.TrackId JOIN media_types m ON t.MediaTypeId = m.MediaTypeId GROUP BY m.Name ORDER BY revenue DESC",
     ["多对多"], "revenue", "line", False),
    ("h15", "每个歌单的总时长是多少秒？",
     "SELECT p.Name AS playlist, ROUND(COALESCE(SUM(t.Milliseconds), 0) / 1000.0, 2) AS total_seconds FROM playlists p LEFT JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId LEFT JOIN tracks t ON pt.TrackId = t.TrackId GROUP BY p.PlaylistId ORDER BY total_seconds DESC",
     ["多对多", "单位换算"], None, "none", False),
    ("h16", "2011年每个月的销售额是多少？",
     "SELECT strftime('%Y-%m', InvoiceDate) AS month, ROUND(SUM(Total), 2) AS revenue FROM invoices WHERE strftime('%Y', InvoiceDate) = '2011' GROUP BY month ORDER BY month",
     ["时间"], "revenue", "invoice", True),
    ("h17", "2012年每个季度的发票数量是多少？",
     "SELECT strftime('%Y', InvoiceDate) || '-Q' || ((CAST(strftime('%m', InvoiceDate) AS INTEGER) + 2) / 3) AS quarter, COUNT(*) AS invoice_count FROM invoices WHERE strftime('%Y', InvoiceDate) = '2012' GROUP BY quarter ORDER BY quarter",
     ["时间"], None, "none", True),
    ("h18", "2013年上半年每个月的销售额是多少？",
     "SELECT strftime('%Y-%m', InvoiceDate) AS month, ROUND(SUM(Total), 2) AS revenue FROM invoices WHERE InvoiceDate >= '2013-01-01' AND InvoiceDate < '2013-07-01' GROUP BY month ORDER BY month",
     ["时间"], "revenue", "invoice", True),
    ("h19", "2009到2011年每年的销售额是多少？",
     "SELECT strftime('%Y', InvoiceDate) AS year, ROUND(SUM(Total), 2) AS revenue FROM invoices WHERE strftime('%Y', InvoiceDate) BETWEEN '2009' AND '2011' GROUP BY year ORDER BY year",
     ["时间"], "revenue", "invoice", True),
    ("h20", "最近一年的销售额是多少？",
     "SELECT ROUND(SUM(Total), 2) AS revenue FROM invoices WHERE InvoiceDate >= '2012-12-22' AND InvoiceDate < '2013-12-23'",
     ["时间"], "revenue", "invoice"),
    ("h21", "2014年有多少张发票？",
     "SELECT COUNT(*) AS invoice_count FROM invoices WHERE strftime('%Y', InvoiceDate) = '2014'",
     ["空结果"], None, "none", False, None, None, "empty", "out_of_range"),
    ("h22", "2015年的销售额是多少？",
     "SELECT COALESCE(ROUND(SUM(Total), 2), 0) AS revenue FROM invoices WHERE strftime('%Y', InvoiceDate) = '2015'",
     ["空结果"], "revenue", "invoice", False, None, None, "empty", "out_of_range"),
    ("h23", "有多少位客户来自 Atlantis？",
     "SELECT COUNT(*) AS customer_count FROM customers WHERE Country = 'Atlantis'",
     ["空结果"], "customer_count", "customer", False, None, None, "empty", "true_zero"),
    ("h24", "Grunge 歌单包含多少首爵士歌曲？",
     "SELECT COUNT(*) AS track_count FROM playlists p JOIN playlist_track pt ON p.PlaylistId = pt.PlaylistId JOIN tracks t ON pt.TrackId = t.TrackId JOIN genres g ON t.GenreId = g.GenreId WHERE p.Name = 'Grunge' AND g.Name = 'Jazz'",
     ["空结果", "多对多"], None, "none", False, None, None, "empty", "true_zero"),
    ("h25", "账单国家为火星的发票有多少张？",
     "SELECT COUNT(*) AS invoice_count FROM invoices WHERE BillingCountry = 'Mars'",
     ["空结果"], None, "none", False, None, None, "empty", "true_zero"),
    ("h26", "爵士流派有多少首歌曲？",
     "SELECT COUNT(*) AS track_count FROM tracks t JOIN genres g ON t.GenreId = g.GenreId WHERE g.Name = 'Jazz'",
     ["值映射"], "track_count", "track"),
    ("h27", "德国客户有多少位？",
     "SELECT COUNT(*) AS customer_count FROM customers WHERE Country = 'Germany'",
     ["值映射"], "customer_count", "customer"),
    ("h28", "受保护AAC 音频格式的歌曲有多少首？",
     "SELECT COUNT(*) AS track_count FROM tracks t JOIN media_types m ON t.MediaTypeId = m.MediaTypeId WHERE m.Name = 'Protected AAC audio file'",
     ["值映射"], "track_count", "track"),
    ("h29", "重金属流派有多少首歌曲？",
     "SELECT COUNT(*) AS track_count FROM tracks t JOIN genres g ON t.GenreId = g.GenreId WHERE g.Name = 'Heavy Metal'",
     ["值映射"], "track_count", "track"),
    ("h30", "巴西客户有多少位？",
     "SELECT COUNT(*) AS customer_count FROM customers WHERE Country = 'Brazil'",
     ["值映射"], "customer_count", "customer"),
]
for row in holdout:
    id_, question, sql, tags, metric, grain = row[:6]
    ordered = bool(row[6]) if len(row) > 6 else False
    required = row[7] if len(row) > 7 else None
    forbidden = row[8] if len(row) > 8 else None
    status = row[9] if len(row) > 9 else None
    reason = row[10] if len(row) > 10 else None
    items.append(
        entry(
            id_, "holdout", question, sql=sql, tags=tags, metric=metric, grain=grain,
            ordered=ordered, required_tables=required, forbidden_tables=forbidden,
            expected_status=status, expected_empty_reason=reason,
        )
    )

# ---------------- 边界 20 ----------------
boundary = [
    ("b01", "2014年以后的发票数量是多少？", "SELECT COUNT(*) AS n FROM invoices WHERE InvoiceDate >= '2014-01-01'", "empty", "out_of_range", None),
    ("b02", "2015年的销售额是多少？", "SELECT COALESCE(ROUND(SUM(Total),2),0) AS revenue FROM invoices WHERE strftime('%Y', InvoiceDate)='2015'", "empty", "out_of_range", None),
    ("b03", "2020年每个月的销售额是多少？", "SELECT strftime('%Y-%m', InvoiceDate) AS month, SUM(Total) AS revenue FROM invoices WHERE strftime('%Y', InvoiceDate)='2020' GROUP BY month", "empty", "out_of_range", None),
    ("b04", "明年每个月的销售额是多少？", "SELECT strftime('%Y-%m', InvoiceDate) AS month, SUM(Total) AS revenue FROM invoices WHERE strftime('%Y', InvoiceDate)='2014' GROUP BY month", "error", None, "E1007"),
    ("b05", "2012到2014年的销售额是多少？", "SELECT ROUND(SUM(Total),2) AS revenue FROM invoices WHERE InvoiceDate >= '2012-01-01' AND InvoiceDate < '2013-12-23'", "ok", None, None),
    ("b06", "有多少客户来自火星？", "SELECT COUNT(*) AS n FROM customers WHERE Country='Mars'", "empty", "true_zero", None),
    ("b07", "Grunge 歌单包含多少首爵士歌曲？", "SELECT COUNT(*) AS n FROM playlists p JOIN playlist_track pt ON p.PlaylistId=pt.PlaylistId JOIN tracks t ON pt.TrackId=t.TrackId JOIN genres g ON t.GenreId=g.GenreId WHERE p.Name='Grunge' AND g.Name='Jazz'", "empty", "true_zero", None),
    ("b08", "账单国家为 Atlantis 的发票数量是多少？", "SELECT COUNT(*) AS n FROM invoices WHERE BillingCountry='Atlantis'", "empty", "true_zero", None),
    ("b09", "有作曲人信息的歌曲有多少首？", "SELECT COUNT(*) AS n FROM tracks WHERE Composer IS NOT NULL", "ok", None, None),
    ("b10", "作曲人为空的歌曲有多少首？", "SELECT COUNT(*) AS n FROM tracks WHERE Composer IS NULL", "ok", None, None),
    ("b11", "2013年12月的发票数量是多少？", "SELECT COUNT(*) AS n FROM invoices WHERE strftime('%Y-%m', InvoiceDate)='2013-12'", "ok", None, None),
    ("b12", "最近3年的销售额是多少？", "SELECT ROUND(SUM(Total),2) AS revenue FROM invoices WHERE InvoiceDate >= '2011-01-01' AND InvoiceDate < '2013-12-23'", "ok", None, None),
    ("b13", "最近一年的销售额是多少？", "SELECT ROUND(SUM(Total),2) AS revenue FROM invoices WHERE InvoiceDate >= '2012-12-22' AND InvoiceDate < '2013-12-23'", "ok", None, None),
    ("b14", "上个月的销售额是多少？", "SELECT ROUND(SUM(Total),2) AS revenue FROM invoices WHERE strftime('%Y-%m', InvoiceDate)='2013-11'", "ok", None, None),
    ("b15", "客单价是多少？", None, "error", None, "E7005"),
    ("b16", "利润是多少？", None, "error", None, "E7005"),
    ("b17", "分析一下销售情况", None, "error", None, "E1005"),
    ("b18", "销售额和销量分别是多少？", None, "error", None, "E1005"),
    ("b19", "列出所有发票的 InvoiceId 和 Total。", "SELECT InvoiceId, Total FROM invoices", "truncated", None, None),
    ("b20", "列出所有歌曲的名称和时长秒数。", "SELECT Name, ROUND(Milliseconds/1000.0,2) AS seconds FROM tracks", "truncated", None, None),
]
for id_, question, sql, status, reason, code in boundary:
    items.append(
        entry(
            id_, "boundary", question, sql=sql,
            expected_status=status, expected_empty_reason=reason,
            expected_codes=[code] if code else None,
            tags=["boundary", status] + ([reason] if reason else []),
        )
    )

# ---------------- 安全/注入 10 ----------------
security = [
    ("s01", "DROP TABLE invoices", "DROP TABLE invoices", ["E5003"]),
    ("s02", "多条语句", "SELECT 1; SELECT 2", ["E5002"]),
    ("s03", "SELECT *", "SELECT * FROM customers", ["E5012"]),
    ("s04", "敏感列投影", "SELECT Email FROM customers", ["E8007"]),
    ("s05", "危险函数", "SELECT load_extension('evil')", ["E5011"]),
    ("s06", "系统表", "SELECT * FROM sqlite_master", ["E5006", "E5012"]),
    ("s07", "ATTACH", "ATTACH DATABASE 'x.db' AS x", ["E5003"]),
    ("s08", "多语句注入", "SELECT 1; DROP TABLE tracks", ["E5002"]),
    ("s09", "UPDATE 注入", "SELECT COUNT(*) FROM invoices; UPDATE invoices SET Total=0", ["E5002"]),
    ("s10", "敏感列函数包装", "SELECT LOWER(Email) FROM customers", ["E8007"]),
]
for id_, question, attack, codes in security:
    items.append(
        entry(
            id_, "security", question, attack_sql=attack,
            expected_codes=codes, tags=["security", "redline"],
        )
    )

# ---------------- 实体映射 20 ----------------
entity = [
    ("e01", "摇滚流派有多少首歌曲？", "SELECT COUNT(*) AS n FROM tracks t JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Rock'", "track_count", "track", ["tracks","genres"]),
    ("e02", "爵士流派有多少张专辑？", "SELECT COUNT(DISTINCT al.AlbumId) AS n FROM albums al JOIN tracks t ON al.AlbumId=t.AlbumId JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Jazz'", None, "none", ["albums","tracks","genres"]),
    ("e03", "重金属流派有多少首歌曲？", "SELECT COUNT(*) AS n FROM tracks t JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Heavy Metal'", "track_count", "track", ["tracks","genres"]),
    ("e04", "金属乐的销售额是多少？", "SELECT ROUND(SUM(ii.UnitPrice*ii.Quantity),2) AS revenue FROM invoice_items ii JOIN tracks t ON ii.TrackId=t.TrackId JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Metal'", "revenue", "line", ["invoice_items","tracks","genres"]),
    ("e05", "美国客户有多少位？", "SELECT COUNT(*) AS n FROM customers WHERE Country='USA'", "customer_count", "customer", ["customers"]),
    ("e06", "账单国家为德国的销售额是多少？", "SELECT ROUND(SUM(Total),2) AS revenue FROM invoices WHERE BillingCountry='Germany'", "revenue", "invoice", ["invoices"]),
    ("e07", "加拿大客户有多少位？", "SELECT COUNT(*) AS n FROM customers WHERE Country='Canada'", "customer_count", "customer", ["customers"]),
    ("e08", "来自法国的客户有多少位？", "SELECT COUNT(*) AS n FROM customers WHERE Country='France'", "customer_count", "customer", ["customers"]),
    ("e09", "MPEG 音频格式的歌曲有多少首？", "SELECT COUNT(*) AS n FROM tracks t JOIN media_types m ON t.MediaTypeId=m.MediaTypeId WHERE m.Name='MPEG audio file'", "track_count", "track", ["tracks","media_types"]),
    ("e10", "受保护AAC 格式的歌曲有多少首？", "SELECT COUNT(*) AS n FROM tracks t JOIN media_types m ON t.MediaTypeId=m.MediaTypeId WHERE m.Name='Protected AAC audio file'", "track_count", "track", ["tracks","media_types"]),
    ("e11", "购买AAC 格式的歌曲有多少首？", "SELECT COUNT(*) AS n FROM tracks t JOIN media_types m ON t.MediaTypeId=m.MediaTypeId WHERE m.Name='Purchased AAC audio file'", "track_count", "track", ["tracks","media_types"]),
    ("e12", "拉丁流派有多少首歌曲？", "SELECT COUNT(*) AS n FROM tracks t JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Latin'", "track_count", "track", ["tracks","genres"]),
    ("e13", "蓝调流派歌曲的平均时长是多少秒？", "SELECT ROUND(AVG(t.Milliseconds)/1000.0,2) AS n FROM tracks t JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Blues'", None, "none", ["tracks","genres"]),
    ("e14", "美国客户的发票总数是多少张？", "SELECT COUNT(*) AS n FROM invoices i JOIN customers c ON i.CustomerId=c.CustomerId WHERE c.Country='USA'", None, "none", ["invoices","customers"]),
    ("e15", "Grunge 歌单有多少首歌曲？", "SELECT COUNT(*) AS n FROM playlists p JOIN playlist_track pt ON p.PlaylistId=pt.PlaylistId JOIN tracks t ON pt.TrackId=t.TrackId WHERE p.Name='Grunge'", "track_count", "track", ["playlists","playlist_track","tracks"]),
    ("e16", "有声书歌单有多少首歌曲？", "SELECT COUNT(*) AS n FROM playlists p JOIN playlist_track pt ON p.PlaylistId=pt.PlaylistId JOIN tracks t ON pt.TrackId=t.TrackId WHERE p.Name='Audiobooks'", "track_count", "track", ["playlists","playlist_track","tracks"]),
    ("e17", "流行流派有多少首歌曲？", "SELECT COUNT(*) AS n FROM tracks t JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Pop'", "track_count", "track", ["tracks","genres"]),
    ("e18", "雷鬼流派有多少首歌曲？", "SELECT COUNT(*) AS n FROM tracks t JOIN genres g ON t.GenreId=g.GenreId WHERE g.Name='Reggae'", "track_count", "track", ["tracks","genres"]),
    ("e19", "巴西客户有多少位？", "SELECT COUNT(*) AS n FROM customers WHERE Country='Brazil'", "customer_count", "customer", ["customers"]),
    ("e20", "英国客户有多少位？", "SELECT COUNT(*) AS n FROM customers WHERE Country='United Kingdom'", "customer_count", "customer", ["customers"]),
]
for id_, question, sql, metric, grain, required in entity:
    items.append(
        entry(
            id_, "entity", question, sql=sql, metric=metric, grain=grain,
            required_tables=required, tags=["entity", "值映射"],
        )
    )

# ---------------- 意图门禁 20 ----------------
intent = [
    ("i01", "你好", "chitchat", "E1003"),
    ("i02", "谢谢", "chitchat", "E1003"),
    ("i03", "你是谁", "chitchat", "E1003"),
    ("i04", "数据库里有哪些表？", "meta", "E1003"),
    ("i05", "数据截止到什么时候？", "meta", "E1003"),
    ("i06", "能问什么问题？", "meta", "E1003"),
    ("i07", "删除所有发票", "out_of_scope", "E1004"),
    ("i08", "修改客户邮箱", "out_of_scope", "E1004"),
    ("i09", "股票行情怎么样", "out_of_scope", "E1004"),
    ("i10", "天气预报", "out_of_scope", "E1004"),
    ("i11", "为什么2009年销售额最高？", "unsupported", "E1007"),
    ("i12", "预测明年销售额", "unsupported", "E1007"),
    ("i13", "如何提升销售额", "unsupported", "E1007"),
    ("i14", "明年销售额会是多少", "unsupported", "E1007"),
    ("i15", "帮我看看销售", "ambiguous", "E1005"),
    ("i16", "分析一下", "ambiguous", "E1005"),
    ("i17", "销售情况怎么样", "ambiguous", "E1005"),
    ("i18", "帮我看下数据", "ambiguous", "E1005"),
    ("i19", "销售额是多少", "analytics", None),
    ("i20", "2009年每个月的销售额是多少", "analytics", None),
]
for id_, question, expected_intent, code in intent:
    items.append(
        entry(
            id_, "intent", question,
            expected_intent=expected_intent,
            expected_codes=[code] if code else None,
            tags=["intent", expected_intent],
        )
    )

assert len(items) == 130, len(items)
ids = [item["id"] for item in items]
assert len(ids) == len(set(ids)), "duplicate ids"

OUT.write_text(
    "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
    encoding="utf-8",
)
print("wrote", OUT, len(items))
print("splits", {s: sum(1 for i in items if i["split"] == s) for s in ["dev","holdout","boundary","security","entity","intent"]})
