const api = require('../../utils/api')
const { API_BASE } = require('../../utils/config')
const { findChampion, inputValue } = require('../../utils/champions')

Page({
  data: {
    apiBase: API_BASE,
    champions: null,
    roles: [
      { label: '推荐辅助', value: 'support' },
      { label: '推荐 ADC', value: 'adc' }
    ],
    role: 'support',
    roleLabel: '推荐辅助',
    allyAdText: '',
    allySupText: '',
    enemyAdText: '',
    enemySupText: '',
    stateInfo: {},
    results: []
  },

  async onLoad() {
    const champions = await api.getChampions()
    this.setData({ champions })
    const lucian = champions.bottom.find((item) => item.id === 'lucian')
    const jinx = champions.bottom.find((item) => item.id === 'jinx')
    const leona = champions.support.find((item) => item.id === 'leona')
    this.setData({
      allyAdText: inputValue(lucian),
      enemyAdText: inputValue(jinx),
      enemySupText: inputValue(leona)
    })
    this.submit()
  },

  onRoleChange(event) {
    const item = this.data.roles[event.detail.value]
    this.setData({ role: item.value, roleLabel: item.label })
    this.submit()
  },

  onInput(event) {
    const key = event.currentTarget.dataset.key
    const map = {
      ally_ad: 'allyAdText',
      ally_sup: 'allySupText',
      enemy_ad: 'enemyAdText',
      enemy_sup: 'enemySupText'
    }
    this.setData({ [map[key]]: event.detail.value })
  },

  resolve(text, lane) {
    const champion = findChampion(text, this.data.champions, lane)
    return champion ? champion.id : null
  },

  async submit() {
    if (!this.data.champions) return
    const payload = {
      role: this.data.role,
      top_n: 20,
      bp_state: {
        ally_ad: this.resolve(this.data.allyAdText, 'bottom'),
        ally_sup: this.resolve(this.data.allySupText, 'support'),
        enemy_ad: this.resolve(this.data.enemyAdText, 'bottom'),
        enemy_sup: this.resolve(this.data.enemySupText, 'support')
      }
    }
    const data = await api.recommend(payload)
    this.setData({
      stateInfo: data.state_info,
      results: data.results
    })
  }
})
