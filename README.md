# 中阿文旅「纯英文」语料库

面向《中阿文旅世界模型》论文的中阿双边文旅**纯英文**语料：抓取 → 三维度清洗 → 交付。

> 2026-09-20 更新：项目口径收敛为**纯英文**（不做三语）。旧的三语/阿拉伯语 demo 数据已移除。

## 一、流水线

```
Crossref API
  └─ fetch_en_crossref.py        抓取（中国 × 阿拉伯 × 文旅话题）→ data_en_crossref/
       └─ clean_dimensions_en.py 三维度清洗（CN ∩ AR ∩ 文旅）   → data_en_clean/
```

## 二、产物

| 路径 | 内容 |
|---|---|
| `data_en_crossref/combined.jsonl` | 英文语料 **3,500** 篇（strict 双边 198 / relaxed 3,302） |
| `data_en_crossref/combined.csv` | 表格版（utf-8-sig） |
| `data_en_crossref/raw/en.jsonl` | 过滤后全量池 7,151 篇（未经抽样） |
| `data_en_crossref/report.json` · `语料报告.md` | 画像与统计 |
| `data_en_crossref/validation_report.json` · `manual_review_sample.csv` | 校验报告 + 人工抽检表 |
| `data_en_clean/cleaned_en.jsonl` | **三维度达标 6,916 条**（CN∩AR∩文旅，含可复核维度字段） |
| `data_en_clean/cleaned_en.csv` · `report.json` · `英文三维度清洗报告.md` | 达标表格 + 清洗报告 |

## 三、三维度清洗口径

达标 = **中国侧(CN) AND 阿拉伯侧(AR) AND 文旅**。

- **CN**：china / chinese / sino / prc / beijing / shanghai / hong kong / macau …
- **AR**：arab / saudi / emirates / qatar / kuwait / bahrain / oman / jordan / egypt /
  iraq / syria / yemen / sudan …（`gulf` 仅收 persian/arabian/gulf cooperation 等具体短语）
- **文旅（宽口径）**：强词（tourism/travel/heritage/museum…）+ 弱词
  （belt and road / bri / silk road / economic cooperation / economic corridor /
  connectivity / people to people / soft power …）

清洗结果（英文池 13,153 → 去重 10,735）：

| 指标 | 条数 |
|---|---|
| CN 命中 | 9,233 |
| AR 命中 | 8,459 |
| 文旅命中 | 9,712 |
| **三维度达标** | **6,916**（强词 6,611 / 仅弱词 305） |

> 局限：引用串匹配对「全球性文献顺带提到 China/Arab」仍有误报，建议人工抽检。

## 四、脚本

| 文件 | 作用 |
|---|---|
| `fetch_en_crossref.py` | Crossref 纯英文抓取（免费、无额度限制） |
| `clean_dimensions_en.py` | 英文三维度清洗（可复用于其他英文数据：`--inputs a.jsonl,b.jsonl --outdir out`） |
| `report_corpus.py` | 生成 `语料报告.md` |
| `validate.py` | 语言/相关度/查重/DOI 四类校验 + 人工抽检表 |
| `fetch_papers.py` · `merge.py` | 通用 OpenAlex 抓取 / 跨目录合并（备用） |

## 五、重跑

```bash
source venv/bin/activate
python fetch_en_crossref.py --outdir data_en_crossref --target 3500 --rows 500
python clean_dimensions_en.py --inputs data_en_crossref/raw/en.jsonl --outdir data_en_clean
python report_corpus.py --data data_en_crossref/combined.jsonl --out data_en_crossref/语料报告.md
python validate.py --source-file data_en_crossref/combined.jsonl --crossref-sample 40
```

## 六、版权与规范

- 官方 API + 真实 `mailto`（礼貌池）；只取元数据+摘要；全文走图书馆/Unpaywall。
