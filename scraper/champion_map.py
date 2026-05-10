"""
champion_map.py - 英雄名 <-> Riot数字ID 映射
数据来源: 本地 hero_id_mapping.py (无需网络)
"""

import os

# 导入本地映射表
from scraper.chinese_getchampion.hero_id_mapping import HERO_ID_MAPPING

_name_to_key = None
_key_to_name = None


def _load():
    global _name_to_key, _key_to_name
    _name_to_key = {k.lower(): v for k, v in HERO_ID_MAPPING.items()}
    _key_to_name = {v: k.lower() for k, v in HERO_ID_MAPPING.items()}
    print(f"[champion_map] 已加载 {len(_name_to_key)} 个英雄映射 (本地数据，无需网络)")


def get_champion_id(name: str) -> int:
    """通过英雄名获取数字ID (支持大小写和空格)"""
    if _name_to_key is None:
        _load()
    key = name.lower().replace(" ", "").replace("'", "").replace("-", "")
    return _name_to_key.get(key)


def get_champion_name(key: int) -> str:
    """通过数字ID获取英雄名"""
    if _key_to_name is None:
        _load()
    return _key_to_name.get(key)


# ✅ 兼容别名 (lolalytics.py 调用的是这些名字)
def get_id(name: str) -> int:
    return get_champion_id(name)


def get_name(key: int) -> str:
    return get_champion_name(key)


# 模块导入时自动加载
_load()

# ✅ 兼容别名
def get_id(name):
    return get_champion_id(name)

def get_key(name):
    return get_champion_id(name)

def get_name(key):
    return get_champion_name(key)

def get_all():
    """返回 {英雄名小写: 数字ID} 字典"""
    if _name_to_key is None:
        _load()
    return dict(_name_to_key)