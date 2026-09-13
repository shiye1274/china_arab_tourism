# -*- coding: utf-8 -*-
"""
fetch_en_crossref.py —— 纯英文「中阿文旅」文献抓取（Crossref 官方 API，免费、无需 Key）

为什么有这个脚本：
    原 fetch_papers.py 依赖 OpenAlex。OpenAlex 已改为信用额度制（响应头
    x-ratelimit-limit: 1000 / x-ratelimit-remaining: 0 / retry-after: ~60500s），
    本机 IP 当日额度耗尽后会连续 429，约 17 小时后才恢复。为不阻塞语料建设，
    增加 Crossref 通道（Crossref 礼貌池 50 req/s，无额度上限）。

与 OpenAlex 通道的差异（重要，写论文时要如实说明）：
    - Crossref 的摘要覆盖率低（期刊常不存摘要），检索只能打在「标题」上。
      因此本脚本的「中阿关系」判定以**标题**为主，摘要仅在存在时辅助。
    - Crossref 的文献类型（type）字段非常完整：journal-article / book-chapter /
      monograph / proceedings-article / report / posted-content 等，正好用于论文
      要求补充的「文献类型」维度。

两层语料（README 建议做法）：
    Tier A  strict   : 标题同时含「中国侧」AND「阿拉伯侧」标记 —— 核心双边集
    Tier B  relaxed  : 标题只含一侧（中国 OR 阿拉伯），但必须含文旅话题词 —— 扩展集
    两层分开计数、分开标注，最终合成 3000~4000 篇纯英文语料。

用法：
    python fetch_en_crossref.py --outdir data_en_crossref --target 3500
    python fetch_en_crossref.py --outdir data_en_crossref --target 3500 --rows 500
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import time
import unicodedata
import urllib.parse

API = "https://api.crossref.org/works"
SELECT = ("DOI,title,type,abstract,author,container-title,issued,"
          "is-referenced-by-count,publisher,URL,subject,short-title")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crossref_cache")

# ---------------- 文献类型：保留「文献」类，剔除数据集/评审记录/资助等 ----------------
KEEP_TYPES = {
    "journal-article",      # 期刊论文
    "book-chapter",         # 图书章节
    "book",                 # 图书
    "monograph",            # 专著
    "edited-book",          # 编著
    "reference-book",       # 工具书/参考书
    "proceedings-article",  # 会议论文
    "report",               # 报告
    "posted-content",       # 预印本/预发表
    "dissertation",         # 学位论文
    "standard",             # 标准（少量，保留可标注）
    "reference-entry",      # 参考条目
}
TYPE_LABEL = {
    "journal-article": "期刊论文", "book-chapter": "图书章节", "book": "图书",
    "monograph": "专著", "edited-book": "编著", "reference-book": "参考书",
    "proceedings-article": "会议论文", "report": "报告", "posted-content": "预印本",
    "dissertation": "学位论文", "standard": "标准", "reference-entry": "参考条目",
}

# ---------------- 中国侧 / 阿拉伯侧标记（标题上做确定性词边界匹配） ----------------
CHINA_MARKERS = ["china", "chinese", "sino", "prc", "beijing", "shanghai", "hong kong", "macau"]
ARAB_MARKERS = [
    "arab", "arabs", "arabic", "arabia", "saudi", "emirates", "emirati", "dubai",
    "abu dhabi", "qatar", "qatari", "kuwait", "bahrain", "oman", "omani", "jordan",
    "morocco", "moroccan", "algeria", "algerian", "tunisia", "tunisian", "libya",
    "egypt", "egyptian", "iraq", "iraqi", "syria", "syrian", "lebanon", "lebanese",
    "yemen", "sudan", "palestine", "palestinian", "mauritania", "somalia", "djibouti",
    "comoros", "gulf", "middle east", "mecca", "medina", "makkah", "riyadh", "doha",
    "cairo", "muscat", "manama", "amman", "rabat", "tunis", "algiers", "baghdad",
    "damascus", "beirut", "halal", "islamic", "muslim",
]
# 文旅话题词（标题必须命中至少一个，才算「文旅」语料）
TOPIC_MARKERS = [
    "tourism", "tourist", "tourists", "touristic", "travel", "traveller", "traveler",
    "heritage", "cultural", "culture", "museum", "museums", "hospitality", "hotel",
    "hotels", "destination", "destinations", "pilgrimage", "pilgrim", "hajj", "umrah",
    "sightseeing", "leisure", "resort", "attraction", "attractions", "silk road",
    "belt and road", "unesco", "world heritage", "intangible", "archaeological",
    "cuisine", "festival", "homestay", "visitor", "visitors", "vacation", "itinerary",
    "tourism industry", "eco-tourism", "ecotourism", "cultural exchange", "expo",
]
# 核心文旅词：用于给语料打相关度等级（"cultural"/"culture" 单用太宽，故不算核心）
CORE_TOPIC_MARKERS = {
    "tourism", "tourist", "tourists", "touristic", "travel", "traveller", "traveler",
    "heritage", "museum", "museums", "hospitality", "hotel", "hotels", "destination",
    "destinations", "pilgrimage", "pilgrim", "hajj", "umrah", "sightseeing", "resort",
    "attraction", "attractions", "tourism industry", "eco-tourism", "ecotourism",
    "cultural tourism", "cultural exchange", "world heritage", "silk road",
}
GRADE_LABEL = {
    "A_双边_文旅核心": "双边（中国∩阿拉伯）且命中核心文旅词",
    "B_双边_弱文旅": "双边（中国∩阿拉伯）但仅命中宽泛文化词/一带一路",
    "C_单边_文旅核心": "单边（中国或阿拉伯）且命中核心文旅词",
    "D_单边_弱文旅": "单边（中国或阿拉伯）但仅命中宽泛文化词/一带一路",
}


def grade_of(sides, topic_hits):
    strong = bool(set(topic_hits) & CORE_TOPIC_MARKERS)
    if sides == "both":
        return "A_双边_文旅核心" if strong else "B_双边_弱文旅"
    return "C_单边_文旅核心" if strong else "D_单边_弱文旅"

# ---------------- 检索式：中国 × 阿拉伯国家 × 文旅话题 ----------------
ARAB_ENTITIES = [
    "Arab", "Arabic", "Saudi Arabia", "Saudi", "UAE", "Emirates", "Dubai", "Abu Dhabi",
    "Qatar", "Kuwait", "Bahrain", "Oman", "Jordan", "Morocco", "Algeria", "Tunisia",
    "Libya", "Egypt", "Iraq", "Syria", "Lebanon", "Yemen", "Sudan", "Palestine",
    "Gulf", "Middle East", "Mecca", "Riyadh", "Doha", "Cairo",
]
CN_SUBJECTS = ["China", "Chinese", "Sino-Arab", "China-Arab"]
CORE_TOPICS = [
    "tourism", "tourists", "cultural heritage", "cultural tourism", "tourism cooperation",
    "hospitality", "museum", "travel",
]


def build_queries():
    qs = []
    # 1) 中国 × 每个阿拉伯实体 × 核心话题
    for c in CN_SUBJECTS[:2]:
        for e in ARAB_ENTITIES:
            for t in ("tourism", "tourists", "cultural heritage"):
                qs.append(f"{c} {e} {t}")
    # 2) 双边关系/合作类
    qs += [
        "China Arab tourism", "China-Arab tourism", "Sino-Arab tourism",
        "China Arab cultural exchange", "China Arab cultural cooperation",
        "China Arab tourism cooperation", "China Arab heritage",
        "Chinese tourists Middle East", "Chinese outbound tourists Arab",
        "Chinese outbound tourism Middle East", "China outbound tourism Gulf",
        "Arab tourism China market", "Arab tourists China", "Arab visitors China",
        "China halal tourism", "China Islamic tourism", "China Muslim tourism",
        "China Belt and Road tourism", "Belt and Road cultural tourism China",
        "Silk Road tourism China", "Silk Road heritage China",
        "China Gulf tourism cooperation", "China Saudi tourism cooperation",
        "China Egypt tourism cooperation", "China UAE tourism cooperation",
        "China Arab museum cooperation", "China Arab cultural heritage preservation",
        "China Arab hospitality industry", "China Arab travel market",
        "China Middle East tourism development", "China Arab civilization exchange tourism",
        "Chinese investment Arab tourism", "China Arab tourism policy",
        "Arab countries tourism China", "China Arab friendship tourism",
    ]
    # 去重保序
    seen, out = set(), []
    for q in qs:
        k = q.lower()
        if k not in seen:
            seen.add(k); out.append(q)
    return out


# ---------------- 工具 ----------------
def strip_tags(s):
    """Crossref 摘要为 JATS XML，剥离标签与多余空白。"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


_TOKEN_CACHE = {}
def _tokenize(text):
    return " " + re.sub(r"[^a-z0-9\s\-]", " ", (text or "").lower()) + " "


def _marker_re(w):
    key = w
    if key not in _TOKEN_CACHE:
        _TOKEN_CACHE[key] = re.compile(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])")
    return _TOKEN_CACHE[key]


def side_hits(title, abstract=""):
    """返回 dict(china=[...], arab=[...], topic=[...])，标题为主、摘要辅助。"""
    tt, ta = _tokenize(title), _tokenize(abstract)
    def hit(words):
        return [w for w in words if _marker_re(w).search(tt) or (ta != "  " and _marker_re(w).search(ta))]
    return {"china": hit(CHINA_MARKERS), "arab": hit(ARAB_MARKERS), "topic": hit(TOPIC_MARKERS)}


def is_latin(text):
    """纯英文判定：标题不得含 CJK / 阿拉伯字母 / 西里尔字母。"""
    if not text:
        return False
    if re.search(r"[\u4e00-\u9fff\u0600-\u06ff\u0400-\u04ff\u3040-\u30ff]", text):
        return False
    return bool(re.search(r"[A-Za-z]", text))


NON_LATIN_RE = re.compile(r"[\u4e00-\u9fff\u0600-\u06ff\u0400-\u04ff\u3040-\u30ff]")


def latin_only(text):
    """摘要去混语：部分出版社同时存英文与阿文/中文摘要，这里只保留最长的拉丁文段，
    保证「纯英文」口径。返回 (清理后文本, 是否发生清理)。"""
    if not text or not NON_LATIN_RE.search(text):
        return text, False
    segs = [s.strip() for s in NON_LATIN_RE.split(text)]
    segs = [s for s in segs if len(s) > 40]
    if not segs:
        return "", True
    return max(segs, key=len), True


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def http_get_json(url, retries=6, pause=0.4):
    cp = os.path.join(CACHE_DIR, hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    last = None
    for attempt in range(retries):
        try:
            out = subprocess.run(["curl", "-s", "--max-time", "90", "-w", "\n%{http_code}", url],
                                 capture_output=True, text=True, check=False)
            body, _, code_s = out.stdout.rpartition("\n")
            code = int(code_s.strip() or 0)
            if code == 429:
                wait = min(120, 5 * (2 ** attempt))
                print(f"  [429] 等待 {wait}s 重试 ...", flush=True)
                time.sleep(wait); continue
            if code != 200 or not body.strip():
                raise RuntimeError(f"http={code} rc={out.returncode}")
            data = json.loads(body)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            time.sleep(pause)
            return data
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败: {url} ({last})")


def to_record(item, query, tier, sides, hits=None):
    year = None
    dp = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    if dp and dp[0]:
        year = int(dp[0])
    authors = []
    for a in (item.get("author") or [])[:20]:
        nm = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name") or ""
        if nm:
            authors.append(nm)
    ct = item.get("container-title") or []
    hits = hits or {}
    abstract, mixed = latin_only(strip_tags(item.get("abstract")))
    return {
        "id": item.get("DOI"),
        "doi": item.get("DOI", ""),
        "title": strip_tags((item.get("title") or [""])[0]),
        "abstract": abstract,
        "abstract_trimmed_mixed": mixed,
        "year": year,
        "type": item.get("type"),
        "type_label": TYPE_LABEL.get(item.get("type"), item.get("type")),
        "authors": "; ".join(authors)[:800],
        "venue": (ct[0] if ct else "") or item.get("publisher") or "",
        "publisher": item.get("publisher"),
        "cited_by_count": item.get("is-referenced-by-count", 0),
        "url": item.get("URL"),
        "source_db": "crossref",
        "language": "en",
        "query_language": "en",
        "tier": tier,
        "relation_sides": sides,
        "relevance_grade": grade_of(sides, hits.get("topic", [])),
        "relevance": {k: hits.get(k, []) for k in ("china", "arab", "topic")},
        "n_topic_hits": len(hits.get("topic", [])),
        "matched_keywords": [query],
        "crawl_query": query,
    }


def main():
    ap = argparse.ArgumentParser(description="纯英文中阿文旅文献抓取（Crossref）")
    ap.add_argument("--outdir", default="data_en_crossref")
    ap.add_argument("--target", type=int, default=3500, help="最终目标篇数（3000~4000）")
    ap.add_argument("--rows", type=int, default=500, help="每条检索式取前 N 条（Crossref 上限 1000）")
    ap.add_argument("--years", default="2010-2026")
    ap.add_argument("--mailto", default="research@bisu.edu.cn")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-queries", type=int, default=0, help="调试用：只跑前 N 条检索式")
    args = ap.parse_args()

    random.seed(args.seed)
    y0, y1 = (args.years.split("-") + [""])[:2]
    y0, y1 = int(y0), int(y1 or 9999)
    os.makedirs(os.path.join(args.outdir, "raw"), exist_ok=True)

    queries = build_queries()
    if args.max_queries:
        queries = queries[:args.max_queries]
    print(f"[Crossref] 检索式 {len(queries)} 条，每条前 {args.rows} 条，年份 {y0}-{y1}")

    recs, seen_doi, seen_title = [], set(), set()
    stats = {"raw_items": 0, "type_dropped": 0, "lang_dropped": 0, "year_dropped": 0,
             "topic_dropped": 0, "dupe_dropped": 0}
    for qi, q in enumerate(queries, 1):
        url = (f"{API}?query.title={urllib.parse.quote(q)}&rows={args.rows}"
               f"&select={SELECT}&mailto={args.mailto}"
               f"&filter=from-pub-date:{y0}-01-01,until-pub-date:{y1}-12-31")
        try:
            data = http_get_json(url)
        except Exception as e:
            print(f"  [{qi}/{len(queries)}] 失败跳过: {q} ({e})", flush=True)
            continue
        items = (data.get("message") or {}).get("items") or []
        stats["raw_items"] += len(items)
        kept_here = 0
        for it in items:
            if it.get("type") not in KEEP_TYPES:
                stats["type_dropped"] += 1; continue
            title = strip_tags((it.get("title") or [""])[0])
            if not is_latin(title):
                stats["lang_dropped"] += 1; continue
            year = ((it.get("issued") or {}).get("date-parts") or [[None]])[0][0]
            if not year or not (y0 <= int(year) <= y1):
                stats["year_dropped"] += 1; continue
            abstract = strip_tags(it.get("abstract"))
            h = side_hits(title, abstract)
            if not h["topic"]:
                stats["topic_dropped"] += 1; continue
            c, a = bool(h["china"]), bool(h["arab"])
            if c and a:
                tier, sides = "strict", "both"
            elif c or a:
                tier, sides = "relaxed", ("china" if c else "arab")
            else:
                stats["topic_dropped"] += 1; continue
            doi = (it.get("DOI") or "").lower()
            nt = normalize_title(title)
            if (doi and doi in seen_doi) or (nt and nt in seen_title):
                stats["dupe_dropped"] += 1; continue
            if doi:
                seen_doi.add(doi)
            seen_title.add(nt)
            recs.append(to_record(it, q, tier, sides, h))
            kept_here += 1
        if qi % 10 == 0 or qi == len(queries):
            print(f"  [{qi}/{len(queries)}] 累计入库 {len(recs)} 篇 | 最新检索式: {q}", flush=True)

    strict = [r for r in recs if r["tier"] == "strict"]
    relaxed = [r for r in recs if r["tier"] == "relaxed"]
    cn_only = [r for r in relaxed if r["relation_sides"] == "china"]
    ar_only = [r for r in relaxed if r["relation_sides"] == "arab"]
    print(f"\n[过滤后] strict(双边)={len(strict)} | relaxed 中国侧={len(cn_only)} 阿拉伯侧={len(ar_only)}")

    # ---- 组语料：strict 全收；relaxed 优先「中国侧」，不足再用「阿拉伯侧」补齐 ----
    # 侧内按 话题词命中数 → 被引 → 标题 排序（确定性，可复现），保证主题信息量高的先入
    key = lambda r: (-r.get("n_topic_hits", 0), -(r.get("cited_by_count") or 0), r.get("title") or "")
    cn_only.sort(key=key); ar_only.sort(key=key)
    quota = max(0, args.target - len(strict))
    # 中国侧 : 阿拉伯侧 ≈ 6:4 —— 项目现有语料缺阿拉伯侧内容，不能全填中国侧
    want_cn = int(quota * 0.6)
    take_cn = cn_only[:min(want_cn, len(cn_only))]
    take_ar = ar_only[:min(max(0, quota - len(take_cn)), len(ar_only))]
    # 一侧不够时用另一侧补足
    if len(take_cn) + len(take_ar) < quota:
        take_ar += ar_only[len(take_ar):len(take_ar) + (quota - len(take_cn) - len(take_ar))]
    if len(take_cn) + len(take_ar) < quota:
        take_cn += cn_only[len(take_cn):len(take_cn) + (quota - len(take_cn) - len(take_ar))]
    final = strict + take_cn + take_ar
    final.sort(key=lambda r: (0 if r["tier"] == "strict" else 1, -(r.get("year") or 0), r.get("title") or ""))

    # ---- 落盘 ----
    raw_path = os.path.join(args.outdir, "raw", "en.jsonl")
    with open(raw_path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(os.path.join(args.outdir, "combined.jsonl"), "w", encoding="utf-8") as f:
        for r in final:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    fields = ["query_language", "tier", "relation_sides", "relevance_grade", "title", "year",
              "type", "type_label", "venue", "publisher", "authors", "doi", "url",
              "cited_by_count", "crawl_query", "has_abstract"]
    with open(os.path.join(args.outdir, "combined.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in final:
            rr = dict(r); rr["has_abstract"] = bool(r.get("abstract"))
            w.writerow(rr)

    from collections import Counter
    def dist(rs, key):
        return dict(Counter((r.get(key) or "未知") for r in rs).most_common())
    report = {
        "source": "crossref",
        "note": "OpenAlex 当日额度耗尽(429, retry-after≈17h)，改用 Crossref 通道",
        "years": args.years, "target": args.target, "queries": len(queries),
        "rows_per_query": args.rows,
        "filter_stats": stats,
        "pool": {"strict_both": len(strict), "relaxed_china_only": len(cn_only),
                 "relaxed_arab_only": len(ar_only), "after_dedup_total": len(recs)},
        "final_total": len(final),
        "final_by_tier": dist(final, "tier"),
        "final_by_type": dist(final, "type_label"),
        "final_by_year": dict(sorted(Counter(r.get("year") for r in final).items())),
        "final_by_sides": dist(final, "relation_sides"),
        "final_by_grade": dist(final, "relevance_grade"),
        "with_abstract": sum(1 for r in final if r.get("abstract")),
        "abstract_trimmed_mixed": sum(1 for r in final if r.get("abstract_trimmed_mixed")),
        "with_doi": sum(1 for r in final if r.get("doi")),
    }
    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n===== 完成 =====  最终 {len(final)} 篇 -> {args.outdir}/combined.jsonl")
    print(f"分层: {report['final_by_tier']}")
    print(f"相关度等级: {report['final_by_grade']}")
    print(f"文献类型: {report['final_by_type']}")
    print(f"有无摘要: {report['with_abstract']}/{len(final)}")


if __name__ == "__main__":
    main()
