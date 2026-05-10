"""
riot.py - 从 Riot CDN 获取英雄列表（免费，无需 API Key）
"""

import requests


def get_current_version():
    """获取当前最新版本号"""
    res = requests.get(
        "https://ddragon.leagueoflegends.com/api/versions.json", timeout=10
    )
    return res.json()[0]


def get_all_champions(version):
    """
    返回格式:
    [{"id": "Jinx", "key": "222", "name": "Jinx", "nameCn": "金克丝"}, ...]
    """
    res_en = requests.get(
        f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json",
        timeout=10,
    )
    res_cn = requests.get(
        f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/champion.json",
        timeout=10,
    )
    data_en = res_en.json()["data"]
    data_cn = res_cn.json()["data"]

    champions = []
    for champ_id, info in data_en.items():
        cn_name = data_cn.get(champ_id, {}).get("name", "")
        champions.append({
            "id": champ_id,
            "key": info["key"],
            "name": info["name"],
            "nameCn": cn_name,
        })
    return champions

