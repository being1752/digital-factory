# digital_factory_web

数字人工厂的独立 uni-app Vue 3 前端。后端位于仓库根目录，默认访问
`http://127.0.0.1:8000`。

## 启动

1. 在后端目录运行 `run.ps1`；
2. 使用 HBuilderX 打开本目录；
3. 选择“运行 → 运行到浏览器 → Chrome”；
4. 页面顶部可修改后端 API 地址，并在“ComfyUI 全局配置”面板保存 ComfyUI URL。

ComfyUI URL 由后端持久化保存，新建任务和项目详情中不再单独设置；保存后的手动任务和队列任务统一使用该地址。

前端支持项目创建、素材上传、ComfyUI 检测、AI 导演、音频生成、本地 Whisper 对齐、
逐段动作修改、InfiniteTalk 视频生成和成品下载。
