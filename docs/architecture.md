# 架构与数据流

本文档说明项目的主要模块、数据流和 API 边界。


## 总览

```text
Lolalytics
  -> pre_fetch.py
  -> data/botlane_dataset.json
  -> bp_engine.py
  -> app.py API
  -> templates/index.html
```

项目运行时不实时请求 Lolalytics。线上和本地服务读取已经生成好的 `data/botlane_dataset.json`，因此推荐接口可以快速返回。


## 数据层

### `pre_fetch.py`

负责从 Lolalytics 拉取 KR / Emerald+ / Ranked / 当前默认版本的下路相关数据，并生成离线快照：

- bottom / support 两个位置的梯队和基础英雄表现。
- 英雄之间的下路协同数据。
- 英雄面对敌方 ADC / 辅助的对位数据。
- 当前版本刚更新时样本较少，入库最低场次为 100；推荐结果仍会通过可信度字段提示样本成熟度。
- 核心数据读取公开统计接口里的 `ep=tier`、`ep=build-team`、`ep=counter` 数据。
- 时间趋势不在这些接口里，需要通过 `--with-timeline` 单独启用 Scrapling 抓取 build 页 Qwik JSON。

输出文件：

```text
data/botlane_dataset.json
```

数据结构主要包含：

- `meta`：更新时间和数据来源。
- `tiers`：bottom/support 梯队。
- `hero_stats`：英雄基础胜率、场次和可用的时间趋势。
- `synergy_by_lane` / `counter_by_lane`：按候选位置保存的协同和对位数据。
- `synergy` / `counter`：兼容旧接口的合并数据。

按位置保存矩阵是推荐准确性的关键：同一个英雄可能同时出现在 bottom 和 support，如果只按英雄名合并，后写入的位置会覆盖先写入的位置，导致配合或对位取到错误位置的数据。

时间趋势数据来自 build 页 Qwik JSON 里的 `time` / `timeWin` 字段。当前可直连的 `mega` 接口只覆盖梯队、协同和对位，不返回时间段字段；因此更新流程分成两层：

- 主动刷新：`python3 update_data.py --with-timeline` 使用 Scrapling 访问英雄 build 页，并从 Qwik JSON 解析 `stats_by_time`。
- 缓存兜底：如果 Scrapling 被 Cloudflare 拦截或页面没有返回 Qwik 数据，只在基础胜率和场次完全一致时，从本地旧快照继承 `stats_by_time`。

Scrapling 的 build 页 URL 默认只带 `lane / region`。当前版本不传 `patch`，历史版本才显式传 `patch=16.9` 这类参数。不要默认追加 `tier=emerald_plus` 或 `queue=ranked`，否则 Lolalytics 可能返回不同页面或错误口径；如果需要 Emerald 单段位等特殊口径，再显式传入对应参数。

时间趋势属于解释型数据，不参与推荐分主公式；它缺失时不应该阻断基础、配合、对位三类核心数据更新。

### `update_data.py`

负责更安全地更新数据。典型流程是：

```text
备份旧数据
  -> 运行 pre_fetch.py
  -> 校验 data/botlane_dataset.json
  -> 运行 API smoke tests
  -> 保留通过校验的新数据
```

可选时间趋势刷新：

```text
备份旧数据
  -> 运行 pre_fetch.py --with-timeline
  -> Scrapling 抓取 build 页 Qwik JSON
  -> 失败项使用同口径本地快照兜底
  -> 校验 data/botlane_dataset.json
  -> 运行 API smoke tests
```


## 推荐引擎

### `bp_engine.py`

推荐引擎接收 BP 状态和推荐目标位置，输出排序后的候选英雄列表。

输入的 BP 状态：

```python
{
    "ally_ad": None,
    "ally_sup": None,
    "enemy_ad": None,
    "enemy_sup": None,
}
```

主要职责：

- 标准化英雄 ID 和 BP 状态。
- 推导当前 BP 场景：S1 到 S8。
- 过滤已选择英雄和 `tier="?"` 的英雄。
- 计算基础表现、配合分、对位分。
- 根据当前场景动态调整权重。
- 输出推荐依据、缺失数据提示、可信度和解释文案。

更多评分细节见 [评分模型](scoring-model.md)。


## API 层

### `app.py`

Flask 服务负责：

- 加载离线数据快照。
- 加载中文名、别名、头像和搜索 token。
- 暴露 JSON API。
- 渲染 `templates/index.html`。

主要接口：

```text
GET  /
GET  /api/health
GET  /api/champions
POST /api/recommend
GET  /api/synergy/<champion>
GET  /api/counter/<champion>
GET  /api/matchup/<champion>
GET  /api/tier/<bottom|support|adc>
```

### `POST /api/recommend`

请求示例：

```json
{
  "role": "support",
  "top_n": 10,
  "bp_state": {
    "ally_ad": "lucian",
    "ally_sup": null,
    "enemy_ad": "jinx",
    "enemy_sup": "leona"
  }
}
```

返回内容包含：

- `state` / `state_info`：当前 BP 场景。
- `weights`：基础、配合、对位的实际权重。
- `results`：推荐列表。
- `precise_results`：更偏精选展示的推荐列表。
- `bp_display`：前端可直接展示的 BP 槽位信息。
- `summary`：可信度统计。


## 前端层

### `templates/index.html`

页面通过浏览器直接调用 Flask API：

- `/api/champions` 初始化英雄池、头像、中文名和搜索数据。
- `/api/recommend` 获取 BP 推荐。
- `/api/synergy/<champion>` 获取协同查询。
- `/api/counter/<champion>` 获取克制查询。
- `/api/tier/<lane>` 获取梯队榜。

页面必须通过 Flask 服务访问，不能直接打开 HTML 文件，否则 API 请求无法工作。


## 静态资源

头像资源位于：

```text
static/avatars/
```

英雄中文名和 ID 映射位于：

```text
scraper/chinese_getchampion/英雄名字.txt
scraper/chinese_getchampion/hero_id_mapping.py
```

玩家常用别名位于：

```text
champion_aliases.py
```
