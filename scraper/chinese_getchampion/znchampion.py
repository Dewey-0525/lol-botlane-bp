import requests
import json
import os
import time

# --- 路径配置（跨平台通用版）---
# 获取当前脚本所在的文件夹
script_dir = os.path.dirname(os.path.abspath(__file__))
# 定义输出目录为脚本同级目录下的 chinese_getchampion
out_dir = os.path.join(script_dir, "chinese_getchampion")
# 定义其他子路径
avatar_dir = os.path.join(script_dir, "static", "avatars")
cn_txt_path = os.path.join(out_dir, "英雄名字.txt")
en_txt_path = os.path.join(out_dir, "hero_names.txt")
mapping_path = os.path.join(out_dir, "hero_id_mapping.py")

# 确保目录存在
os.makedirs(avatar_dir, exist_ok=True)
# ------------------------------

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 请求数据
url = "https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js"
print("正在获取英雄数据...")
resp = requests.get(url, headers=headers, timeout=15)

raw = resp.content
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    text = raw.decode("latin-1").encode("latin-1").decode("utf-8")

if "heroList" in text:
    text = text[text.index("{"):]

data = json.loads(text)
heroes = data.get("hero", [])
total = len(heroes)
print(f"数据源共 {total} 个英雄\n")

# 下载头像
img_base = "https://game.gtimg.cn/images/lol/act/img/champion/"
ok = 0
skip = 0
fail = 0
fail_list = []
cn_names = []
en_names = []
mapping_lines = []

for hero in heroes:
    hero_id = hero.get("heroId", "")
    cn_name = hero.get("name", hero_id)
    en_name = hero.get("alias", hero_id)
    en_key = en_name.lower()
    cn_names.append(cn_name)
    en_names.append(en_name)
    mapping_lines.append(f'    "{en_key}": {hero_id}')
    path = os.path.join(avatar_dir, hero_id + ".png")

    if os.path.exists(path):
        skip += 1
        continue

    img_url = img_base + en_name + ".png"
    downloaded = False

    for attempt in range(3):
        try:
            r = requests.get(img_url, headers=headers, timeout=15)
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
                ok += 1
                print(f"  + {cn_name} / {en_name} (id:{hero_id})")
                downloaded = True
                break
        except Exception:
            time.sleep(1)

    if not downloaded:
        fail += 1
        fail_list.append(f"{cn_name}({en_name})")

# 写入中文名txt
with open(cn_txt_path, "w", encoding="utf-8") as f:
    for n in cn_names:
        f.write(n + "\n")

# 写入英文名txt
with open(en_txt_path, "w", encoding="utf-8") as f:
    for n in en_names:
        f.write(n + "\n")

# 写入映射表 py 文件
mapping_content = "HERO_ID_MAPPING = {\n"
mapping_content += ",\n".join(mapping_lines)
mapping_content += "\n}"
with open(mapping_path, "w", encoding="utf-8") as f:
    f.write(mapping_content)

# 汇总
print(f"\n===== 结果 =====")
print(f"数据源总数:   {total}")
print(f"已有跳过:     {skip}")
print(f"新增下载:     {ok}")
print(f"下载失败:     {fail}")
print(f"中文名txt:    {cn_txt_path}")
print(f"英文名txt:    {en_txt_path}")
print(f"ID映射表:     {mapping_path}")
if fail_list:
    print(f"失败列表: {', '.join(fail_list)}")
