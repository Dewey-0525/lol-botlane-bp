# 微信小程序迁移规划

本文档用于把当前 Flask + Web 原型迁移为微信小程序。

当前 Web 原型已经验证过的核心交互：

- BP 推荐
- 协同查询
- 克制查询
- 梯队榜
- 中文名、英文名、英雄 ID、玩家常用别名搜索
- 依据、可信度、缺失数据提示


一、整体架构
------------

小程序端不直接运行 Python。小程序通过 HTTPS 请求后端 API。

```text
微信小程序
  -> HTTPS API
  -> Flask 后端 app.py
  -> bp_engine.py 推荐算法
  -> data/botlane_dataset.json 本地数据
```

当前本地开发地址：

```text
http://127.0.0.1:8000
```

正式小程序需要替换成线上 HTTPS 域名。


二、小程序页面结构
------------------

建议页面：

```text
pages/bp/index          BP 推荐，主页面
pages/synergy/index     协同查询
pages/counter/index     克制查询
pages/tier/index        梯队榜
pages/about/index       数据说明/使用说明，可后置
```

底部 Tab 建议：

```text
BP 推荐 | 查询 | 梯队 | 我的/说明
```

如果想保持当前 Web 原型结构，也可以顶部使用分段控件：

```text
BP 推荐 | 协同查询 | 克制查询 | 梯队榜
```

推荐优先级：

1. `pages/bp/index`
2. `pages/synergy/index`
3. `pages/counter/index`
4. `pages/tier/index`


三、页面功能规格
----------------

### 1. BP 推荐页

核心页面，对应当前 Web 原型的主功能。

控件：

- 推荐位置：`support` / `adc`
- 己方 ADC
- 己方辅助
- 敌方 ADC
- 敌方辅助
- 排序方式：
  - 推荐结果
  - 推荐指数
  - 基础表现
  - 对位
  - 配合分
- 可信度筛选：
  - 全部
  - 高可信
  - 中及以上

展示：

- BP 状态说明
- 推荐列表
- 英雄头像
- 中文名 / 英文名
- 梯队
- 推荐指数
- 基础表现
- 对位分
- 配合分
- 依据
- 可信度
- 缺失数据提示

交互规则：

- 输入别名后，建议列表展示官方中文名。
- 选中后，槽位显示官方中文名，例如 `曙光女神`。
- 发送给后端的值仍然使用英雄 key，例如 `leona`。
- 已经在四个槽位中出现的英雄，不应出现在推荐结果中。


### 2. 协同查询页

用途：查看某个英雄的最佳搭档。

控件：

- 英雄搜索框

接口：

```text
GET /api/synergy/<champion>?top_n=30
```

展示字段：

- 排名
- 英雄头像
- 中文名 / 英文名
- 梯队
- 胜率


### 3. 克制查询页

用途：敌方选择某英雄时，推荐我方 ADC / 辅助反制。

控件：

- 敌方英雄搜索框

接口：

```text
GET /api/counter/<champion>?top_n=10
```

展示：

- 推荐 ADC 列表
- 推荐辅助列表

展示字段：

- 排名
- 英雄头像
- 中文名 / 英文名
- 梯队
- 我方胜率


### 4. 梯队榜页

用途：查看当前版本下路 ADC / 辅助梯队。

控件：

- 位置选择：ADC / 辅助

接口：

```text
GET /api/tier/bottom
GET /api/tier/support
```

展示字段：

- 排名
- 英雄头像
- 中文名 / 英文名
- 梯队


四、API 对接规范
----------------

### 1. 英雄列表

```text
GET /api/champions
```

用途：

- 初始化搜索框数据
- 构建英雄选择器
- 获取中文名、别名、头像

关键字段：

```json
{
  "id": "leona",
  "name": "Leona",
  "cn_name": "曙光女神",
  "aliases": ["日女", "蕾欧娜", "曙光女神"],
  "champion_id": 89,
  "tier": "A",
  "avatar": "/static/avatars/89.png",
  "search_text": "曙光女神 leona leona 89 日女 蕾欧娜 曙光女神"
}
```

小程序端建议缓存该接口返回值，避免每次页面切换都请求。


### 2. BP 推荐

```text
POST /api/recommend
```

请求：

```json
{
  "role": "support",
  "top_n": 30,
  "bp_state": {
    "ally_ad": "lucian",
    "ally_sup": null,
    "enemy_ad": "jinx",
    "enemy_sup": "leona"
  }
}
```

响应关键字段：

```json
{
  "ok": true,
  "role": "support",
  "role_label": "辅助",
  "state": "S8",
  "state_info": {
    "label": "下路信息完整",
    "description": "会一起看三件事：英雄本身强不强、和队友搭不搭、打敌方下路好不好打。"
  },
  "weights": {
    "base": 0.3,
    "synergy": 0.35,
    "counter": 0.35
  },
  "summary": {
    "total": 30,
    "confidence_counts": {
      "high": 10,
      "medium": 8,
      "low": 12
    },
    "complete_data_count": 10
  },
  "excluded_champions": ["jinx", "leona", "lucian"],
  "results": []
}
```

`results` 单项字段：

```json
{
  "name": "seraphine",
  "display_name": "星籁歌姬",
  "english_name": "Seraphine",
  "champion_id": 147,
  "avatar": "/static/avatars/147.png",
  "tier": "S",
  "final_rating": 18.37,
  "display_winrate": 52.64,
  "base_rating": 46.47,
  "synergy_bonus": 2.28,
  "synergy_adjusted_score": 2.28,
  "synergy_absolute_score": 8.31,
  "synergy_residual_score": -9.62,
  "counter_bonus": 10.37,
  "counter_adjusted_score": 10.37,
  "counter_absolute_score": 18.2,
  "counter_residual_score": -1.37,
  "evidence_label": "对位、配合、基础表现",
  "missing_labels": [],
  "confidence_level": "high",
  "confidence_label": "高",
  "effective_weights": {
    "base": 0.3,
    "synergy": 0.35,
    "counter": 0.35
  }
}
```

评分模型说明：

```text
推荐分 = 基础表现 × 基础权重 + 配合分 × 配合权重 + 对位分 × 对位权重
推荐指数 = 100 / (1 + 10^(-推荐分 / 400))
```

推荐指数只是模型排序值，不应在小程序文案里写成真实胜率。

配合分：

```text
配合分 = 置信度 × (65% × 组合绝对表现 + 35% × 超预期配合)
```

对位分：

```text
对位分 = 置信度 × (60% × 对位绝对表现 + 40% × 超预期对位)
```

动态权重：

```text
盲选：基础 100%
只知道己方搭档：基础 35%，配合 65%
只知道敌方信息：基础 40%，对位 60%
己方搭档 + 敌方信息：基础 30%，配合 35%，对位 35%
```


五、小程序端数据结构建议
------------------------

### champion

```js
{
  id: 'leona',
  cnName: '曙光女神',
  enName: 'Leona',
  aliases: ['日女', '蕾欧娜'],
  tier: 'A',
  avatar: 'https://your-domain.com/static/avatars/89.png',
  searchText: '...'
}
```

### bpState

```js
{
  ally_ad: null,
  ally_sup: null,
  enemy_ad: null,
  enemy_sup: null
}
```

### recommendation

```js
{
  name: 'seraphine',
  displayName: '星籁歌姬',
  tier: 'S',
  finalRating: 18.37,
  recommendationIndex: 52.64,
  baseRating: 46.47,
  counterScore: 10.37,
  synergyScore: 2.28,
  evidenceLabel: '对位、配合、基础表现',
  confidenceLabel: '高',
  missingLabels: []
}
```


六、搜索选择器设计
------------------

搜索框输入：

- 官方中文名：`曙光女神`
- 常用别名：`日女`
- 英文：`leona`
- 英雄 ID：`89`

建议列表展示：

```text
曙光女神
Leona / A
匹配：日女
```

选中后槽位显示：

```text
曙光女神
```

内部保存：

```js
'leona'
```

注意：别名只用于搜索匹配，不作为最终展示名。


七、排序和筛选
--------------

BP 推荐页前端排序：

- 推荐结果：`final_rating`
- 推荐指数：`display_winrate`
- 基础表现：`base_rating`
- 梯队：`S+ > S > S- > A+ ...`
- 对位分：`counter_bonus`
- 配合分：`synergy_bonus`

可信度筛选：

- 全部
- 高可信：`confidence_level === 'high'`
- 中及以上：`confidence_level !== 'low'`

这些排序和筛选可以只在小程序端完成，不需要额外请求后端。


八、部署要求
------------

微信小程序正式请求后端时需要：

- HTTPS 域名
- 小程序后台配置 request 合法域名
- 后端服务可公网访问
- 静态头像资源可通过 HTTPS 访问

开发阶段可以：

- 本地 Flask 服务继续用于调试
- 小程序开发工具中配置开发环境接口地址
- 真机调试时需要局域网地址或临时公网隧道

正式阶段建议：

- Flask API 部署到云服务器或云托管
- 使用 Nginx + HTTPS
- 定期运行 `pre_fetch.py` 更新数据
- 保留 `test_api.py` 作为上线前冒烟测试


九、迁移步骤建议
----------------

1. 创建微信小程序项目。
2. 实现全局 API 配置：

```js
const API_BASE = 'https://your-domain.com'
```

3. 实现 `GET /api/champions`，缓存英雄列表。
4. 实现通用英雄搜索选择组件。
5. 实现 BP 推荐页。
6. 实现协同查询页。
7. 实现克制查询页。
8. 实现梯队榜页。
9. 接入真实 HTTPS 后端。
10. 真机测试和小程序审核准备。


十、暂不处理的问题
------------------

这些可以后置：

- 登录系统
- 用户收藏
- 历史记录
- 自定义服务器分区
- 多版本补丁选择
- 自动定时更新数据
- 数据置信区间和样本数展示

当前阶段优先目标：

```text
把已验证的 Web 原型稳定迁移成小程序 MVP。
```
