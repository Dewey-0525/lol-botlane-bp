# 部署说明

本文档合并原 `deployment_plan.md` 和 `public_trial_deploy.md`，分为 Render 快速试用和正式生产部署两种路径。


## Render 快速试用

短期给朋友试用，推荐先用 Render Web Service 托管当前 Flask 网页版。

适合场景：

- 想快速得到一个 HTTPS 公网地址。
- 不想先购买服务器、配置 Nginx 或申请证书。
- 可以接受免费或低配实例的冷启动。
- 当前数据作为随代码发布的静态快照即可。

当前线上服务：

```text
https://lol-botlane-bp.onrender.com/
```

### 项目内置部署文件

- `render.yaml`：Render 蓝图配置。
- `Procfile`：通用 Python Web 托管启动入口。
- `requirements.txt`：Python 依赖。
- `wsgi.py`：生产 WSGI 入口。

### Render 配置

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

### 发布步骤

1. 把项目放到 GitHub 仓库。
2. 打开 Render Dashboard，选择 New -> Web Service。
3. 连接这个 GitHub 仓库。
4. 如果 Render 识别到 `render.yaml`，可以按蓝图创建。
5. 如果手动填写，则使用上面的 Build Command、Start Command 和 Health Check Path。
6. 部署完成后先访问 `/api/health`，确认 `ok: true` 后再分享主页。

需要提交的运行文件包括：

- `app.py`
- `wsgi.py`
- `bp_engine.py`
- `champion_aliases.py`
- `requirements.txt`
- `render.yaml`
- `Procfile`
- `templates/`
- `static/`
- `data/botlane_dataset.json`
- `scraper/chinese_getchampion/英雄名字.txt`
- `scraper/chinese_getchampion/hero_id_mapping.py`

### 数据自动更新

项目已配置 GitHub Actions：

```text
.github/workflows/update-data.yml
```

默认运行时间：

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

如果 GitHub Actions 第一次自动提交失败，需要在 GitHub 仓库设置里打开写权限：

```text
Settings -> Actions -> General -> Workflow permissions -> Read and write permissions
```


## 正式生产部署

正式生产环境不建议直接使用 Flask 开发服务器。推荐使用：

```text
用户浏览器
  -> HTTPS / 域名
  -> Nginx 或平台负载均衡
  -> Gunicorn
  -> Flask app.py
  -> bp_engine.py
  -> data/botlane_dataset.json
```

### Gunicorn 启动

```bash
gunicorn -w 2 -b 127.0.0.1:8000 wsgi:app
```

正式环境通常再由 Nginx 提供 HTTPS，并反向代理到 `127.0.0.1:8000`。

### 生产检查清单

本地检查：

```bash
python3 test_api.py
```

线上健康检查：

```text
https://你的域名/api/health
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

静态头像检查：

```text
https://你的域名/static/avatars/222.png
```

### 长期部署建议

- 绑定自有域名。
- 使用 HTTPS。
- 使用 Gunicorn 或同类 WSGI 服务承载 Flask。
- 使用 Nginx、平台网关或负载均衡处理公网入口。
- 固定数据更新任务，并保留更新日志。
- 保留 `/api/health` 作为健康检查入口。
- 如果需要中国大陆更稳定访问，可考虑腾讯云轻量应用服务器、阿里云 ECS、国内云托管或容器平台。
