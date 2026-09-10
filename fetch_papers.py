# -*- coding: utf-8 -*-
"""
fetch_papers.py —— 中/英/阿 三语文旅论文抓取（基于 OpenAlex 官方 API，免费、无需 Key、合法）

策略：
  1) 英语/阿拉伯语：用关键词短语走 OpenAlex 的 title_and_abstract.search 精确短语检索，
     再把短语匹配情况记录到 matched_keywords，供后续准确性校验用。
  2) 中文：OpenAlex 对中文分词检索支持弱，改用「按语言 language:zh 流式拉取 + 本地
     CJK 子串包含判断」，判断是确定性的，不会漏匹配。
  3) 三语各自去重后，按 min(三语可得数量) 均衡抽样，保证比例大致相同。

  ★ 全文摘要抓取（--fetch-abstract）：
     当 OpenAlex 未提供摘要时，自动从开放获取论文的 HTML 页面或 PDF 中提取摘要。
     优先级：OpenAlex倒排索引 > HTML页面 > PDF

用法示例：
  python fetch_papers.py --outdir data --per-lang 60 --years 2010-2026
  python fetch_papers.py --outdir data --per-lang 200 --langs en ar     # 只抓英/阿
  python fetch_papers.py --outdir data --per-lang 100 --mailto you@x.edu
  python fetch_papers.py --outdir data --per-lang 60 --fetch-abstract  # 启用全文摘要抓取

输出：
  data/raw/en.jsonl, zh.jsonl, ar.jsonl     —— 每语言全量去重结果
  data/combined.jsonl / combined.csv        —— 均衡抽样后的最终语料
  data/report.json                          —— 数量与抽样报告
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from io import BytesIO

# 全文摘要抓取依赖（可选）
try:
    import requests as _requests
except ImportError:
    _requests = None
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
try:
    from PyPDF2 import PdfReader
except ImportError:
    PdfReader = None

API = "https://api.openalex.org/works"
PER_PAGE = 200                      # OpenAlex 单页上限
DEFAULT_MAILTO = "your.email@university.edu"  # 建议改成你自己的邮箱（礼貌池，速度更快）
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oa_cache")

# ---------------- 检索词表（可按自己的研究主题增删） ----------------
# 每门语言一组短语。英文/阿语短语交给 API 做精确短语匹配；中文短语在本地做子串包含判断。
KEYWORDS = {
    "en": [
        "cultural tourism",
        "China Arab tourism",
        "Sino-Arab tourism",
        "Belt and Road tourism",
        "silk road tourism",
        "China Arab cultural exchange",
        "Arab heritage tourism",
        "Islamic tourism",
        "halal tourism",
        "heritage tourism China",
        # ---- 双边补充短语（扩大“中国∩阿拉伯”AND 命中面）----
        "China-Arab cultural exchange",
        "Sino-Arab cooperation",
        "China and Arab tourism",
        "Chinese-Arab relations",
        "Belt and Road cultural cooperation",
    ],
    "zh": [
        "文化旅游",
        "文旅融合",
        "中阿旅游",
        "中阿文旅",
        "阿拉伯旅游",
        "阿拉伯国家旅游",
        "一带一路旅游",
        "丝绸之路旅游",
        "文化遗产旅游",
        "出入境旅游",
        # ---- 双边补充短语 ----
        "中阿文化交流",
        "中阿合作",
        "中阿博览会",
        "阿拉伯游客",
        "宁夏旅游",
    ],
    "ar": [
        # ---- 核心文旅话题（数量实测: 2026-09 快照, language:ar 2010-2026）----
        "السياحة",                    # 旅游(3429)
        "التراث الثقافي",              # 文化遗产(609)
        "المتاحف",                    # 博物馆(500)
        "التنمية السياحية",            # 旅游发展(485)
        "صناعة السياحة",               # 旅游产业(278)
        "الموروث الثقافي",             # 文化遗产(271)
        "السياحة البيئية",             # 生态旅游(201)
        "الحج والعمرة",                # 朝觐与副朝(167)
        "التسويق السياحي",             # 旅游营销(164)
        "السياحة المستدامة",           # 可持续旅游(117)
        "السياحة الثقافية",            # 文化旅游(108)
        "السياحة الدينية",             # 宗教旅游(97)
        "السياحة الصحراوية",           # 沙漠旅游(85)
        "المعالم الأثرية",             # 古迹(83)
        "التبادل الثقافي",             # 文化交流(78)
        "مبادرة الحزام والطريق",       # 一带一路(77)
        "طريق الحرير",                # 丝绸之路(63)
        "السفر والسياحة",              # 旅行与旅游(61)
        "السياحة العلاجية",            # 医疗旅游(49)
        "صناعة الضيافة",               # 酒店业(34)
        "السياحة الريفية",             # 乡村旅游
        # ---- 原有 12 条（含中阿双边短语）----
        "السياحة التراثية",            # 遗产旅游
        "التعاون السياحي",             # 旅游合作
        "السياحة في الصين",            # 中国旅游
        "طريق الحرير السياحي",         # 丝绸之路旅游
        "التبادل الثقافي العربي الصيني",  # 中阿文化交流
        "السياحة الحلال",              # 清真旅游
        "العلاقات الصينية العربية",    # 阿中关系
        "التعاون الصيني العربي",        # 中阿合作
        "الصين والعالم العربي",         # 中国与阿拉伯世界
        "السياح الصينيون",             # 中国游客
        # ---- 拓展层：词元级泛化词（提升召回）----
        # 阿语构词法: سياحة(名词)/سياحي·سياحية(形容词) 是不同的检索词元，
        # 只查 السياحة 会漏掉“التنمية السياحية/الخدمات السياحية/المنتجات السياحية”等
        # 大量只出现形容词形式、没出现名词形式的文献。故补词元级检索。
        "السياحية",                   # 旅游的(形容词,召回大头)
        "السياحي",                    # 旅游的(阳性形容词)
        "السائحين",                   # 游客们
        "السائح",                     # 游客
        "التراث",                     # 遗产(实测 language:ar 2010-2026 共 7808 条)
        "المتحف",                     # 博物馆(单数形式; المتاحف 复数已在上面)
        "السفر",                      # 旅行
        "الحج",                       # 朝觐
        "العمرة",                     # 副朝
        "الآثار",                     # 古迹/文物
        "الفندقة",                    # 酒店业
        "الضيافة",                    # 接待业
        "الرحلات",                    # 旅游行程/旅行团
        "المنتجعات",                  # 度假村
    ],
}
LANG_LABEL = {"en": "英语", "zh": "中文", "ar": "阿拉伯语"}

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# ---------------- “中国-阿拉伯”双边关系标记 ----------------
# 判断一篇文章是否属于“中阿之间”的文旅：strict 要求标题/摘要【同时】命中
# 中国侧标记 AND 阿拉伯侧标记；relaxed 要求至少命中一侧。
# 中文的“中阿”一词本身含中+阿，故同时放入两侧；英文 Sino-Arab / China-Arab 同理。
MARKERS = {
    "en": {
        "china": ["china", "chinese", "sino", "prc"],
        "arab": ["arab", "arabs", "arabic", "gulf", "middle east", "saudi", "emirates",
                 "dubai", "abu dhabi", "qatar", "kuwait", "bahrain", "oman", "jordan",
                 "morocco", "algeria", "tunisia", "libya", "egypt", "iraq", "syria",
                 "lebanon", "yemen", "sudan", "palestine", "mauritania", "somalia",
                 "djibouti", "comoros", "mecca", "medina", "makkah"],
    },
    "zh": {
        "china": ["中国", "我国", "中华", "来华", "赴华", "在华", "中阿"],
        "arab": ["阿拉伯", "沙特", "阿联酋", "迪拜", "埃及", "卡塔尔", "科威特", "巴林",
                 "阿曼", "约旦", "中东", "海湾", "摩洛哥", "突尼斯", "阿尔及利亚", "利比亚",
                 "伊拉克", "叙利亚", "黎巴嫩", "也门", "苏丹", "巴勒斯坦", "毛里塔尼亚",
                 "索马里", "吉布提", "科摩罗", "中阿", "赴阿", "访阿"],
    },
    "ar": {
        "china": ["الصين", "الصيني", "الصينية", "بكين", "شنغهاي"],
        "arab": ["العرب", "العربي", "العربية", "الخليج", "الشرق الاوسط", "السعودية",
                 "مصر", "الامارات", "دبي", "ابوظبي", "قطر", "الكويت", "البحرين", "عمان",
                 "اليمن", "العراق", "سوريا", "لبنان", "الاردن", "فلسطين", "المغرب",
                 "الجزائر", "تونس", "ليبيا", "السودان", "موريتانيا", "الصومال", "جيبوتي",
                 "مكة", "المدينة"],
    },
}
_EN_MARKER_PAT = {
    side: re.compile(r"\b(" + "|".join(re.escape(t) for t in toks) + r")\b")
    for side, toks in MARKERS["en"].items()
}


def ar_normalize(s):
    """阿拉伯文轻量归一化：NFKC + 去变音/塔特维尔 + 统一 أإآ→ا、ى/ی→ي 等变体，
    使 OpenAlex 里各种书写变体都能匹配上。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\u064B-\u0652\u0640]", "", s)
    for a, b in [("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"),
                 ("ى", "ي"), ("ی", "ي"), ("ؤ", "و")]:
        s = s.replace(a, b)
    return s


def relation_hits(lang, text):
    """返回该文本在 中国侧/阿拉伯侧 是否命中。返回 dict(china=bool, arab=bool)。"""
    if lang == "en":
        t = (text or "").lower()
        return {s: bool(p.search(t)) for s, p in _EN_MARKER_PAT.items()}
    t = ar_normalize(text) if lang == "ar" else (text or "")
    return {s: any(m in t for m in MARKERS[lang][s]) for s in ("china", "arab")}


def relation_filter(recs, lang, mode):
    """按中阿关系口径过滤记录，返回 (保留列表, 丢弃数量, 各命中侧统计)。
    mode: strict=中国AND阿拉伯 / relaxed=中国OR阿拉伯 / off=不过滤"""
    kept, dropped = [], 0
    sides = {"china_only": 0, "arab_only": 0, "both": 0, "neither": 0}
    for r in recs:
        text = (r.get("title") or "") + " " + (r.get("abstract") or "")
        h = relation_hits(lang, text)
        both, c, a = h["china"] and h["arab"], h["china"], h["arab"]
        key = "both" if both else ("china_only" if c else ("arab_only" if a else "neither"))
        sides[key] += 1
        if mode == "strict" and not both:
            dropped += 1
            continue
        if mode == "relaxed" and not (c or a):
            dropped += 1
            continue
        r["relation_sides"] = ("both" if both else ("china" if c else ("arab" if a else "none")))
        kept.append(r)
    return kept, dropped, sides


# ---------------- 工具函数 ----------------
_USE_CURL_ONLY = None  # None=未探测; True/False=本进程内 urllib 是否可用


class RateLimited(Exception):
    """HTTP 429：被 OpenAlex 限流，需等待更久后重试。"""


def _cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json")


def http_get_json(url, retries=10, pause=0.3):
    """带重试与限流退避的 GET，并对响应做磁盘缓存（断点续跑/重跑不重复消耗额度）。
    - 429：指数退避重试（上限 600s）；
    - 每次成功后 pause 秒；成功响应写入 oa_cache/ 供复用。"""
    global _USE_CURL_ONLY

    cp = _cache_path(url)
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)

    def _via_urllib():
        req = urllib.request.Request(url, headers={"User-Agent": "paper-fetcher/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited()
            raise

    def _via_curl():
        out = subprocess.run(
            ["curl", "-s", "--max-time", "60", "-w", "\n%{http_code}", url],
            capture_output=True, text=True, check=False)
        body, _, code_s = out.stdout.rpartition("\n")
        code = int(code_s.strip() or 0)
        if code == 429:
            raise RateLimited()
        if out.returncode != 0 or code != 200 or not body.strip():
            raise RuntimeError(f"curl failed rc={out.returncode} http={code}")
        return json.loads(body)

    for attempt in range(retries):
        try:
            if _USE_CURL_ONLY is False:
                data = _via_urllib()
            elif _USE_CURL_ONLY is True:
                data = _via_curl()
            else:  # 首次请求：探测 urllib 是否可用
                try:
                    data = _via_urllib()
                    _USE_CURL_ONLY = False
                except Exception:
                    _USE_CURL_ONLY = True
                    data = _via_curl()
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            time.sleep(pause)
            return data
        except RateLimited:
            wait = min(600, 10 * (2 ** attempt))
            print(f"  [429限流] 等待 {wait}s 后重试 ...", flush=True)
            time.sleep(wait)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("429 持续限流：额度可能已耗尽，请稍后重试（缓存已保存已抓页面）")


def abstract_from_inverted_index(inv):
    """OpenAlex 的摘要以倒排索引存储，这里还原成纯文本。"""
    if not inv:
        return ""
    pos_word = []
    for word, positions in inv.items():
        for p in positions:
            pos_word.append((p, word))
    pos_word.sort()
    return " ".join(w for _, w in pos_word)


# ── 全文摘要抓取 ──────────────────────────────────────────
# 常见学术网站摘要选择器（按优先级）
_ABSTRACT_SELECTORS = [
    'meta[name="description"]',
    'meta[property="og:description"]',
    '.abstract',
    '#abstract',
    '[data-test="abstract"]',
    '.abstract-content',
    '.article-abstract',
    'div[class*="abstract"]',
]


def _fetch_url_content(url, timeout=30):
    """下载 URL 内容，返回 bytes"""
    if _requests:
        resp = _requests.get(url, timeout=timeout,
                            headers={"User-Agent": "Mozilla/5.0 (paper-fetcher/1.0)"})
        resp.raise_for_status()
        return resp.content
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()


def fetch_abstract_from_html(url, timeout=30):
    """从 HTML 页面提取摘要"""
    if not BeautifulSoup:
        return ""
    try:
        html = _fetch_url_content(url, timeout)
        soup = BeautifulSoup(html, "html.parser")

        # 方法1: meta description
        for sel in ['meta[name="description"]', 'meta[property="og:description"]']:
            tag = soup.select_one(sel)
            if tag and tag.get("content"):
                text = tag["content"].strip()
                if len(text) > 50:  # 过滤太短的描述
                    return text

        # 方法2: 常见摘要容器
        for sel in _ABSTRACT_SELECTORS[2:]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if len(text) > 50:
                    return text

        # 方法3: 查找包含 "abstract" 的 h2/h3 后的 p
        for heading in soup.find_all(["h2", "h3", "h4"]):
            if "abstract" in heading.get_text(strip=True).lower():
                next_p = heading.find_next("p")
                if next_p:
                    text = next_p.get_text(strip=True)
                    if len(text) > 50:
                        return text

        return ""
    except Exception:
        return ""


def fetch_abstract_from_pdf(pdf_url, timeout=30):
    """从 PDF 提取摘要"""
    if not PdfReader:
        return ""
    try:
        content = _fetch_url_content(pdf_url, timeout)
        reader = PdfReader(BytesIO(content))

        # 提取前3页文本（摘要通常在首页）
        full_text = ""
        for i, page in enumerate(reader.pages[:3]):
            text = page.extract_text() or ""
            full_text += text
            if len(full_text) > 5000:
                break

        if not full_text:
            return ""

        # 查找摘要部分
        # 尝试英文摘要标记
        match = re.search(
            r'abstract[:\s]*(.{50,500}?)(?:\n\n|introduction|keywords|1\.)',
            full_text, re.DOTALL | re.IGNORECASE
        )
        if match:
            abstract = re.sub(r'\s+', ' ', match.group(1).strip())
            if len(abstract) > 50:
                return abstract

        # 尝试法文/其他语言摘要标记
        match = re.search(r'resum[:e][:\s]*(.{50,500}?)(?:\n\n|introduction)', full_text, re.DOTALL)
        if match:
            abstract = re.sub(r'\s+', ' ', match.group(1).strip())
            if len(abstract) > 50:
                return abstract

        # 如果找不到明确的摘要标记，返回前500字符作为备选
        if len(full_text) > 100:
            return full_text[:500].strip()
        return ""
    except Exception:
        return ""


def fetch_full_abstract(work, timeout=30):
    """
    获取论文的完整摘要。
    优先级: OpenAlex倒排索引 > HTML页面 > PDF
    """
    # 1. 先检查 OpenAlex 是否已有摘要
    inv_index = work.get("abstract_inverted_index")
    if inv_index:
        abstract = abstract_from_inverted_index(inv_index)
        if abstract:
            return abstract

    # 2. 获取 OA 位置信息
    oa_location = work.get("best_oa_location") or {}
    if not oa_location.get("is_oa"):
        return ""

    # 3. 优先从 HTML 页面提取
    landing_url = oa_location.get("landing_page_url")
    if landing_url:
        abstract = fetch_abstract_from_html(landing_url, timeout)
        if abstract:
            return abstract

    # 4. 后备从 PDF 提取
    pdf_url = oa_location.get("pdf_url")
    if pdf_url:
        abstract = fetch_abstract_from_pdf(pdf_url, timeout)
        if abstract:
            return abstract

    return ""


def normalize_title(t):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff\u0600-\u06ff]", "", (t or "").lower())


def work_to_record(w, matched, fetch_abstract=False, abstract_timeout=30):
    src = (w.get("primary_location") or {}).get("source") or {}
    authors = [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])]

    # 获取摘要
    if fetch_abstract:
        abstract = fetch_full_abstract(w, timeout=abstract_timeout)
    else:
        abstract = abstract_from_inverted_index(w.get("abstract_inverted_index"))

    return {
        "id": w.get("id"),
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "title": w.get("title"),
        "abstract": abstract,
        "language": w.get("language"),
        "year": w.get("publication_year"),
        "authors": "; ".join([a for a in authors if a])[:800],
        "venue": (src or {}).get("display_name"),
        "type": w.get("type"),
        "cited_by_count": w.get("cited_by_count", 0),
        "openalex_url": w.get("id"),
        "matched_keywords": matched,
        "query_language": None,  # 由调用方填
    }


# ---------------- 抓取实现 ----------------
def fetch_via_phrase_search(lang, keywords, years, cap_per_keyword, mailto=DEFAULT_MAILTO,
                           fetch_abstract=False, abstract_timeout=30, abstract_delay=1.0):
    """按语言逐短语精确检索 title+abstract，取并集。
    注意：实测 OpenAlex 对中文/阿拉伯文短语检索均有效（如 language:zh + "文化旅游"）。"""
    hits = {}  # id -> (record, [kw...])
    for kw in keywords:
        f = f'language:{lang},title_and_abstract.search:"{kw}"'
        if years:
            f += f",publication_year:{years}"
        url = (f"{API}?filter={urllib.parse.quote(f)}"
               f"&per-page={PER_PAGE}&mailto={mailto}&cursor=*")
        page = 0
        while url and page < 50:  # 每个短语最多翻 50 页(=PER_PAGE*50 条)
            data = http_get_json(url)
            for w in data.get("results", []):
                wid = w["id"]
                if wid not in hits:
                    hits[wid] = (work_to_record(w, [],
                               fetch_abstract=fetch_abstract,
                               abstract_timeout=abstract_timeout), [])
                hits[wid][1].append(kw)
            page += 1
            cursor = (data.get("meta") or {}).get("next_cursor")
            url = None if not cursor else url.replace("cursor=*", f"cursor={cursor}")
            if len(hits) >= cap_per_keyword * 3:  # 保守上限，防失控
                break
            time.sleep(0.1)
    return [rec for rec, kws in hits.values() if (rec.update(matched_keywords=sorted(set(kws))) or True)]


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="三语文旅论文抓取（OpenAlex）")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--langs", default="en,zh,ar", help="逗号分隔：en,zh,ar")
    ap.add_argument("--per-lang", type=int, default=60, help="每语言目标均衡数量")
    ap.add_argument("--years", default="2010-2026", help="年份范围，如 2013-2026；空串表示不限")
    ap.add_argument("--relation", default="strict",
                    choices=["strict", "relaxed", "off"],
                    help="中阿关系口径: strict=标题/摘要同时含中国AND阿拉伯标记; "
                         "relaxed=含任一方即可; off=仅按话题词不过滤")
    ap.add_argument("--mailto", default=None, help="OpenAlex 礼貌池邮箱(默认取文件顶部常量)")
    ap.add_argument("--seed", type=int, default=42)
    # ── 全文摘要抓取 ──
    ap.add_argument("--fetch-abstract", action="store_true",
                    help="启用全文摘要抓取（从HTML/PDF提取，需安装 beautifulsoup4 requests PyPDF2）")
    ap.add_argument("--abstract-timeout", type=int, default=30,
                    help="摘要抓取超时时间（秒），默认30")
    ap.add_argument("--abstract-delay", type=float, default=1.0,
                    help="摘要抓取请求间隔（秒），默认1.0")
    args = ap.parse_args()
    mailto = args.mailto or DEFAULT_MAILTO

    random.seed(args.seed)
    os.makedirs(os.path.join(args.outdir, "raw"), exist_ok=True)
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    report = {"years": args.years, "relation": args.relation,
              "requested_per_lang": args.per_lang, "langs": {},
              "fetch_abstract": args.fetch_abstract,
              "abstract_timeout": args.abstract_timeout}

    for lang in langs:
        kws = KEYWORDS[lang]
        print(f"\n===== 抓取 {LANG_LABEL[lang]}（{lang}），短语数={len(kws)} =====")
        if args.fetch_abstract:
            print(f"  [摘要] 全文摘要抓取已启用 (timeout={args.abstract_timeout}s, delay={args.abstract_delay}s)")
        recs = fetch_via_phrase_search(lang, kws, args.years, args.per_lang, mailto=mailto,
                                      fetch_abstract=args.fetch_abstract,
                                      abstract_timeout=args.abstract_timeout,
                                      abstract_delay=args.abstract_delay)
        # 去重（同标题/同 DOI 视为重复）
        seen_t, seen_d, uniq = set(), set(), []
        for r in sorted(recs, key=lambda x: -(x.get("cited_by_count") or 0)):
            nt, doi = normalize_title(r["title"]), (r.get("doi") or "").lower()
            if (nt and nt in seen_t) or (doi and doi in seen_d):
                continue
            seen_t.add(nt); seen_d.add(doi)
            uniq.append(r)
        for r in uniq:
            r["query_language"] = lang
        # 中阿关系过滤
        if args.relation == "off":
            kept, dropped, sides = uniq, 0, None
        else:
            kept, dropped, sides = relation_filter(uniq, lang, args.relation)
            print(f"  [中阿关系·{args.relation}] 双侧both={sides['both']} | "
                  f"仅中国={sides['china_only']} | 仅阿拉伯={sides['arab_only']} | "
                  f"均不涉及={sides['neither']} -> 保留 {len(kept)} 篇")
        # 显示摘要统计
        if args.fetch_abstract and kept:
            with_abs = sum(1 for r in kept if r.get("abstract"))
            print(f"  [摘要] {with_abs}/{len(kept)} 篇有摘要 ({with_abs/len(kept)*100:.1f}%)")
        path = os.path.join(args.outdir, "raw", f"{lang}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in kept:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        report["langs"][lang] = {
            "label": LANG_LABEL[lang], "dedup_total": len(uniq),
            "relation_kept": len(kept), "relation_dropped": dropped,
            "relation_sides": sides,
            "with_abstract": sum(1 for r in kept if r.get("abstract")) if args.fetch_abstract else 0,
        }
        print(f"  去重 {len(uniq)} 篇 -> 关系过滤后保留 {len(kept)} 篇 -> {path}")

    # ---- 均衡抽样：每语言取 min(可得数, per_lang)，保证比例接近 1:1:1 ----
    pools = {}
    for lang in langs:
        with open(os.path.join(args.outdir, "raw", f"{lang}.jsonl"), encoding="utf-8") as f:
            pools[lang] = [json.loads(l) for l in f if l.strip()]
    avail = {l: len(pools[l]) for l in pools}
    balanced_n = min([avail[l] for l in pools] + [args.per_lang])
    sampled = {}
    for l, pool in pools.items():
        sampled[l] = random.sample(pool, min(balanced_n, len(pool)))
    report["balanced_n_per_lang"] = balanced_n
    report["availability"] = avail

    all_recs = []
    for l in langs:
        for r in sampled[l]:
            all_recs.append(r)
    with open(os.path.join(args.outdir, "combined.jsonl"), "w", encoding="utf-8") as f:
        for r in all_recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 同时导出一份 CSV 方便在 Excel/WPS 里人工浏览
    fields = ["query_language", "title", "year", "venue", "authors", "doi",
              "openalex_url", "matched_keywords", "relation_sides",
              "cited_by_count", "type"]
    with open(os.path.join(args.outdir, "combined.csv"), "w", encoding="utf-8-sig", newline="") as f:
        wcsv = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        wcsv.writeheader()
        for r in all_recs:
            wcsv.writerow(r)

    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n===== 完成 =====")
    print(f"可用数量: {avail}")
    print(f"均衡抽样后每语言 {balanced_n} 篇（约 1:1:1）")
    print(f"输出: {args.outdir}/combined.jsonl .csv + report.json")


if __name__ == "__main__":
    main()
