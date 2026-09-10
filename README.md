# 中阿文旅论文三语语料抓取工具（中/英/阿 ≈ 1:1:1，限"中阿之间"双边文献）

用于《中阿文旅世界模型》论文语料构建：从 **OpenAlex 官方免费 API**
（https://api.openalex.org ，无需 Key、合法）抓取中、英、阿三语"中阿双边文旅"
论文元数据（含摘要），自动去重、关系过滤、均衡抽样、准确性校验。

## 核心概念：三种"中阿关系"口径（--relation）
| 模式 | 判定（在标题/摘要上做确定性文本匹配） | 适用 |
|---|---|---|
| `strict`（默认） | 同篇**同时**含中国侧标记 **AND** 阿拉伯侧标记（如"中阿"、"Sino-Arab"、中国+沙特…） | 你说的"中国阿拉伯之间、and 的关系" |
| `relaxed` | 至少含一侧（中国 **OR** 阿拉伯） | 语料不够时的扩展集 |
| `off` | 只按话题词，不过滤 | 对照/预抓取 |

> 标记表（MARKERS）可按需增删国家/地名。中文"中阿"本身含中+阿故双计；
> 英文对词做 \b 词边界匹配，阿拉伯文先做归一化（أإآ→ا、ىی→ي、去变音）再子串匹配。

## 文件
| 文件 | 作用 |
|---|---|
| `fetch_papers.py` | 抓取 + 去重 + 关系过滤 + 均衡抽样 |
| `validate.py` | 四层准确性校验 + 人工抽检表 |
| `merge.py` | 合并三个组员的 outdir（跨语言按 DOI/标题查重） |
| `data_strict/` | strict 模式实测（en 37 / zh 89 / ar 58，均衡抽样 20×3） |
| `data_ar_demo/` | 单语言实测（只跑 ar） |

## 三个人怎么分工（无需改代码，只改参数）
```bash
# 你（阿语）——单独一个输出目录
python fetch_papers.py --langs ar --outdir data_ar --per-lang 400 --years 2013-2026 \
       --relation strict --mailto you@univ.edu

# 组员A（英语）
python fetch_papers.py --langs en --outdir data_en --per-lang 400 --years 2013-2026 \
       --relation strict --mailto teamA@univ.edu

# 组员C（中文）
python fetch_papers.py --langs zh --outdir data_zh --per-lang 400 --years 2013-2026 \
       --relation strict --mailto teamC@univ.edu

# 汇总：合并三个目录（自动跨语言查重，DOI 优先）
python merge.py --dirs data_ar,data_en,data_zh --out merged
```
语言由 `--langs` 决定（可只写一种），不必删 KEYWORDS；想少跑点可删 `KEYWORDS` 里
别的语言的词，但没必要。

## 能爬多少？（实测规模预期）
- API 本身不限量（礼貌池约 10 次/秒、10 万次/天），但按语料均衡策略，**每个短语
  最多翻 50 页(=5000 条)**，且全语言命中池超过 `--per-lang×3` 条后即停。
- 真正的瓶颈是 **strict 双边文献总量**。同一话题池下 strict 保留率实测：
  ar ≈ 25%、zh ≈ 26%、en ≈ 10%（词表已含中阿/丝路/一带一路等双边短语）。
- 经验值：`--per-lang 400` 时每语言 raw 池约 1000+ 条，strict 后约 100~300 条/语言；
  最终每语言取 min(三语池, per-lang) 篇，保证三语篇数完全相等。
- 若 strict 池仍不够：a) 加双边短语进 KEYWORDS；b) 中文侧用知网导出补齐（见下）；
  c) 论文里分"核心集(strict) + 扩展集(relaxed)"两层，两层分开报告数量。

## 常用命令
```bash
python fetch_papers.py --per-lang 200 --relation strict --outdir data   # 标准三语
python fetch_papers.py --langs ar --relation relaxed --outdir data_ar_x # 阿语扩展集
python validate.py --data data --crossref-sample 30                     # 校验+人工抽检表
python merge.py --dirs data_ar,data_en,data_zh --out merged             # 组员合并
```
> 环境提示：本机 Python OpenSSL 握手被网络设备掐断，脚本自动探测并回退 curl，无需配置。

## 检索词（KEYWORDS 可自行增删）
- **en** 15 条：cultural tourism / China Arab tourism / Sino-Arab cooperation /
  China-Arab cultural exchange / silk road tourism / Belt and Road cultural cooperation / halal tourism …
- **zh** 15 条：文化旅游 / 文旅融合 / 中阿旅游 / 中阿文旅 / 中阿合作 / 中阿博览会 /
  阿拉伯游客 / 宁夏旅游 / 一带一路旅游 …
- **ar** 12 条：السياحة الثقافية / التعاون السياحي / مبادرة الحزام والطريق /
  العلاقات الصينية العربية / الصين والعالم العربي / السياح الصينيون …
实现：三语统一走 `title_and_abstract.search:"短语"` 精确短语检索（实测中文/阿语均有效），
同语言多短语取并集，命中短语记入 `matched_keywords` 字段。

## 准确性如何判断（validate.py 已实现）
1. 语言交叉验证：文字系统独立检测 vs OpenAlex 标注一致率（实测 en≈1.0、zh≈0.996、ar≈0.96）。
2. 主题相关度：逐篇复算命中短语数，**0 命中清单 = 人工复核候选**（多为检索边界效应，
   如"China–Arab relations"类政治外交文献——是否保留取决于你的"文旅"外延定义）。
3. 查重：标题归一化 + DOI 双通道（含跨语言重复）。
4. DOI 真实性抽查：抽样去 Crossref 验证存在性。
5. 人工抽检：`manual_review_sample.csv` 每语言随机抽 10~15 篇逐条阅读，算人工可接受率。

## 数据源途径（三语对比）
| 语言 | 推荐途径 | 说明 |
|---|---|---|
| 英语 | OpenAlex / Crossref / Semantic Scholar API | 官方 API 免费 |
| 阿拉伯语 | OpenAlex / Crossref / DOAJ | 阿语短语检索有效，海湾/埃及期刊多在 Crossref |
| 中文 | OpenAlex + **知网/万方手工导出** | OpenAlex 中文覆盖小于知网；核心双边文献可用知网"导出(EndNote)"补并库 |

## 版权与规范
- 官方 API + 真实 `mailto`（礼貌池）；只取元数据+摘要；全文走图书馆/Unpaywall。
- 知网禁止爬虫，只用手工导出；代码已做 429 退避重试。
# china_arab_tourism
