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

检查 API 是否可用：

```bash
python3 test_api.py
```


数据来源与免责声明
------------------

数据来自 Lolalytics 的公开统计接口，当前快照默认面向 KR / Emerald+ / Ranked / current patch 的 bottom 和 support 数据。推荐指数是项目内部排序分，不是真实胜率，也不是 Lolalytics 原始胜率。

当前抓取逻辑不再依赖 Lolalytics 页面 HTML。页面本身可能被 Cloudflare 拦截，但数据更新使用 Lolalytics 的公开统计接口：

- `ep=tier`：拉取 bottom/support 梯队、胜率和场次。
- `ep=build-team`：拉取下路组合协同。
- `ep=counter`：拉取面对敌方 ADC / 辅助的对位表现。

默认不传 `patch` 参数，以对齐 Lolalytics 网页默认当前版本；`patch=7` 表示最近 7 天，`patch=14` 表示最近 14 天，`patch=16.9` 这类值表示指定旧版本。

评分摘要
--------

推荐结果由三部分组成：

```text
推荐分 = 基础校准分 × 基础权重 + 配合校准分 × 配合权重 + 对位校准分 × 对位权重
推荐指数 = 100 / (1 + 10^(-推荐分 / 400))
```

- 基础表现：英雄自身版本强度，只使用 Tier 先验和单英雄胜率；校准上限较低，避免强势英雄一项独大。
- 配合：候选英雄和己方下路搭档的组合表现，同时看绝对胜率、是否超出双方基础预期、样本成熟度。
- 对位：候选英雄面对敌方 ADC / 辅助的表现，同时看绝对胜率、是否超出理论预期、样本成熟度。
- 可信度：按参考场次分级，只说明样本是否充足，不代表一定更好赢。

详细公式见 [评分模型](docs/scoring-model.md)。

本项目仅用于英雄联盟 BP 学习、数据分析和个人试用，不代表 Riot Games 或 Lolalytics 官方观点。


文档
----

- [架构与数据流](docs/architecture.md)
- [评分模型](docs/scoring-model.md)
- [部署说明](docs/deployment.md)
