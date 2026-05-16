# 网页版短期试用发布方案

## 选择：Render Web Service

短期给朋友试用，建议先用 Render 托管当前 Flask 网页版。

理由：

- 自动提供 HTTPS 公网地址，朋友不用连你的局域网。
- 不需要买云服务器、配置 Nginx 或申请证书。
- 当前项目已经有 `requirements.txt`、`wsgi.py`、`data/botlane_dataset.json` 和 `static/avatars/`，很适合直接部署。
- 比本地穿透更稳定：你的电脑关机后，朋友仍然能访问线上页面。

不选本地穿透作为主方案的原因：

- 需要你的电脑和本地服务一直开着。
- 临时域名可能变化，朋友试用体验不稳定。
- 更适合临时演示，不适合持续几天收集反馈。

## 已补充的部署文件

- `render.yaml`：Render 蓝图配置，包含构建命令、启动命令和健康检查。
- `Procfile`：通用 Python Web 托管启动入口，也可用于 Railway 等平台。
- `.gitignore`：避免把虚拟环境、缓存、备份数据提交上去。

生产启动命令：

```bash
gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app
```

## 发布步骤

1. 把项目放到一个 GitHub 仓库。

   需要提交这些运行文件：

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

2. 打开 Render Dashboard，选择 New -> Web Service。

3. 连接这个 GitHub 仓库。

4. 如果 Render 识别到 `render.yaml`，可以按蓝图创建；如果手动填写，则使用：

   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app`
   - Health Check Path: `/api/health`

5. 部署完成后，Render 会给出一个 `https://...onrender.com` 地址。

6. 先打开：

   ```text
   https://你的地址.onrender.com/api/health
   ```

   返回 `ok: true` 后，再把主页地址发给朋友：

   ```text
   https://你的地址.onrender.com/
   ```

## 试用期注意事项

- 当前数据文件是随代码发布的静态快照。更新数据后，需要重新提交并部署。
- 免费或低配实例可能会有冷启动，第一次打开会慢一点。
- 这是临时试用方案；如果后续要做长期稳定访问，再迁移到云服务器、容器平台或稳定付费实例，并绑定自有域名。

## 数据自动更新

项目已加入 GitHub Actions 定时任务：

```text
.github/workflows/update-data.yml
```

它会每天 06:00 UTC 自动运行，相当于北京时间 14:00。流程会：

1. 安装项目依赖。
2. 运行 `python update_data.py`。
3. 校验数据和 API。
4. 如果 `data/botlane_dataset.json` 有变化，就自动提交并推送。
5. Render 收到 GitHub 新提交后自动重新部署。

如果 GitHub Actions 第一次提交失败，需要在 GitHub 仓库设置里打开写权限：

```text
Settings -> Actions -> General -> Workflow permissions -> Read and write permissions
```

也可以在 GitHub Actions 页面手动点 `Update botlane dataset` -> `Run workflow` 立即更新一次。
