const { API_BASE } = require('./config')

function request(path, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}${path}`,
      method: options.method || 'GET',
      data: options.data || undefined,
      header: {
        'Content-Type': 'application/json'
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else {
          reject(new Error(`HTTP ${res.statusCode}`))
        }
      },
      fail: reject
    })
  })
}

function getChampions() {
  return request('/api/champions')
}

function recommend(payload) {
  return request('/api/recommend', {
    method: 'POST',
    data: payload
  })
}

function getSynergy(champion, topN = 30) {
  return request(`/api/synergy/${champion}?top_n=${topN}`)
}

function getCounter(champion, topN = 10) {
  return request(`/api/counter/${champion}?top_n=${topN}`)
}

function getTier(lane) {
  return request(`/api/tier/${lane}`)
}

module.exports = {
  getChampions,
  recommend,
  getSynergy,
  getCounter,
  getTier
}
