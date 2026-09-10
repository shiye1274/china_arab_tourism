# -*- coding: utf-8 -*-
"""
merge.py —— 合并三个组员的抓取结果（你:ar / 组员A:en / 组员C:zh）

跨语言查重：优先按 DOI 判重（同一论文的多语言版本通常共享 DOI），
无 DOI 再按归一化标题判重；重复记录保留被引更高的一篇，其余计入 removed。

用法：
  python merge.py --dirs data_you_ar,data_A_en,data_C_zh --out merged
输出：
  merged/all.jsonl | all.csv      合并后的最终语料
  merged/merge_report.json        各语言数量 + 跨语言去重明细
"""
import argparse
import csv
import json
import os
import re

LANGS = {"en": "英语", "zh": "中文", "ar": "阿拉伯语"}


def norm_title(t):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff\u0600-\u06ff]", "", (t or "").lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", required=True, help="逗号分隔的多个 outdir")
    ap.add_argument("--out", default="merged")
    args = ap.parse_args()

    records, stats = [], {}
    for d in args.dirs.split(","):
        d = d.strip()
        p = os.path.join(d, "combined.jsonl")
        if not os.path.exists(p):
            print(f"跳过不存在: {p}")
            continue
        with open(p, encoding="utf-8") as f:
            rs = [json.loads(l) for l in f if l.strip()]
        records += rs
        lang = rs[0]["query_language"] if rs else "?"
        stats[lang] = len(rs)
        print(f"{d}: {len(rs)} 篇 ({LANGS.get(lang, lang)})")

    seen_d, seen_t = {}, {}
    kept, removed = [], []
    for r in sorted(records, key=lambda x: -(x.get("cited_by_count") or 0)):
        doi = (r.get("doi") or "").lower()
        nt = norm_title(r.get("title"))
        dup = None
        if doi and doi in seen_d:
            dup = seen_d[doi]
        elif nt and nt in seen_t:
            dup = seen_t[nt]
        if dup:
            removed.append({"kept": dup.get("title"), "removed": r.get("title"),
                            "why": "doi" if (doi and doi in seen_d) else "title"})
        else:
            if doi:
                seen_d[doi] = r
            if nt:
                seen_t[nt] = r
            kept.append(r)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "all.jsonl"), "w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    fields = ["query_language", "title", "year", "venue", "authors", "doi",
              "openalex_url", "matched_keywords", "relation_sides",
              "cited_by_count", "type"]
    with open(os.path.join(args.out, "all.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in kept:
            w.writerow(r)

    out_stats = {}
    for r in kept:
        out_stats[r["query_language"]] = out_stats.get(r["query_language"], 0) + 1
    report = {"input_per_dir": stats, "merged_by_language": out_stats,
              "total_merged": len(kept), "cross_lang_duplicates_removed": len(removed),
              "removed_examples": removed[:30]}
    with open(os.path.join(args.out, "merge_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n合并后:", out_stats, "共", len(kept), "篇")
    print(f"跨语言重复剔除 {len(removed)} 篇（详见 {args.out}/merge_report.json）")
    print(f"输出 -> {args.out}/all.jsonl | all.csv")


if __name__ == "__main__":
    main()
