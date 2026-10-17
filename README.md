# TVBox 接口净化工具

把带违规内容的 TVBox 接口清洗成干净版本，可重复用于任何接口。

## 线上地址（按优先级）

### ① jsDelivr CDN（长期稳定，可自动更新，推荐）

```
https://cdn.jsdelivr.net/gh/shenzengquan/tvbox-clean/tvbox.json       ← 推荐
https://cdn.jsdelivr.net/gh/shenzengquan/tvbox-clean/tvbox.min.json
https://fastly.jsdelivr.net/gh/shenzengquan/tvbox-clean/tvbox.json    ← 备用节点
```

实测：397KB / **0.84~1.29 秒**，多次重复一致。

仓库：<https://github.com/shenzengquan/tvbox-clean>

> 自有托管 `https://tvbox-clean.app.workbuddy.host/tvbox.json` 目前
> **返回的是旧版数据**（上次发布被审批超时拦下），未列入推荐。
> 若要启用，重新发布 `site/` 后再自行加回。

### ② GitHub raw（不推荐）

```
https://raw.githubusercontent.com/shenzengquan/tvbox-clean/main/tvbox.json
```

⚠️ 本机实测 **`http=000`，19 秒超时，完全不可用**。仅作备用记录。
（Actions 运行环境里反而是通的，0.2 秒拉完上游——网络环境不同。）

### 速度实测对比

| 路径 | 结果 | 评价 |
|---|---|---|
| 自有托管 | 0.5~0.9s | 最快，但 `*.workbuddy.host` 是开发预览域名 |
| **jsDelivr（无版本号）** | **0.84~1.29s** | **推荐长期使用** |
| ghproxy 镜像 | 167 KB/s | 上游拉取用，不适合当发布地址 |
| GitHub raw 直连 | 3 次中 2 次失败，1 次 1.3KB/s | 不可用 |
| Statically | 21s 超时 | 不可用 |
| 源站 `tv.cc0cd.cc.cd` | 5~45 KB/s | 极慢，仅作兜底 |

### ⚠️ 最重要的一条：地址里千万别写 `@main`

这是本项目踩得最深的一个坑，**直接影响数据新鲜度**。

jsDelivr 对**显式分支引用**（`@main`）做长缓存，最长 12 小时。实测：

| 写法 | 返回 |
|---|---|
| `.../tvbox-clean@main/tvbox.json` | 407695 字节（**旧版**） |
| `.../tvbox-clean/tvbox.json` | 396847 字节（新版 ✅） |
| `fastly.jsdelivr.net/...`（无版本号） | 396847 字节（新版 ✅） |
| 按 commit sha 访问 | 396847 字节（新版，但地址会变） |

当时仓库里已经是新版，`@main` 连续 6 次、十分钟内**始终返回旧版**。
而且 purge 接口（`purge.jsdelivr.net`）返回 200 但**实际刷不掉**。
`?v=xxx` 这类 query 参数也无效 —— 分支引用下 query 不计入缓存键。

**结论：用省略版本号的写法。** 它会解析到默认分支，缓存短，实测每次都是最新。

想拿绝对最新（绕过一切缓存），用提交号代替分支名，但地址每次提交都会变，
不适合填进客户端，只适合临时排查：

```
https://cdn.jsdelivr.net/gh/shenzengquan/tvbox-clean@{commit_sha}/tvbox.json
```

## 换行符坑（TVBoxOSC 拉取失败的根因）

Python 在 Windows 上以**文本模式** `open(..., "w")` 写入时，会自动把 `\n` 转成 `\r\n`，
生成的 JSON 文件头变成 `{\r\n`。**TVBoxOSC 某些版本解析 CRLF 的 JSON 会直接失败**，
表现就是"拉取配置失败"，而服务端返回 200 一切正常。

已改为字节模式写入：

```python
body = json.dumps(...).replace("\r\n", "\n")
open(out, "wb").write(body.encode("utf-8"))   # 不加 BOM
```

改完后多行版 425KB → 408KB（去掉了多余的 `\r`），单行版 328KB。

## 上游源

源项目 [25175/tvyuan](https://github.com/25175/tvyuan)，每小时整点自动更新。

| 版本 | 原始地址 |
|---|---|
| 全量版（当前使用） | `raw.githubusercontent.com/25175/tvyuan/master/tvbox_full.json` |
| 简洁版 | `.../master/tvbox.json` |
| 多仓版 | `.../master/tvbox_multi.json` |

⚠️ 本机直连 `raw.githubusercontent.com` **时通时不通**（实测被 reset 过，也成功过）。
可用镜像（实测 190~250 KB/s，比原站 `tv.cc0cd.cc.cd` 的 5~45 KB/s 快 4~40 倍）：

```
https://gh-proxy.com/<原始地址>
https://ghproxy.net/<原始地址>
https://ghfast.top/<原始地址>
```

## 文件

| 文件 | 说明 |
|---|---|
| `tvbox_pipeline.py` | **自动化流水线**：拉取→分类探测→清洗→校验→发布 |
| `probe_sites.py` | **分类探测器**：问采集源要真实分类，识别名字干净的伪装色情站 |
| `cat_policy.py` | 净化策略：两种粒度判定（整接口删 / 只删分类）+ 色情分类词表 |
| `tvbox_clean.py` | 单次清洗工具（流水线内部复用它的规则） |
| `tvbox_publish.py` | GitHub 推送（走 Git Data API）+ jsDelivr 缓存刷新 |
| `blacklist.txt` | 词库，随时可改，不用动脚本 |
| `.github_token` | GitHub token，已加进 `.gitignore` |
| `site/` | 发布目录（`index.html` 状态页 + `tvbox.json`） |
| `probe_result.json` | 探测明细（每次运行生成，已 gitignore） |
| `tvbox_raw.json` | 原始接口快照（536KB） |
| `tvbox_clean.json` | 清洗结果 |
| `report.txt` | 完整删除清单，人工复核用 |
| `pipeline.log` | 每次运行的日志 |

## 只靠词库永远不够：内容级探测（2026-10-17 新增）

用户报障「还有十八禁内容，如凤凰影视」，查下来是这样的条目：

```json
{"key": "fh资源", "name": "🧿凤凰┃影视",
 "api": "http://fhapi9.com/api.php/provide/vod/"}
```

名字里「凤凰」「影视」都是正常词，**任何词库都不可能命中**。但它
`ac=list` 一返回，分类就是：

```
伦理三级 | 制服丝袜 | 少女萝莉 | 巨乳美乳 | 群交淫乱
```

实测同类伪装站有 50+ 个：`🍊森林`、`🍊蝙蝠`、`🧿正品┃影视`、`🍊奶香`、
`🍊极品`、`🍊豆豆`、`🍊湿园`、`🧿乐播┃资源`、`🧿gay┃资源`……
名字全都干干净净，只能问接口本身。

**所以流水线加了探测步骤**（`probe_sites.py` + `cat_policy.py`）：

1. 对 `type=0/1/4` 且 api 是 http 的采集源，并发请求 `?ac=list`
2. 优先读 `class[]`（全站分类），否则退回样片的 `type_name`
3. 命中的色情分类再单独请求 `?ac=detail&t=<id>`，看分类里**到底有没有片**

第 3 步是必须的：正经站（索尼 / 猫眼 / 百度 / iKun）的分类表里也挂着
「港台三级」「里番动漫」这些名字，但**分类是空的**或内容错标。
实测猫眼源的「里番动漫」分类里返回的是《罪火焦点》《灵魂伴侣》这类科幻片。
只看名字就删，会误杀 40 多个正常站。

### 两种粒度的净化规则

| 情况 | 处理 |
|---|---|
| 整个接口是色情站（分类表过半是色情分类，或全是色情分类且有片） | **整个接口删除** |
| 只有部分分类违禁 | **只删该分类，站点保留** |

第二种情况，若配置里没有 `categories` 字段（分类由 api 的 `class[] 提供），
就把「实测分类 − 违规分类」写成 `categories` 白名单。
注意白名单必须过滤掉**所有**色情分类名，不能只删探测点名的那几个
—— 否则探测漏判的分类会顺着白名单漏过去（实测漏过 39 个）。

### 另一道防线：抓取地址

`type=3`（jar/py 蜘蛛）站点没有 HTTP 探测协议，靠 `ext` 里的真实 URL 判断：

```
🧿春堂┃资源  → ext.主页url = https://jav.sb/
🧿快活┃影视  → https://www.777mmx.com/yazhouxingai/index.html
🧿流云┃影视  → https://www.1av.shop/
🧿搜视┃影视  → https://777170.xyz/%e6%97%a5%e9%9f%a9%e8%a7%86%e9%a2%91/
```

⚠️ 只查 URL，**不能查 ext 全部文本**：6080电影、纽约影院、囧次元动漫
这些正经站的「剧情」筛选项是 `情感&科幻&冒险&萝莉&校园`，拿它判违规会
误杀一片。另外 URL 要 `unquote` 一次，`%e6%97%a5%e9%9f%a9` 解码后是「日韩视频」。

### 同域传播

同一批色情源会换名字挂多个入口（`凤凰┃影视` / `番号资源` / `番外┃采集`
其实是同一个 `fhapi9.com`）。判定色情后把 **host + path** 指纹记下来，
其他镜像直接判，不必重新探测。
只按 host+path，**不按裸域名** —— 同一台机器上正经源和色情源并存是常态。

### 误判与漏判的实测边界

| 站点 | 分类名里有色情词 | 实际情况 | 处理 |
|---|---|---|---|
| 索尼 / 猫眼 / 百度 / iKun | 里番动漫、港台三级 | **空分类或内容错标** | 保留站点，删分类名 |
| 6080电影 / 纽约影院 | ext 里有「萝莉」 | 剧情筛选项标签串 | **不动** |
| 凤凰 / 森林 / 蝙蝠 | 无 | 分类全是色情分类且有片 | **整站删** |

## 三个字段都要清洗（重要教训）

TVBox 配置有 `sites` / `lives` / `parses` 三个内容字段，**少清一个就等于白清**。

早期版本只清洗了 `sites` 和 `lives`，结果 `parses` 里躺着一个
`{"name": "萝莉", "url": "http://chaloli.cn/home/..."}` 被原样带出，
直到拿线上文件做全字段扫描才发现。

现在 `parses` 也走清洗流程，且规则和站点不同：

- `name`（解析器显示名）→ 按站点名规则匹配，命中即删
- `url`（网关域名）→ **无边界子串匹配**。`chaloli.cn` 里的 `loli`
  前后都是字母和点，如果加 `(?<![a-z0-9])` 词边界就完全漏检
- `ext`（flag 列表：优酷/爱奇艺…）→ 不参与匹配，纯白名单内容

## 误杀防线（血泪总结）

这三个坑都踩过，都已在代码里修掉：

**① 单字词误杀**

`花`/`兔`/`舞`/`奶`/`丝`/`妹`/`夜`/`桃`/`杏`/`秀` 这类单字绝不能进词库，
会误杀兔小贝、樱花动漫、跳舞教学、芒果TV、花卷4K。只用完整词。

**② ASCII 短词在 base64 里误命中**

`FC2`/`Sex` 会命中 ext 里一大段 base64 的偶然子串，误杀追剧、小黄人、星奇。
站点匹配时加词边界 `(?<![a-z0-9])xxx(?![a-z0-9])` 解决。
（但 `parses` 的 url 不能加边界，见上一节）

**③ 分类筛选项不是违规信号**

正常影视站的 `ext` 里有「情色」「伦理」，正常动漫站有
`情感&科幻&冒险&萝莉&校园&机战` —— 这些都是标准分类标签。
所以这类词只放 `[nameonly]` 段，只匹配站点名。

对应地，流水线的残留检查也是分级的：硬违规词全文扫描，
「萝莉」这类只查 `sites/lives/parses` 的 `name` 字段 +
`parses` 的 url，绝不全文扫描，否则每天都会误报。

## 自动更新

**用 GitHub Actions，不需要开电脑。**

配置文件：`.github/workflows/update.yml`，每天北京时间 **08:30**（UTC 00:30）运行。

```
拉取上游（多镜像竞速）
  → 清洗 sites / lives / parses
  → 校验（残留词、站点数骤降、误杀抽检）
  → 写 tvbox.json / tvbox.min.json 到仓库根
  → git commit + push
  → 刷新 jsDelivr
  → 在运行页生成摘要
```

查看运行记录 / 手动触发：
<https://github.com/shenzengquan/tvbox-clean/actions/workflows/update.yml>

> 仓库若 60 天无任何提交，GitHub 会自动暂停定时任务。
> 但本任务每天都在提交（至少 `tvbox.json` 变化时），实测不会触发暂停。
> 真被暂停了，进上面页面点一次 `Enable workflow` 即可。

### 云端怎么提交的

Actions 环境里用 `git commit + push`（runner 本身在 GitHub 内网，畅通），
不需要 PAT。本地运行时才走 Git Data API（因为本机 `git push` 不通）。

脚本会检测 `GITHUB_ACTIONS=true` 自动切换，无需改配置。

### 保护机制

发布前会校验，任一项不过就**中止且不发布**，线上保留上一次的好数据：

- **残留检查**：24 个高置信敏感词，出现即中止（分级：硬词全文扫描，
  「萝莉」类只查名称字段，避免误判动漫分类）
- **站点数骤降保护**：低于上次的 80% 就中止（防止上游给残缺数据）
- **误杀抽检**：`哔哩`≥5、`豆瓣`≥3、`芒果`≥1，否则判为过度清洗
- **产物校验**：0 换行符必须是 `\n`（CRLF 会让 TVBoxOSC 拉取失败）、四个必备字段齐全
- **幂等**：`tvbox.json` 内容没变就不提交，不会刷满 commit 历史

校验失败时退出码 2，workflow 会变红，线上保持旧版不受影响。

### 本地手动跑（可选，不是必需的）

```bash
# 完整流程：拉取 + 清洗 + 写 site/ + 推 GitHub
python tvbox_pipeline.py

# 只清洗不发布
python tvbox_pipeline.py --dry-run

# 只写本地，不碰 GitHub
python tvbox_pipeline.py --no-github

# 用本地快照（不联网，秒出）
python tvbox_pipeline.py --local tvbox_raw.json
```

本地跑和 Actions 跑的结果完全一致，产物路径会自动区分：
Actions 写仓库根（jsDelivr 直接可访问），本地写 `site/` 子目录（给自有托管用）。

## 最快上手

```bash
# 1. 先看报告，不写文件（默认就是报告模式）
python tvbox_clean.py https://tv.cc0cd.cc.cd

# 2. 确认没问题再生成
python tvbox_clean.py https://tv.cc0cd.cc.cd --apply --out tvbox_clean.json
```

> ⏱ 源站很慢：536KB 大约要 **4 分钟**，中途还会断。
> 脚本已内置**断点续传 + 自动重试 3 次**（`Range` 请求累积下载），不用手动重试。
> 嫌慢就用本地快照：`python tvbox_clean.py tvbox_raw.json ...`（秒出）



## 参数

```
--apply            真正写文件（不加则只报告）
--out FILE         输出文件名，默认 tvbox_clean.json
--blacklist FILE   换一份词库
--drop-sub URL     整段丢弃某个来源子接口，可写多次
--keep-sub URL     白名单，优先于 drop-sub
--clean-names      剥掉 [延迟|来源] 前缀，显示名更干净
--stats            只看结构统计
```

## 这个接口的结构（已摸清）

```
tv.cc0cd.cc.cd  (1369 站点)
├── 主配置内联           1214 个
├── clun.top/fun.json      71 个  ← 全是违规站点，整段丢弃
├── 9280.kstore.space/wex.json  50 个  ← 正常（影视/短剧/听书/少儿）
└── ls660.com/TV/feimao.json    34 个  ← 正常（影视/APP/网盘）
```

合并后的 name 前缀形如 `[189ms|https://clun.top/fun.json] 麻豆 | 采集`，
脚本就是靠这个前缀识别来源的。

## 三层过滤机制

1. **来源级**：`--drop-sub` 整段砍掉子接口，最快最彻底
2. **字段级**：扫描 `name / group / categories / genre / ext / key`
3. **词库级**：`blacklist.txt` 分四段

| 段 | 含义 |
|---|---|
| `[nameonly]` | 只匹配站点显示名。用于 `情色/伦理/写真` —— 普通站的筛选项里有这些是正常的 |
| `[strong]` | 任意字段命中即删 |
| `[weak]` | 任意字段命中列入待复核，也删 |
| `[source]` | 段的 URL 作为默认 `--drop-sub` |

## 踩过的坑（改词库前必读）

**1. 不要放单字。**
`花 兔 舞 奶 丝 妹 夜 桃 杏 秀` 会误杀 `兔小贝`、`樱花动漫`、`跳舞教学`、
`芒果TV`、`花卷4K`、`美剧看看`。第一版放了单字，181 个删错了 90 多个。

**2. ASCII 短词必须加词边界。**
`FC2`、`Sex` 在 base64 串里会偶然出现，曾误杀 `追剧`、`小黄人`、`星奇`。
脚本已用 `(?<![a-z0-9])xxx(?![a-z0-9])` 保护，纯 ASCII 词自动走这条路径。

**3. 光看站名会漏。**
`🧿快活┃影视` 名字正常，但 `ext.主页url` 是 `777mmx.com/yazhouxingai/`。
`8️⃣85K┃影视` 的分类里藏着 `里番动漫$46`。
**必须扫 ext**，这层才是真过滤器。

**4. 也要看直播源。**
`lives` 里有 32 条是伪装过的成人频道：`大洋马直播`、`大秀直播`、`花活`、
`绝品`、`珍藏`、`美腿丝袜`。光清 sites 会漏掉这一整块。

**5. spider 不用动。**
`starlink.jar` 里 181 个类全是通用采集器（XBPQ、XYQHiker、网盘、B站、豆瓣），
没有成人站专用类。`csp_GM` 实为 `merge/A0/gm` 合并工具库。
那些 `./py/18.py`、`./js/ss/肉视频.py` 是**站点自带 ext**，删掉站点条目就不再加载。

## 验证结果

```
原始: 站点 1369 / 直播 129 / 解析 153
净化: 站点 1264 / 直播  97 / 解析 153

违规残留检查: 无
误杀检查: 兔小贝5 樱花动漫3 跳舞2 芒果4 花卷2 电影天堂2 豆瓣12 哔哩28 → 全部保留
```

## 为什么能"快速访问"

| 访问方式 | 实测速度 |
|---|---|
| 源站 `tv.cc0cd.cc.cd` | 5~45 KB/s，常超时（Cloudflare 路由到 LAX，且带限速） |
| GitHub raw 直连 | 时通时断（被 reset） |
| GitHub 镜像 | 190~250 KB/s |
| **发布后的地址** | **0.5 秒返回完整 425KB** |

发布后的地址是 WorkBuddy 应用托管，国内直连，不受源站限速影响。

## 换托管位置

如果以后想换（比如自己 NAS），把 `site/` 目录整个搬过去即可，`tvbox.json` 是纯静态文件。

⚠️ 无论托管在哪，`spider` 字段必须保留原值（`starlink.jar` 的 gitee 地址），
否则依赖 jar 的站点会全部失效。

## 换接口复用

```bash
# 新接口直接跑，先看报告
python tvbox_clean.py 新接口地址

# 如果新接口也有 fun.json 这类子接口，加 --drop-sub
python tvbox_clean.py 新接口地址 --drop-sub "fun.json" --apply
```

删清单每次都写进 `report.txt`，建议每次生成后扫一眼。
