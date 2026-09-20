# -*- coding: utf-8 -*-
"""
clean_dimensions_en.py —— 英文语料「三维度」清洗（中国 + 阿拉伯 + 文旅）

背景（按反馈修缮英文部分）：
  - 反馈指出：英文未按「中国 + 阿拉伯 + 文旅」三维度清洗；且「文旅」定义偏窄，
    只到「文化遗产 / heritage」，未纳入「经济合作 / BRI 政策」等泛文旅内容。
  - 本脚本对英文语料做三维度过滤：CN(中国侧) AND AR(阿拉伯侧) AND TOURISM(文旅)。
    其中 TOURISM 采用**宽文旅**口径：旅游(强词) + 文化遗产 + 经济合作 + BRI(弱词)。

用法：
  python clean_dimensions_en.py \
      --inputs data_en_crossref/raw/en.jsonl,data/raw/en.jsonl \
      --outdir data_en_clean

输出：
  data_en_clean/cleaned_en.jsonl    三维度达标记录
  data_en_clean/cleaned_en.csv      同上（表格）
  data_en_clean/report.json         清洗报告（各维度/各来源/弱强文旅分布）
"""
import argparse
import csv
import json
import os
import re
from collections import Counter

# ---------------------------------------------------------------------------
# CN 维度：中国侧（英文）
# ---------------------------------------------------------------------------
CN_TERMS = [
    "china", "chinese", "sino", "prc", "beijing", "shanghai",
    "hong kong", "macau", "macao", "mainland china", "greater china",
    "chinese mainland", "中国",
]

# ---------------------------------------------------------------------------
# AR 维度：阿拉伯侧（英文，含反馈指出的国家）
# ---------------------------------------------------------------------------
AR_TERMS = [
    "arab", "arabs", "arabic", "arabia", "saudi", "emirates", "emirati",
    "dubai", "abu dhabi", "qatar", "qatari", "kuwait", "kuwaiti", "bahrain",
    "bahraini", "oman", "omani", "jordan", "jordanian", "morocco", "moroccan",
    "algeria", "algerian", "tunisia", "tunisian", "libya", "libyan", "egypt",
    "egyptian", "iraq", "iraqi", "syria", "syrian", "lebanon", "lebanese",
    "yemen", "yemeni", "sudan", "sudanese", "palestine", "palestinian",
    "mauritania", "mauritanian", "somalia", "somali", "djibouti", "comoros",
    "persian gulf", "arabian gulf", "gulf cooperation council", "gulf states",
    "gulf countries", "gulf region", "arab gulf", "middle east", "middle eastern",
    "mecca", "medina", "makkah",
    "riyadh", "doha", "cairo", "muscat", "manama", "amman", "rabat", "tunis",
    "algiers", "baghdad", "damascus", "beirut", "halal", "islamic", "muslim",
    "gcc", "gulf cooperation council", "red sea", "oic",
    "organization of islamic cooperation", "阿拉伯",
]

# ---------------------------------------------------------------------------
# 文旅维度（宽口径）
# ---------------------------------------------------------------------------
# 强词：旅游本体
TOURISM_STRONG = [
    "tourism", "tourist", "tourists", "touristic", "travel", "traveller",
    "traveler", "travellers", "travelers", "heritage", "museum", "museums",
    "hospitality", "hotel", "hotels", "destination", "destinations",
    "pilgrimage", "pilgrim", "pilgrims", "hajj", "umrah", "sightseeing",
    "leisure", "resort", "resorts", "attraction", "attractions", "tour",
    "tours", "homestay", "visitor", "visitors", "vacation", "itinerary",
    "cuisine", "festival", "festivals", "cruise", "archaeological",
    "cultural tourism", "cultural exchange", "world heritage",
    "tourism industry", "tourist industry", "eco tourism", "ecotourism",
    "intangible cultural heritage", "旅游", "文旅", "文化遗产",
]
# 弱词：经济合作 / BRI 等泛文旅（反馈要求纳入；用具体短语，不用泛词，
#       避免 "cultural"/"cooperation" 这类把无关文献也带进来）
TOURISM_WEAK = [
    "silk road", "maritime silk road", "belt and road", "one belt one road",
    "bri", "economic cooperation", "trade cooperation", "economic and trade",
    "economic corridor", "energy cooperation", "industrial cooperation",
    "free trade", "connectivity", "people to people", "soft power",
    "cultural diplomacy", "civilization exchange",
    "一带一路", "丝绸之路", "中阿",
]


def tokenize(text):
    return " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "


_re_cache = {}


def _term_re(term):
    if term not in _re_cache:
        _re_cache[term] = re.compile(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])")
    return _re_cache[term]


def hits(terms, tok):
    return [t for t in terms if _term_re(t).search(tok)]


def record_text(r):
    parts = [r.get("title") or "", r.get("abstract") or ""]
    mk = r.get("matched_keywords")
    if isinstance(mk, list):
        parts.append(" ".join(str(x) for x in mk))
    elif mk:
        parts.append(str(mk))
    rel = r.get("relevance")
    if isinstance(rel, dict):
        for v in rel.values():
            if isinstance(v, list):
                parts.append(" ".join(str(x) for x in v))
    return " ".join(parts)


def norm_title(t):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff\u0600-\u06ff]", "", (t or "").lower())


def main():
    ap = argparse.ArgumentParser(description="英文语料三维度清洗（CN+AR+文旅，宽文旅口径）")
    ap.add_argument("--inputs", required=True,
                    help="逗号分隔的英文 jsonl 列表")
    ap.add_argument("--outdir", default="data_en_clean")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    files = [f.strip() for f in args.inputs.split(",") if f.strip()]
    os.makedirs(args.outdir, exist_ok=True)

    per_source = {}
    all_rows = []
    for p in files:
        rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        per_source[p] = len(rows)
        all_rows += rows

    # 去重（DOI 优先，其次标题）
    seen_d, seen_t, uniq = set(), set(), []
    for r in sorted(all_rows, key=lambda x: -(x.get("cited_by_count") or 0)):
        d = (r.get("doi") or "").lower()
        t = norm_title(r.get("title"))
        if (d and d in seen_d) or (t and t in seen_t):
            continue
        if d:
            seen_d.add(d)
        if t:
            seen_t.add(t)
        uniq.append(r)

    stats = Counter()
    cleaned = []
    for r in uniq:
        tok = tokenize(record_text(r))
        cn = hits(CN_TERMS, tok)
        ar = hits(AR_TERMS, tok)
        strong = hits(TOURISM_STRONG, tok)
        weak = hits(TOURISM_WEAK, tok)
        tourism = bool(strong or weak)
        ok = bool(cn and ar and tourism)

        r["dim_cn"] = sorted(set(cn))
        r["dim_ar"] = sorted(set(ar))
        r["dim_tourism_strong"] = sorted(set(strong))
        r["dim_tourism_weak"] = sorted(set(weak))
        r["dim_ok"] = ok
        r["dim_tourism_level"] = ("strong" if strong else ("weak" if weak else "none"))

        stats["total_dedup"] += 1
        if cn:
            stats["cn_ok"] += 1
        if ar:
            stats["ar_ok"] += 1
        if tourism:
            stats["tourism_ok"] += 1
        if ok:
            stats["pass"] += 1
            if strong:
                stats["pass_strong"] += 1
            else:
                stats["pass_weak_only"] += 1
        if cn and ar and not tourism:
            stats["cn_ar_no_tourism"] += 1
        if ok:
            cleaned.append(r)

    out_jsonl = os.path.join(args.outdir, "cleaned_en.jsonl")
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for r in cleaned:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    fields = ["dim_ok", "dim_tourism_level", "title", "year", "type", "type_label",
              "venue", "authors", "doi", "url", "source_db",
              "dim_cn", "dim_ar", "dim_tourism_strong", "dim_tourism_weak"]
    with open(os.path.join(args.outdir, "cleaned_en.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in cleaned:
            rr = dict(r)
            for k in ("dim_cn", "dim_ar", "dim_tourism_strong", "dim_tourism_weak"):
                rr[k] = "; ".join(rr.get(k) or [])
            w.writerow(rr)

    report = {
        "inputs": per_source,
        "input_raw_total": sum(per_source.values()),
        "total_dedup": stats["total_dedup"],
        "dimension_coverage": {
            "cn_ok": stats["cn_ok"],
            "ar_ok": stats["ar_ok"],
            "tourism_ok": stats["tourism_ok"],
        },
        "pass_all_three": stats["pass"],
        "pass_strong_tourism": stats["pass_strong"],
        "pass_weak_tourism_only": stats["pass_weak_only"],
        "cn_ar_but_no_tourism": stats["cn_ar_no_tourism"],
        "pass_rate_of_dedup": round(stats["pass"] / stats["total_dedup"], 4) if stats["total_dedup"] else None,
    }
    rp = args.report or os.path.join(args.outdir, "report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("================ 英文三维度清洗 ================")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"达标记录 -> {out_jsonl}")


if __name__ == "__main__":
    main()
