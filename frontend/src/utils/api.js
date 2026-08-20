function runtimeDefaultApi() {
  const configured = String(import.meta.env.VITE_API_BASE_URL || '').trim()
  if (configured) return configured.replace(/\/$/, '')

  // #ifdef H5
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin
  }
  // #endif

  return 'http://127.0.0.1:8000'
}

export function getApiBase() {
  return runtimeDefaultApi().replace(/\/$/, '')
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

export function upload(path, filePath, name = 'file', onProgress = null) {
  return new Promise((resolve, reject) => {
    const task = uni.uploadFile({
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
    if (onProgress && task?.onProgressUpdate) {
      task.onProgressUpdate(event => onProgress(Number(event.progress || 0), event))
    }
  })
}
