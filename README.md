LoL 下路 BP 智能推荐助手
=========================

基于 Lolalytics 真实对局数据的下路 BP 分析与推荐工具，用 Flask API 和本地网页把 ADC/辅助推荐、协同、克制和梯队信息串起来。


功能
----

- BP 推荐：根据己方下路、敌方下路和目标位置推荐 ADC 或辅助。
- 协同查询：查看某个英雄和下路搭档的历史组合表现。
- 克制查询：查看面对某个英雄时更好用的 ADC/辅助选择。
- 梯队榜：查看当前数据快照里的 bottom/support 英雄表现。
- 中文搜索：支持中文名、英文名、英雄 ID 和常用别名。


快速启动
--------

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

启动后打开：

```text
http://127.0.0.1:8000/
```

如果需要更新本地数据：

```bash
python3 update_data.py
```

更新流程会先备份旧数据，重新抓取 Lolalytics 数据，校验 `data/botlane_dataset.json`，再运行 API 冒烟测试。任一步失败都会保留或恢复旧数据。

如果需要同时刷新时间趋势图数据：

```bash
python3 update_data.py --with-timeline
```

时间趋势会额外通过 Scrapling 访问英雄 build 页面，从页面 Qwik JSON 中解析 `time/timeWin`。如果遇到 Cloudflare 验证，可以改用有界面模式：

```bash
python3 update_data.py --with-timeline --timeline-headed
```

检查 API 是否可用：

```bash
python3 test_api.py
```


数据来源与免责声明
------------------

数据来自 Lolalytics 的公开统计接口，当前快照默认面向 KR / Emerald+ / Ranked / current patch 的 bottom 和 support 数据。推荐指数是项目内部排序分，不是真实胜率，也不是 Lolalytics 原始胜率。

核心抓取逻辑不依赖 Lolalytics 页面 HTML。页面本身可能被 Cloudflare 拦截，但基础、配合和对位数据更新使用 Lolalytics 的公开统计接口：

- `ep=tier`：拉取 bottom/support 梯队、胜率和场次。
- `ep=build-team`：拉取下路组合协同。
- `ep=counter`：拉取面对敌方 ADC / 辅助的对位表现。

默认不传 `patch` 参数，以对齐 Lolalytics 网页默认当前版本。Lolalytics 中 `patch=7` 表示最近 7 天，`patch=14` 表示最近 14 天，`patch=16.9` 这类值表示指定旧版本。

时间趋势是单独链路：`--with-timeline` 会用 Scrapling 抓取英雄 build 页源码，解析 Qwik JSON 里的分时段场次和胜场。build 页默认只带 `lane / region` 参数，历史版本才额外带 `patch`；如果抓不到，则只在胜率和场次同口径时继承本地旧快照，避免把不同版本的趋势混入当前数据。

当前版本刚更新时样本会很小，数据预热按 100 场作为最低入库门槛，并用前端可信度提示样本成熟度。

评分摘要
--------

推荐结果由三部分组成：

```text
原始推荐分 = 基础校准分 × 基础权重 + 配合校准分 × 配合权重 + 对位校准分 × 对位权重
最终推荐分 = 原始推荐分 - 语境惩罚
推荐指数 = 100 / (1 + 10^(-最终推荐分 / 400))
```

- 基础表现：英雄自身版本强度，只使用 Tier 先验和单英雄胜率；校准上限较低，避免强势英雄一项独大。
- 配合：候选英雄和己方下路搭档的纯净增益，主要看是否超出双方基础预期和样本成熟度，不再把组合绝对表现重复计入排序。
- 对位：候选英雄面对敌方 ADC / 辅助的纯净增益，主要看是否超出理论预期和样本成熟度，不再把对位绝对表现重复计入排序。
- 语境惩罚：当基础强度很高但当前配合/对位没有明显支持时，下调 Top 优先级，避免版本强势英雄无条件压过所有 BP 场景。
- 可信度：按参考场次分级，只说明样本是否充足，不代表一定更好赢。
- 时间趋势：来自英雄 build 页 Qwik JSON 的分时段场次/胜场；核心统计接口没有该字段，因此作为单独链路或缓存兜底，不参与推荐分计算。

详细公式见 [评分模型](docs/scoring-model.md)。

本项目仅用于英雄联盟 BP 学习、数据分析和个人试用，不代表 Riot Games 或 Lolalytics 官方观点。


文档
----

- [架构与数据流](docs/architecture.md)
- [评分模型](docs/scoring-model.md)
- [部署说明](docs/deployment.md)
