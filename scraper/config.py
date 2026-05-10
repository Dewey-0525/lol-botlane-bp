"""
config.py - 全局配置
"""

# ============ 爬取配置 ============

# 段位：翡翠以上
RANK_FILTER = "emerald_plus"

# 地区：韩服
REGION_FILTER = "kr"

# ============ 数据质量 ============

# 最低样本量（低于此值直接丢弃）
MIN_GAMES = 500

# 重试次数
MAX_RETRIES = 3

# 请求间隔（秒）
REQUEST_DELAY = 1.5

# ============ 权重 ============

WEIGHT_SYNERGY = 0.3
WEIGHT_COUNTER = 0.3
WEIGHT_TIER = 0.4

# ============ 输出 ============

OUTPUT_PATH = "data/botlane_dataset.json"

