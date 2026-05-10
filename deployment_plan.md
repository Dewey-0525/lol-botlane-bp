# 后端部署规划

本文档用于规划 Flask API 如何部署到微信小程序可访问的线上环境。

当前本地服务：

```text
http://127.0.0.1:8000
```

小程序正式环境需要：

```text
https://你的域名
```


一、为什么必须部署后端
----------------------

微信小程序不能直接运行本项目的 Python 文件。

当前架构是：

```text
小程序前端
  -> 请求 HTTPS API
  -> Flask 后端 app.py
  -> bp_engine.py
  -> data/botlane_dataset.json
```

所以正式上线必须有一个公网可访问的 HTTPS 后端。


二、部署必须满足的条件
----------------------

微信小程序正式请求后端时通常需要：

1. HTTPS。
2. 可公网访问的域名。
3. 在微信小程序后台配置 request 合法域名。
4. 后端接口不能只在本机 `127.0.0.1`。
5. 静态头像资源也要能通过 HTTPS 访问。

开发阶段可以继续用：

```text
http://127.0.0.1:8000
```

但真机和正式上线不能依赖这个地址。


三、推荐部署路线
----------------

### 临时试用优先路线：Render Web Service

适合：

- 先把网页版发给几个朋友试用
- 不想现在就买云服务器
- 希望快速拿到 HTTPS 公网地址

结构：

```text
用户浏览器
  -> Render HTTPS 地址
  -> Gunicorn
  -> Flask app.py
```

优点：

- 上线速度最快
- 自动提供 HTTPS
- 不需要配置 Nginx、域名证书和服务器安全组
- 当前项目数据和头像体积很小，适合随代码一起部署

缺点：

- 免费或低配实例可能有冷启动
- 数据更新后需要重新部署
- 后续正式小程序上线仍建议绑定自有域名

当前已补：

```text
render.yaml
Procfile
.gitignore
public_trial_deploy.md
```

短期试用建议优先级：最高。


### 路线 A：云服务器 + Nginx + Gunicorn

适合：

- 想掌握完整后端控制权
- 后续可能要做定时任务
- 想自己管理数据文件和爬取脚本

结构：

```text
用户/小程序
  -> HTTPS 域名
  -> Nginx
  -> Gunicorn
  -> Flask app.py
```

优点：

- 控制力强
- 适合长期维护
- 可以直接运行 `pre_fetch.py`
- 可以用定时任务更新数据

缺点：

- 需要维护服务器
- 需要配置 HTTPS
- 需要了解 Linux 基础

建议优先级：正式长期维护时高。


### 路线 B：云托管 / 容器部署

适合：

- 不想直接管理服务器
- 希望部署流程更自动化
- 可以接受平台约束

结构：

```text
用户/小程序
  -> 平台 HTTPS 域名或自定义域名
  -> 容器
  -> Flask app.py
```

优点：

- 运维负担较低
- 更容易做自动部署

缺点：

- 需要适配平台部署方式
- 定时数据更新可能要单独配置
- 平台迁移成本可能更高

建议优先级：中。


### 路线 C：只做本地工具，不上线

适合：

- 暂时只自己使用
- 先完善算法和 UI
- 不急着发小程序

优点：

- 不花服务器成本
- 开发速度最快

缺点：

- 微信小程序无法正式使用
- 只能本机访问

建议优先级：当前开发阶段可用，正式小程序不可用。


四、本项目部署前需要补的文件
----------------------------

当前已有：

- `requirements.txt`
- `app.py`
- `data/botlane_dataset.json`
- `static/avatars/`
- `test_api.py`
- `wsgi.py`
- `render.yaml`
- `Procfile`
- `.gitignore`

如果走云服务器 + Gunicorn，已有：

```text
wsgi.py
```

用于生产服务器启动：

```python
from app import app, get_db

get_db()
```

如果走容器部署，再补：

```text
Dockerfile
.dockerignore
```

如果走云服务器 + Gunicorn，安装：

```bash
python3 -m pip install gunicorn
```

并把 `gunicorn` 加入 `requirements.txt`。


五、生产启动方式建议
--------------------

本地开发：

```bash
python3 app.py
```

生产环境不要直接用 Flask 开发服务器。

生产推荐：

```bash
gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app
```

再由 Nginx 对外提供 HTTPS：

```text
https://你的域名
  -> Nginx
  -> 127.0.0.1:8000
```


六、数据更新方案
----------------

当前数据文件：

```text
data/botlane_dataset.json
```

更新命令：

```bash
python3 update_data.py
```

建议更新策略：

### MVP 阶段

手动更新：

```bash
python3 update_data.py
重启后端服务
```

### 稳定阶段

定时更新：

```text
每天或每几天运行一次 pre_fetch.py
成功后运行 test_api.py
成功后重启服务
失败则保留旧数据
```

现在项目已提供安全更新脚本：

```bash
python3 update_data.py
```

它会自动：

1. 备份旧数据到 `data/backups/`
2. 运行 `pre_fetch.py`
3. 校验新数据结构和覆盖率
4. 运行 `test_api.py`
5. 失败时恢复旧数据

重要原则：

- 不要在用户请求时实时爬 Lolalytics。
- 推荐接口只读本地 JSON。
- 数据更新失败时不要覆盖旧数据。


### 本地 macOS 定时更新

本地开发阶段可以用 `launchd` 或 `cron` 定时运行：

```bash
cd /Users/yzsw/Desktop/lol-botlane-bp
python3 update_data.py
```

限制：

- 电脑关机不会运行
- 电脑睡眠通常不会运行
- 网络不通会失败

因此本地定时只适合开发阶段。


### 服务器 Linux 定时更新

正式部署后建议在服务器上设置定时任务，例如每天凌晨运行：

```cron
0 4 * * * cd /path/to/lol-botlane-bp && /usr/bin/python3 update_data.py >> logs/update_data.log 2>&1
```

如果后端进程不会自动重新加载 JSON，可以在更新成功后重启服务。


七、上线前检查清单
------------------

部署前运行：

```bash
python3 test_api.py
```

检查接口：

```text
GET  /api/health
GET  /api/champions
POST /api/recommend
GET  /api/synergy/jinx
GET  /api/counter/jinx
GET  /api/tier/support
```

检查静态资源：

```text
https://你的域名/static/avatars/222.png
```

检查小程序后台：

- request 合法域名已配置
- 域名是 HTTPS
- 证书有效
- API 返回 JSON


八、小程序 API 地址配置
----------------------

小程序端建议集中维护：

```js
const API_BASE = 'https://你的域名'
```

开发环境：

```js
const API_BASE = 'http://127.0.0.1:8000'
```

正式环境：

```js
const API_BASE = 'https://你的域名'
```

注意：

- 本机 `127.0.0.1` 只对当前电脑有效。
- 真机调试通常不能直接访问电脑的 `127.0.0.1`。
- 真机调试需要局域网地址、开发工具代理或临时公网地址。


九、最小上线版本建议
--------------------

MVP 版本只需要：

1. Flask API 可 HTTPS 访问。
2. `/api/champions` 可用。
3. `/api/recommend` 可用。
4. 头像资源可访问。
5. 数据更新时间可展示。
6. `test_api.py` 通过。

其他功能可以后续再上线：

- 协同查询
- 克制查询
- 梯队榜
- 自动数据更新
- 历史记录
- 用户收藏


十、下一步建议
--------------

建议接下来做：

1. 增加 `wsgi.py`。
2. 把 `gunicorn` 加入 `requirements.txt`。
3. 写一个 `deploy_check.py` 或继续扩展 `test_api.py`。
4. 再决定使用云服务器还是云托管。

如果暂时还不部署，可以先进入小程序前端开发；等页面成型后再部署后端。
