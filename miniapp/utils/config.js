// 小程序接口环境配置。
//
// dev:
//   微信开发者工具模拟器可先用 127.0.0.1。
//   真机调试时通常需要改成电脑局域网 IP，例如 http://192.168.1.10:8000。
//
// prod:
//   正式小程序必须使用 HTTPS 域名，并在微信小程序后台配置 request 合法域名。
const ENV = 'dev'

const API_BASES = {
  dev: 'http://127.0.0.1:8000',
  prod: 'https://your-domain.com'
}

const API_BASE = API_BASES[ENV]

module.exports = {
  ENV,
  API_BASE,
  API_BASES
}
