const DEFAULT_API = 'http://127.0.0.1:8000'

export function getApiBase() {
  return (uni.getStorageSync('digital_factory_api') || DEFAULT_API).replace(/\/$/, '')
}
export function setApiBase(value) {
  const normalized = String(value || '').trim().replace(/\/$/, '') || DEFAULT_API
  uni.setStorageSync('digital_factory_api', normalized)
  return normalized
}

export function fileUrl(path) {
  return `${getApiBase()}${path}`
}

export function request(path, options = {}) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: `${getApiBase()}${path}`,
      method: options.method || 'GET',
      data: options.data,
      header: { 'Content-Type': 'application/json', ...(options.header || {}) },
      timeout: options.timeout || 30000,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) resolve(response.data)
        else reject(new Error(response.data?.detail || `请求失败：HTTP ${response.statusCode}`))
      },
      fail(error) { reject(new Error(error.errMsg || '无法连接后端服务')) }
    })
  })
}

export function upload(path, filePath, name = 'file') {
  return new Promise((resolve, reject) => {
    uni.uploadFile({
      url: `${getApiBase()}${path}`,
      filePath,
      name,
      timeout: 180000,
      success(response) {
        let data = response.data
        try { data = JSON.parse(data) } catch (_) {}
        if (response.statusCode >= 200 && response.statusCode < 300) resolve(data)
        else reject(new Error(data?.detail || `上传失败：HTTP ${response.statusCode}`))
      },
      fail(error) { reject(new Error(error.errMsg || '文件上传失败')) }
    })
  })
}
