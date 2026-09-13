# -*- coding: utf-8 -*-
"""
validate.py —— 语料准确性校验（判断抓得准不准）

四层校验：
  1) 语言校验：用「文字系统特征」独立检测每篇的标题+摘要语言（CJK/阿拉伯字母/拉丁字母），
     与 OpenAlex 标注的 language 字段比对，算出一致率。检测逻辑对中英阿三语是确定性的。
  2) 主题相关度：统计该篇标题/摘要里实际命中了多少条检索短语、命中的是哪些。
  3) 查重：标题归一化 + DOI 双通道查重（含跨语言重复，如同一篇论文有中英双版本）。
  4) DOI 真实性抽查：抽 N 篇带 DOI 的记录，去 Crossref 确认该 DOI 真实存在。

用法：
  python validate.py --data data --crossref-sample 30
输出：
  data/validation_report.json     机器可读报告
  data/manual_review_sample.csv   每语言随机抽 15 篇供你逐条人工阅读核验
"""
import argparse
import csv
import json
import os
import random
import re
import subprocess
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"']+")

# 与 fetch_papers.py 保持一致的词表，用于相关度复算
KEYWORDS = {
    "en": ["cultural tourism", "China Arab tourism", "sino-arab tourism", "belt and road tourism",
           "silk road tourism", "china arab cultural exchange", "arab heritage tourism",
           "islamic tourism", "halal tourism", "heritage tourism china",
           # 与 fetch_papers.py 的多词 AND 检索层保持一致（前缀 "!"）
           "!china arab tourism", "!china arab cultural tourism", "!china arab cooperation",
           "!chinese arab tourism", "!chinese tourism arab", "!sino-arab",
           "!china gulf tourism", "!china saudi tourism", "!china emirates tourism",
           "!china egypt tourism", "!chinese tourists gulf", "!chinese tourists middle east",
           "!china middle east tourism", "!china arab cultural exchange", "!china arab heritage",
           "!belt and road tourism", "!belt and road arab", "!belt and road cultural heritage",
           "!silk road tourism china", "!silk road heritage tourism", "!arab tourism china",
           "!arab tourists china", "!arab countries tourism cooperation", "!halal tourism china",
           "!islamic tourism china", "!arab cultural heritage tourism"],
    "zh": ["文化旅游", "文旅融合", "中阿旅游", "中阿文旅", "阿拉伯旅游", "阿拉伯国家旅游",
           "一带一路旅游", "丝绸之路旅游", "文化遗产旅游", "出入境旅游"],
    "ar": ["السياحة", "التراث الثقافي", "المتاحف", "التنمية السياحية", "صناعة السياحة",
           "الموروث الثقافي", "السياحة البيئية", "الحج والعمرة", "التسويق السياحي",
           "السياحة المستدامة", "السياحة الثقافية", "السياحة الدينية", "السياحة الصحراوية",
           "المعالم الأثرية", "التبادل الثقافي", "مبادرة الحزام والطريق", "طريق الحرير",
           "السفر والسياحة", "السياحة العلاجية", "صناعة الضيافة", "السياحة الريفية",
           "السياحة التراثية", "التعاون السياحي", "السياحة في الصين", "طريق الحرير السياحي",
           "التبادل الثقافي العربي الصيني", "السياحة الحلال", "العلاقات الصينية العربية",
           "التعاون الصيني العربي", "الصين والعالم العربي", "السياح الصينيون",
           "السياحية", "السياحي", "السائحين", "السائح", "التراث", "المتحف", "السفر",
           "الحج", "العمرة", "الآثار", "الفندقة", "الضيافة", "الرحلات", "المنتجعات"],
}
LANG_LABEL = {"en": "英语", "zh": "中文", "ar": "阿拉伯语"}


def detect_script_lang(text):
    """独立语言检测：按文字系统判定（确定性方法，对中/英/阿足够可靠）。"""
    t = text or ""
    ar, cjk, la = len(ARABIC_RE.findall(t)), len(CJK_RE.findall(t)), len(LATIN_RE.findall(t))
    if ar > la and ar >= cjk:
        return "ar"
    if cjk > la:
        return "zh"
    if la > 0:
        return "en"
    return "unknown"


def normalize_title(t):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff\u0600-\u06ff]", "", (t or "").lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="fetch_papers.py 的 --outdir")
    ap.add_argument("--source-file", default=None,
                    help="直接校验某个 jsonl（如 data_en_crossref/combined.jsonl）。"
                         "不填则读 <data>/raw/{en,zh,ar}.jsonl")
    ap.add_argument("--crossref-sample", type=int, default=30, help="抽多少篇去 Crossref 验 DOI")
    ap.add_argument("--review-per-lang", type=int, default=15, help="人工抽检每语言多少篇")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)

    rows = []
    out_dir = os.path.dirname(args.source_file) if args.source_file else args.data
    if args.source_file:
        with open(args.source_file, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    else:
        for lang, label in LANG_LABEL.items():
            p = os.path.join(args.data, "raw", f"{lang}.jsonl")
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rows.append(json.loads(line))

    if not rows:
        print("没有找到 raw/*.jsonl，请先运行 fetch_papers.py")
        return

    # ---- 1) 语言一致性 ----
    per_lang = defaultdict(lambda: {"agree": 0, "n": 0, "detected": Counter()})
    lang_agree_total = 0
    for r in rows:
        text = (r.get("title") or "") + " " + (r.get("abstract") or "")
        det = detect_script_lang(text)
        gl = r.get("query_language")
        per_lang[gl]["n"] += 1
        per_lang[gl]["detected"][det] += 1
        if det == gl:
            per_lang[gl]["agree"] += 1
            lang_agree_total += 1
        r["script_detected_lang"] = det
        r["lang_consistent"] = (det == gl)

    # ---- 2) 主题相关度：命中短语数（独立复算，非抓取时记录的） ----
    def _kw_hit(kw, text):
        """复算单个检索词是否命中。前缀 '!' 表示多为隐式 AND 检索：
        要求所有词元（≥3 字符）都出现在文本中，避免长词表整体子串匹配失效。"""
        if kw.startswith("!"):
            toks = [t for t in re.split(r"[\s\-]+", kw[1:].lower()) if len(t) >= 3]
            return bool(toks) and all(t in text for t in toks)
        return kw in text

    for r in rows:
        text = ((r.get("title") or "") + " " + (r.get("abstract") or "")).lower()
        kws = KEYWORDS.get(r.get("query_language"), [])
        hits = [k for k in kws if _kw_hit(k, text)]
        # Crossref 通道：记录自带确定性标记命中（中国侧/阿拉伯侧/文旅话题词），
        # 这才是该语料真正的纳入依据（见 fetch_en_crossref.py），故独立统计。
        rel = r.get("relevance")
        if rel:
            marker = {k: rel.get(k, []) for k in ("china", "arab", "topic")}
            r["marker_hits"] = marker
            r["marker_ok"] = bool(marker["topic"]) and (bool(marker["china"]) or bool(marker["arab"]))
        r["relevance_hits"] = hits
        r["relevance_hit_count"] = len(hits)

    marker_rows = [r for r in rows if "marker_ok" in r]
    marker_ok = [r for r in marker_rows if r["marker_ok"]]
    zero_hit = [r for r in rows
                if r["relevance_hit_count"] == 0 and r.get("marker_ok") is not True]
    lang_inconsistent = [r for r in rows if not r["lang_consistent"]]

    # ---- 3) 查重（标题 + DOI 双通道，跨语言也算） ----
    seen_t, seen_d = {}, {}
    dup_groups = []
    for i, r in enumerate(rows):
        nt = normalize_title(r.get("title"))
        doi = (r.get("doi") or "").lower()
        dup_with = None
        if nt and nt in seen_t:
            dup_with = seen_t[nt]
        elif doi and doi in seen_d:
            dup_with = seen_d[doi]
        if dup_with is not None:
            dup_groups.append((i, dup_with, nt or doi))
        else:
            if nt:
                seen_t[nt] = i
            if doi:
                seen_d[doi] = i
    # 注：上面只标记“后出现者”，同组多次重复会形成链，汇总即可

    # ---- 4) DOI 真实性抽查（Crossref） ----
    doi_rows = [r for r in rows if r.get("doi")]
    sample = random.sample(doi_rows, min(args.crossref_sample, len(doi_rows)))
    crossref_ok, crossref_fail = 0, []
    for r in sample:
        try:
            url = "https://api.crossref.org/works/" + urllib.parse.quote(r["doi"])
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "paper-validator/0.1"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    ok = resp.status == 200
            except Exception:
                out = subprocess.run(["curl", "-s", "-o", os.devnull, "-w", "%{http_code}",
                                      "--max-time", "60", url], capture_output=True, text=True)
                ok = out.stdout.strip() == "200"
            if ok:
                crossref_ok += 1
            else:
                crossref_fail.append(r["doi"])
        except Exception:
            crossref_fail.append(r["doi"])

    # ---- 汇总报告 ----
    lang_report = {}
    for gl, st in per_lang.items():
        lang_report[gl] = {
            "label": LANG_LABEL.get(gl, gl),
            "n": st["n"],
            "lang_agreement_rate": round(st["agree"] / st["n"], 4) if st["n"] else None,
            "detected_distribution": dict(st["detected"]),
        }
    report = {
        "total_records": len(rows),
        "language_check": lang_report,
        "relevance": {
            "records_with_zero_keyword_hit": len(zero_hit),
            "zero_hit_ratio": round(len(zero_hit) / len(rows), 4) if rows else None,
            "mean_hits_per_record": round(sum(r["relevance_hit_count"] for r in rows) / len(rows), 2) if rows else 0,
            "marker_based": {
                "records_with_marker_fields": len(marker_rows),
                "passed_own_inclusion_rule": len(marker_ok),
                "pass_rate": round(len(marker_ok) / len(marker_rows), 4) if marker_rows else None,
            } if marker_rows else None,
        },
        "duplicates": {"flagged_pairs_or_chains": len(dup_groups), "example_indices": dup_groups[:20]},
        "doi_crossref_check": {
            "checked": len(sample),
            "resolved_ok": crossref_ok,
            "failed": crossref_fail[:20],
        },
    }
    with open(os.path.join(out_dir, "validation_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # ---- 人工抽检表：每语言随机 N 篇，含摘要前 200 字，方便逐条读 ----
    review_fields = ["query_language", "tier", "relation_sides", "marker_ok", "lang_consistent",
                     "script_detected_lang", "relevance_hit_count", "relevance_hits", "title",
                     "year", "type_label", "venue", "authors", "doi", "url", "abstract_snippet"]
    review_rows = []
    by_lang = defaultdict(list)
    for r in rows:
        by_lang[r["query_language"]].append(r)
    for gl, rs in by_lang.items():
        for r in random.sample(rs, min(args.review_per_lang, len(rs))):
            rr = dict(r)
            rr["abstract_snippet"] = (r.get("abstract") or "")[:200]
            review_rows.append(rr)
    with open(os.path.join(out_dir, "manual_review_sample.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=review_fields, extrasaction="ignore")
        w.writeheader()
        for r in review_rows:
            w.writerow(r)

    # ---- 终端摘要 ----
    print("================ 准确性校验结果 ================")
    for gl, st in lang_report.items():
        print(f"[{st['label']}] n={st['n']}  语言一致率={st['lang_agreement_rate']}")
        print(f"    检出语言分布: {st['detected_distribution']}")
    print(f"相关度: 平均每篇命中 {report['relevance']['mean_hits_per_record']} 条短语；"
          f"0 命中记录 {report['relevance']['records_with_zero_keyword_hit']} 条 "
          f"({report['relevance']['zero_hit_ratio']})")
    if report["relevance"].get("marker_based"):
        mb = report["relevance"]["marker_based"]
        print(f"纳入规则自检: 带标记字段 {mb['records_with_marker_fields']} 条，"
              f"通过「话题词+中/阿侧」规则 {mb['passed_own_inclusion_rule']} 条 "
              f"(通过率 {mb['pass_rate']})")
    print(f"查重: 疑似重复 {len(dup_groups)} 组")
    print(f"DOI抽查: {crossref_ok}/{len(sample)} 在 Crossref 真实存在")
    print(f"人工抽检表 -> {os.path.join(out_dir, 'manual_review_sample.csv')}")
    if zero_hit:
        print("\n!! 注意以下记录 0 短语命中（可能跑题，建议人工确认后剔除）:")
        for r in zero_hit[:10]:
            print("   -", r.get("title"), "|", r.get("doi"))


if __name__ == "__main__":
    main()
