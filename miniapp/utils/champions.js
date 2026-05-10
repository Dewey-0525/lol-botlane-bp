function normalize(value) {
  return String(value || '').trim().toLowerCase()
}

function inputValue(champion) {
  if (!champion) return ''
  return `${champion.cn_name} (${champion.id})`
}

function championPool(champions, lane) {
  if (!champions) return []
  if (lane === 'all') {
    const seen = {}
    return [...champions.bottom, ...champions.support].filter((champion) => {
      if (seen[champion.id]) return false
      seen[champion.id] = true
      return true
    })
  }
  return champions[lane] || []
}

function findChampion(rawValue, champions, lane) {
  const value = normalize(rawValue)
  if (!value) return null
  const pool = championPool(champions, lane)
  const bracketMatch = value.match(/\(([^)]+)\)/)
  if (bracketMatch) {
    const id = normalize(bracketMatch[1])
    return pool.find((champion) => champion.id === id) || null
  }
  return pool.find((champion) => {
    return champion.id === value ||
      normalize(champion.cn_name) === value ||
      normalize(champion.name) === value ||
      String(champion.champion_id) === value ||
      normalize(champion.search_text).includes(value)
  }) || null
}

module.exports = {
  inputValue,
  findChampion,
  championPool
}
