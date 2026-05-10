const api = require('../../utils/api')
const { API_BASE } = require('../../utils/config')
const { findChampion, inputValue } = require('../../utils/champions')

Page({
  data: {
    apiBase: API_BASE,
    champions: null,
    query: '',
    adc: [],
    support: []
  },

  async onLoad() {
    const champions = await api.getChampions()
    const jinx = champions.bottom.find((item) => item.id === 'jinx')
    this.setData({ champions, query: inputValue(jinx) })
    this.submit()
  },

  onInput(event) {
    this.setData({ query: event.detail.value })
  },

  async submit() {
    const champion = findChampion(this.data.query, this.data.champions, 'all')
    if (!champion) return
    const data = await api.getCounter(champion.id, 10)
    this.setData({
      query: inputValue(champion),
      adc: data.adc,
      support: data.support
    })
  }
})
