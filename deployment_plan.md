# 网页版部署规划

本文档用于记录 Flask 网页版如何部署到公网环境。

当前本地服务：

```text
http://127.0.0.1:8000
```

当前线上服务：

```text
https://lol-botlane-bp.onrender.com/
```


一、当前部署方案
----------------

当前使用 Render Web Service 托管 Flask 应用：

```text
用户浏览器
  -> Render HTTPS 地址
  -> Gunicorn
  -> Flask app.py
  -> bp_engine.py
  -> data/botlane_dataset.json
```

已包含：

- `requirements.txt`
- `app.py`
- `wsgi.py`
- `render.yaml`
- `Procfile`
- `templates/`
- `static/avatars/`
- `data/botlane_dataset.json`
- `.github/workflows/update-data.yml`


二、Render 启动方式
-------------------

构建命令：

```bash
pip install -r requirements.txt
```

启动命令：

```bash
gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app
```

健康检查：

```text
/api/health
```


三、数据更新方案
----------------

线上数据文件：

```text
data/botlane_dataset.json
```

本地手动更新：

```bash
python3 update_data.py
```

只检查当前数据：

```bash
python3 update_data.py --check-only
```

项目已配置 GitHub Actions 自动更新：

```text
.github/workflows/update-data.yml
```

运行时间：

```text
每天 06:00 UTC，也就是北京时间 14:00
```

流程：

```text
安装依赖
  -> 运行 python update_data.py
  -> 运行 API 测试
  -> 数据有变化则提交 data/botlane_dataset.json
  -> Render 自动部署
```


四、检查清单
------------

本地检查：

```bash
python3 test_api.py
```

线上健康检查：

```text
https://lol-botlane-bp.onrender.com/api/health
```

常用接口：

```text
GET  /api/health
GET  /api/champions
POST /api/recommend
GET  /api/synergy/jinx
GET  /api/counter/jinx
GET  /api/tier/support
```

静态头像：

```text
https://lol-botlane-bp.onrender.com/static/avatars/222.png
```


五、后续可选路线
----------------

Render 适合短期试用和轻量分享。若后续需要中国大陆更稳定访问，可以迁移到：

- 腾讯云轻量应用服务器
- 阿里云 ECS
- 国内云托管或容器平台

长期部署通常建议：

- 绑定自有域名
- 使用 HTTPS
- 配置 Nginx 反向代理
- 将数据更新和部署日志纳入固定监控
