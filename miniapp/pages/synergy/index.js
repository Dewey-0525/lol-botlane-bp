const api = require('../../utils/api')
const { API_BASE } = require('../../utils/config')
const { findChampion, inputValue } = require('../../utils/champions')

Page({
  data: {
    apiBase: API_BASE,
    champions: null,
    query: '',
    results: []
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
    const data = await api.getSynergy(champion.id, 30)
    this.setData({ query: inputValue(champion), results: data.results })
  }
})
