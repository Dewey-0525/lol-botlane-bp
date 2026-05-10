LoL 下路 BP 智能推荐助手
=========================

基于 Lolalytics 真实对局数据的下路 BP 分析与推荐工具。

当前项目已经包含：

- 本地数据预热脚本
- 推荐算法核心模块
- 命令行调试入口
- Flask API 后端
- 本地可交互产品原型页面


项目结构
--------

- `bp_engine.py`
  推荐算法核心。负责 BP 状态推导、动态权重、缺失数据处理、可信度和依据字段。

- `app.py`
  Flask API 服务。微信小程序未来可以通过这些接口调用推荐结果。

- `wsgi.py`
  生产环境 WSGI 入口，可配合 Gunicorn/Nginx 部署。

- `templates/index.html`
  本地产品原型页面。包含 BP 推荐、协同查询、克制查询、梯队榜。

- `main.py`
  命令行调试入口。

- `pre_fetch.py`
  从 Lolalytics 预热数据，生成 `data/botlane_dataset.json`。

- `update_data.py`
  安全数据更新脚本。会备份旧数据、运行预热、校验数据并执行 API 测试。

- `champion_aliases.py`
  中文玩家常用英雄别名表，用于搜索框匹配。

- `test_api.py`
  基础 API 回归测试。

- `miniapp_plan.md`
  微信小程序迁移规划。

- `deployment_plan.md`
  后端部署规划。


安装依赖
--------

```bash
cd /Users/yzsw/Desktop/lol-botlane-bp
python3 -m pip install -r requirements.txt
```


生成/更新数据
-------------

如果 `data/botlane_dataset.json` 不存在，或你想更新数据：

```bash
python3 update_data.py
```

只检查当前数据是否可用：

```bash
python3 update_data.py --check-only
```

当前数据源配置：

- 地区：KR
- 段位：Emerald+
- 队列：Ranked
- 位置：bottom / support


启动本地服务
------------

```bash
python3 app.py
```

启动后打开：

```text
http://127.0.0.1:8000/
```

注意：不要直接打开 `templates/index.html` 文件。页面必须通过 Flask 服务访问，否则 API 请求无法工作。


生产启动入口
------------

生产环境不要直接使用 Flask 开发服务器。

可使用 Gunicorn 启动：

```bash
gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app
```

正式部署时通常再由 Nginx 提供 HTTPS，并反向代理到 `127.0.0.1:8000`。


短期公网试用
------------

如果只是先把网页版发给几个朋友试用，当前推荐用 Render Web Service：

- 不需要自己买服务器或配置 Nginx。
- 平台会提供 HTTPS 公网地址。
- 当前项目已补充 `render.yaml` 和 `Procfile`，可以直接按托管平台的 Python Web 服务方式启动。

详见：

```text
public_trial_deploy.md
```


主页面功能
----------

页面顶部包含四个功能：

- BP 推荐
- 协同查询
- 克制查询
- 梯队榜

BP 推荐是核心功能。支持：

- 推荐辅助 / 推荐 ADC
- 己方 ADC、己方辅助、敌方 ADC、敌方辅助四个槽位
- 中文名、英文名、英雄 ID、玩家常用别名搜索
- 自动过滤已被选择的英雄
- 按推荐结果、推荐指数、基础表现、对位、配合排序
- 按可信度筛选
- 展示依据、缺失数据提示、BP 状态说明


推荐规则概要
------------

`bp_state` 结构：

```python
{
    "ally_ad": None,
    "ally_sup": None,
    "enemy_ad": None,
    "enemy_sup": None,
}
```

状态推导：

- S1：双方下路未知，主要看基础表现。
- S2：已知己方搭档，优先看配合。
- S3：只知道敌方 ADC，优先看对 ADC 的克制。
- S4：只知道敌方辅助，优先看对辅助的克制。
- S5：已知敌方下路组合，综合双人对位和基础表现。
- S6：己方搭档 + 敌方 ADC，综合配合和克制。
- S7：己方搭档 + 敌方辅助，综合配合和克制。
- S8：己方搭档和敌方下路组合都已知，综合基础表现、配合和对位。

评分模型：

```text
推荐分 = 基础表现 × 基础权重 + 配合分 × 配合权重 + 对位分 × 对位权重
推荐指数 = 100 / (1 + 10^(-推荐分 / 400))
```

推荐指数是排序用的模型值，不是真实胜率，也不是 Lolalytics 原始胜率。

动态权重：

```text
S1 盲选：基础 100%
只知道己方搭档：基础 35%，配合 65%
只知道敌方信息：基础 40%，对位 60%
己方搭档 + 敌方信息：基础 30%，配合 35%，对位 35%
```

基础表现：

```text
基础表现 = Tier 先验 + 胜率修正
胜率修正 = winrateToRating(平滑英雄胜率) × min(1, sqrt(英雄场次 / 3000))
平滑英雄胜率 = (胜场 + 1000 × 50%) / (场次 + 1000)
```

配合分：

```text
配合分 = 置信度 × (65% × 组合绝对表现 + 35% × 超预期配合)
组合绝对表现 = winrateToRating(组合胜率相对 50% 的表现)
超预期配合 = 组合实际 rating - 双方期望 rating
```

对位分：

```text
对位分 = 置信度 × (60% × 对位绝对表现 + 40% × 超预期对位)
超预期对位 = 实际对位 rating - 理论对位 rating
```

设计原则：

- 组合/对位不会只看“是否超预期”，也会看实际胜率是否足够好。
- `Tier` 先验用于基础表现，不参与配合/对位的期望值，避免强英雄被重复抬高期望。
- 推荐辅助时更重视敌方辅助对位，推荐 ADC 时更重视敌方 ADC 对位。

数据处理原则：

- 正式推荐过滤 `tier="?"` 的英雄。
- 已选英雄不会进入推荐结果。
- 缺失的 synergy/counter 数据不按 0 分当作中性优势。
- 有队友但缺配合样本时，会做轻微保守惩罚。
- 有敌方但缺对位样本时，会按缺失比例做轻微保守惩罚。
- 返回 `evidence_label`、`missing_labels`、`confidence_label` 供前端解释推荐依据。


API 接口
--------

健康检查：

```text
GET /api/health
```

英雄列表：

```text
GET /api/champions
```

智能推荐：

```text
POST /api/recommend
```

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

常用查询：

```text
GET /api/synergy/<champion>
GET /api/counter/<champion>
GET /api/matchup/<champion>
GET /api/tier/<bottom|support>
```


命令行调试
----------

推荐辅助：

```bash
python3 main.py recommend support ally_ad=lucian enemy_ad=jinx enemy_sup=leona
```

推荐 ADC：

```bash
python3 main.py recommend adc ally_sup=thresh enemy_ad=jinx enemy_sup=leona
```

查看梯队：

```bash
python3 main.py tier support
python3 main.py tier bottom
```

协同查询：

```bash
python3 main.py synergy jinx
```

克制查询：

```bash
python3 main.py matchup jinx
python3 main.py counter jinx
```


运行测试
--------

```bash
python3 test_api.py
```

成功时输出：

```text
OK: API smoke tests passed
```


后续方向
--------

建议下一步：

1. 继续补全 `champion_aliases.py` 中的玩家常用别名。
2. 参考 `miniapp_plan.md` 将当前 Web 原型迁移为微信小程序页面。
3. 参考 `deployment_plan.md` 部署 Flask API 到可被小程序访问的后端服务。
4. 增加数据更新时间展示和一键刷新数据流程。
