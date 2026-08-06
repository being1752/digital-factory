# digital_factory_web

数字人工厂的独立 uni-app Vue 3 + Vite 前端。后端位于仓库根目录，默认访问
`http://127.0.0.1:8000`，不依赖 HBuilderX。

## 启动

在仓库根目录一键启动前后端：

```powershell
.\run.ps1
```

也可以单独启动前端：

```powershell
cd frontend
npm install
npm run dev:h5
```

前端默认地址为 `http://127.0.0.1:5173`。后端地址由同源代理或
`VITE_API_BASE_URL` 决定；页面顶部只保留“ComfyUI 全局配置”面板。

局域网其他设备可访问 `http://服务器IP:5173`。H5 默认使用同源 `/api`，由
Vite 在服务器内部转发到 `http://127.0.0.1:8000`，因此远程浏览器不会错误连接
访问者自己的 `127.0.0.1`，也无需为开发服务器放宽后端 CORS。

使用独立后端域名时，可以在启动或构建前配置：

```powershell
$env:VITE_API_BASE_URL='https://api.example.com'
npm run build:h5
```

## 构建

```powershell
npm run build:h5
```

生产文件生成到 `dist/build/h5/`，可以由 Nginx 或其他静态文件服务器部署。
生产服务器应把同域 `/api` 反向代理到 FastAPI；或者在构建时设置
`VITE_API_BASE_URL`。

ComfyUI URL 由后端持久化保存，新建任务和项目详情中不再单独设置；保存后的手动任务和队列任务统一使用该地址。

前端支持项目创建、素材上传、ComfyUI 检测、AI 导演、音频生成、本地 Whisper 对齐、
逐段动作修改、InfiniteTalk 视频生成和成品下载。
