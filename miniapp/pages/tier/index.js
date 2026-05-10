const api = require('../../utils/api')
const { API_BASE } = require('../../utils/config')

Page({
  data: {
    apiBase: API_BASE,
    lanes: [
      { label: 'ADC', value: 'bottom' },
      { label: '辅助', value: 'support' }
    ],
    lane: 'bottom',
    laneLabel: 'ADC',
    results: []
  },

  onLoad() {
    this.loadTier()
  },

  onLaneChange(event) {
    const lane = this.data.lanes[event.detail.value]
    this.setData({ lane: lane.value, laneLabel: lane.label })
    this.loadTier()
  },

  async loadTier() {
    const data = await api.getTier(this.data.lane)
    this.setData({ results: data.results })
  }
})
