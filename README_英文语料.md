# China_arab_tourism —— 中阿文旅「纯英文」语料

面向《中阿文旅世界模型》论文语料构建，按世界模型线上项目／世界模型研发资料库
所需的文献类型（期刊论文、图书、图书章节、专著、会议论文、预印本、报告等）抓取
**纯英文**文献。

## 一、产物（本次交付）

| 路径 | 内容 |
|---|---|
| `data_en_crossref/combined.jsonl` | **最终语料 3500 条**（每条含摘要、文献类型、分层、相关度等级） |
| `data_en_crossref/combined.csv` | 同上的表格版（utf-8-sig，Excel/WPS 可直接打开） |
| `data_en_crossref/raw/en.jsonl` | 过滤后全量池 6359 条（未经抽样，供重新配比） |
| `data_en_crossref/report.json` | 机器可读统计报告 |
| `data_en_crossref/语料报告.md` | 语料画像：分层 / 相关度等级 / 文献类型 / 年份 / 来源 |
| `data_en_crossref/validation_report.json` | 校验报告（语言、查重、DOI 真实性、纳入规则自检） |
| `data_en_crossref/manual_review_sample.csv` | 人工抽检表（20 条，逐条阅读用） |
| `crossref_cache/` | 214 条检索式响应缓存（重跑不重复请求） |

## 二、数字口径（写论文请按此报告）

- 总量 **3500** 条（2010–2026，纯英文，DOI 覆盖 100%，摘要覆盖 42%）
- 中阿关系分层：`strict` 双边 **172**；`relaxed` 中国侧 **1996**、阿拉伯侧 **1332**
- 相关度等级：`A_双边_文旅核心` 89 / `B_双边_弱文旅` 83 /
  `C_单边_文旅核心` 3158 / `D_单边_弱文旅` 170
- 文献类型：期刊论文 2904、图书章节 339、会议论文 103、预印本 51、
  专著 37、图书 36、编著 20、参考条目 6、报告 2、参考书 2

> 取子集建议：严格口径取 **A+C**（命中核心文旅词）＝ 3247 条；
> 研究"中阿双边"专题时只用 `tier=strict`。

## 三、脚本

| 文件 | 作用 |
|---|---|
| `fetch_en_crossref.py` | **本次实际使用**：Crossref 通道纯英文抓取（免费、无额度限制） |
| `fetch_papers(1).py` | 原 OpenAlex 通道脚本（多语种）。已小改：`!` 前缀＝多词隐式 AND、`--keywords-json`、`--pages-per-keyword` |
| `validate(1).py` | 校验。已小改：`--source-file` 直接校验指定 jsonl；相关度复算兼容 `!` 词；新增标记规则自检 |
| `report_corpus.py` | 生成 `语料报告.md` |
| `kw_en_strict.json` / `kw_en_extended.json` | 英文检索词表（strict 双边 / 扩展） |

重跑（响应已缓存，约 1 分钟）：

```bash
cd China_arab_tourism
source venv/bin/activate
python fetch_en_crossref.py --outdir data_en_crossref --target 3500 --rows 500
python report_corpus.py --data data_en_crossref/combined.jsonl --out data_en_crossref/语料报告.md
python "validate(1).py" --source-file data_en_crossref/combined.jsonl --crossref-sample 40
```

## 四、已知限制（必须在论文中交代）

1. **OpenAlex 通道当日不可用。** OpenAlex 已改为信用额度制，本机 IP 当日额度耗尽，
   响应头 `x-ratelimit-remaining: 0`、`retry-after ≈ 60500s`（约 17 小时）。
   `fetch_papers.py` 现在会持续 429，需等额度恢复后重跑；届时可用它补摘要与双边集。
2. **严格双边纯英文文献总量有限。** 标题/摘要同时含中国侧 AND 阿拉伯侧标记
   且属文旅主题的纯英文文献，在开放数据库中总量仅数百篇。本语料 Tier A 只有
   172 条（其中 89 条命中核心文旅词）已接近该口径可得上限，这是领域事实，
   不要写成"检索不充分"。
3. **扩展集是单边的。** relaxed 层只要求命中中国侧**或**阿拉伯侧，因此包含
   诸如"约旦可持续旅游发展"这类与中国无直接关联的阿拉伯旅游研究。用
   `relevance_grade` 可按需收紧。
4. **摘要覆盖率 42%。** Crossref 并非所有出版社都存摘要；11 条原始摘要中英阿混排，
   已只保留英文段并置 `abstract_trimmed_mixed=true`。
5. **DOI 抽查 40/40 通过属弱证据**——数据源本身就是 Crossref，此项只能证明记录真实存在，
   不能证明主题相关性；相关性由 `manual_review_sample.csv` 人工抽检确认。
6. **仅采集元数据（题录＋摘要）**，不存储全文；全文须走图书馆授权渠道或 Unpaywall。

## 五、字段说明（combined.jsonl）

| 字段 | 含义 |
|---|---|
| `title` / `abstract` / `year` / `authors` / `venue` / `doi` / `url` | 题录信息 |
| `type` / `type_label` | **文献类型**（Crossref 原始值 / 中文标签） |
| `tier` | `strict`（双边） / `relaxed`（单边） |
| `relation_sides` | `both` / `china` / `arab` |
| `relevance_grade` | A/B/C/D 相关度等级（见报告） |
| `relevance` | 命中的中国侧/阿拉伯侧/文旅话题词清单（可复核） |
| `source_db` | `crossref` |
| `crawl_query` | 命中该记录的检索式 |
| `cited_by_count` | 被引次数 |
