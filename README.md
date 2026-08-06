# 数字人工厂 · 第一、二期

FastAPI 后端服务，将 AI 导演、IndexTTS2 情绪语音和 InfiniteTalk 动态视频工作流串成完整制作流程。独立 uni-app 前端位于仓库的 `frontend/`。

## 已实现

- 使用用户填写的完整 ComfyUI URL，不追加端口；
- 检查 ComfyUI 节点依赖；
- 上传数字人图片和参考音色，也可直接使用目录中的默认素材；
- AI 图片分析、口播润色和情绪设计；
- 未配置 AI 时自动使用安全规则导演；
- 调用 IndexTTS2 情绪版生成音频；
- 读取音频真实时长；
- 每约 4 秒生成一段连续动作计划；
- 本地 OpenAI Whisper CLI 单词/分段时间戳；
- ASR 识别结果与原始口播稿字符级强制对齐；
- 识别一句话跨越多个 4 秒窗口，并向前后窗口传递完整语义和动作状态；
- 在窗口中途开句时，按局部秒数延迟动作，而不是在窗口开头提前动作；
- 动态创建任意数量的 InfiniteTalk 火车节；
- 在线编辑文案、情绪和每段动作；
- SQLite 任务记录、过程文件留档、音频试听和视频下载。
- 新建项目可选择手动模式或全自动模式；全自动任务进入 SQLite 持久化 FIFO 队列，AI 导演失败后每 3 秒持续重试，成功后自动完成音频、对齐和视频；重试期间可取消释放队列。
- 音频生成可按任务选择原版 IndexTTS2 情绪向量流程，或新版 IndexTTS2 音色＋情感双参考流程；两套 API 模板互不影响。

## 启动

当前环境已经具备主要依赖。如需完整安装：

```powershell
python -m pip install -r requirements.txt
```

复制环境配置：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`。文本导演与视觉模型可以使用不同的 OpenAI-compatible 接口。
DeepSeek V4 是纯文本模型，因此用 DeepSeek 时应保持 `AI_VISION_MODEL` 为空；
系统会使用本地图片元数据兜底，并继续用 DeepSeek 优化文案和设计分镜：

```env
AI_BASE_URL=https://api.deepseek.com
AI_API_KEY=your-deepseek-key
AI_TEXT_MODEL=deepseek-v4-flash
AI_VISION_MODEL=
```

当前项目已适配智谱 GLM-4.6V-Flash。它使用纯 Base64 图片输入并开启思考模式：

```env
AI_VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4
AI_VISION_API_KEY=your-zhipu-key
AI_VISION_MODEL=glm-4.6v-flash
```

文本与视觉服务使用不同地址时必须分别填写 Key，程序不会把 DeepSeek Key
自动发送给智谱。未填写有效的智谱 Key 时，视觉分析保持禁用并使用本地规则兜底。

语音对齐只使用本地 Whisper 命令行，不需要 ASR API 或 ComfyUI Whisper 工作流：

```env
WHISPER_EXECUTABLE=whisper
WHISPER_MODEL=large-v3-turbo
WHISPER_LANGUAGE=Chinese
WHISPER_WORD_TIMESTAMPS=true
WHISPER_MODEL_DIR=
WHISPER_DEVICE=
WHISPER_TIMEOUT_SECONDS=1800
```

如果启动应用的环境找不到 `whisper`，请把 `WHISPER_EXECUTABLE` 改成 `whisper.exe`
的绝对路径。程序固定生成 JSON 并优先读取词级时间戳；只有 segment 时会在段内插值。
命令不存在或执行失败时会降级为字符权重估算，页面会明确显示“估算对齐”。

如果 InfiniteTalk 在 KSampler 报错 `comfy_aimdo ... Fault failed`，这是 ComfyUI
Dynamic VRAM 的权重换入故障。请给 ComfyUI 启动参数增加
`--disable-dynamic-vram`，完全退出并重新启动 ComfyUI 后再生成视频。

启动：

```powershell
.\run.ps1
```

后端接口地址为 <http://127.0.0.1:8000>，API 文档为 <http://127.0.0.1:8000/docs>。

前端使用 HBuilderX 打开仓库内的 `frontend/`，选择“运行到浏览器”。前端页面顶部可以修改后端 API 地址。跨域来源通过以下配置控制：

```env
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080
```

## 推荐使用顺序

1. 输入 ComfyUI URL，点击“检测”；
2. 创建项目；
3. 运行 AI 导演分析并审核口播稿；
4. 生成和试听音频，检查精确语音时间轴与跨窗口标记，审核每 4 秒动作；
5. 生成最终视频。

生成文件位于 `data/jobs/<项目ID>/`。编译后的 TTS 和视频 API 工作流也会保留，便于排错。

## 服务器部署

后端部署结构如下：

```text
浏览器前端 -> FastAPI 后端 -> 文本/视觉 AI API
                         -> 远程 ComfyUI
                         -> 后端本机 Whisper CLI
                         -> SQLite + data/jobs 生成文件
```

后端本身不运行 IndexTTS2 或 InfiniteTalk 模型；ComfyUI 可以部署在另一台
Windows/Linux GPU 服务器上。后端服务器必须能够访问填写的 ComfyUI URL。

### 通用要求

- 推荐 Python 3.11；
- 安装 FFmpeg，并确保 `ffmpeg` 可从服务进程的 PATH 中找到；
- 执行 `pip install -r requirements.txt`；
- 如需精确时间轴，额外安装 `openai-whisper`；
- 使用 `DATA_DIR` 持久化 `jobs.db` 和 `jobs/`；
- 只启动 **一个 Uvicorn Worker**，不要使用 `--workers 2/4`，否则可能重复消费任务；
- `.env` 含 API Key，不要提交到代码仓库；
- 公开仓库不包含人物照片、参考音色和生成结果；创建项目时请上传自己的素材，
  或仅在服务器本地放置默认素材；
- 当前 API 没有用户认证，不建议直接裸露到公网，生产环境应放在 HTTPS
  反向代理、VPN或可信内网之后。

### Windows 部署

以下示例假设代码位于 `C:\apps\digital_factory`：

```powershell
cd C:\apps\digital_factory
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install openai-whisper
Copy-Item .env.example .env
```

服务器还需要安装 FFmpeg。安装后检查：

```powershell
ffmpeg -version
.\.venv\Scripts\whisper.exe --help
```

Windows `.env` 示例：

```env
AI_BASE_URL=https://api.deepseek.com
AI_API_KEY=your-text-model-key
AI_TEXT_MODEL=your-text-model

AI_VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4
AI_VISION_API_KEY=your-zhipu-key
AI_VISION_MODEL=glm-4.6v-flash

WHISPER_EXECUTABLE=C:\apps\digital_factory\.venv\Scripts\whisper.exe
WHISPER_MODEL=large-v3-turbo
WHISPER_LANGUAGE=Chinese
WHISPER_WORD_TIMESTAMPS=true
WHISPER_DEVICE=cuda
WHISPER_TIMEOUT_SECONDS=1800

DATA_DIR=C:\apps\digital_factory\data
COMFY_TIMEOUT_SECONDS=7200
DEFAULT_COMFY_URL=http://your-comfyui-host
FRONTEND_ORIGINS=https://your-frontend.example.com
```

没有 NVIDIA GPU 时可设置 `WHISPER_DEVICE=cpu`，但 `large-v3-turbo` 会明显变慢。
不需要精确对齐时可把 `WHISPER_EXECUTABLE` 留空，系统将使用估算时间轴。

生产启动命令：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

现有 `run.ps1` 适合本地启动；服务器部署建议明确指定 `.venv` 中的 Python。
需要长期后台运行时，可使用 NSSM、WinSW 或 Windows 任务计划程序。服务配置的
工作目录必须是项目根目录，程序为 `.venv\Scripts\python.exe`，参数为：

```text
-m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

若直接通过局域网访问 8000 端口，需要配置 Windows 防火墙。公网部署建议使用
IIS、Caddy 或 Nginx 反向代理并启用 HTTPS。

### Linux 部署（Ubuntu/Debian）

安装系统依赖：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg nginx
```

以下示例假设代码位于 `/opt/digital_factory`：

```bash
cd /opt/digital_factory
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip install openai-whisper
cp .env.example .env
```

Linux `.env` 示例：

```env
AI_BASE_URL=https://api.deepseek.com
AI_API_KEY=your-text-model-key
AI_TEXT_MODEL=your-text-model

AI_VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4
AI_VISION_API_KEY=your-zhipu-key
AI_VISION_MODEL=glm-4.6v-flash

WHISPER_EXECUTABLE=/opt/digital_factory/.venv/bin/whisper
WHISPER_MODEL=large-v3-turbo
WHISPER_LANGUAGE=Chinese
WHISPER_WORD_TIMESTAMPS=true
WHISPER_DEVICE=cpu
WHISPER_TIMEOUT_SECONDS=1800

DATA_DIR=/var/lib/digital_factory
COMFY_TIMEOUT_SECONDS=7200
DEFAULT_COMFY_URL=http://your-comfyui-host
FRONTEND_ORIGINS=https://your-frontend.example.com
```

创建独立服务账户和持久化目录后，确保该账户对代码目录和数据目录有读写权限。
systemd 服务 `/etc/systemd/system/digital-factory.service` 示例：

```ini
[Unit]
Description=Digital Factory Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=digitalfactory
Group=digitalfactory
WorkingDirectory=/opt/digital_factory
EnvironmentFile=/opt/digital_factory/.env
ExecStart=/opt/digital_factory/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

启用并查看日志：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now digital-factory
sudo systemctl status digital-factory
sudo journalctl -u digital-factory -f
```

Nginx 反向代理示例：

```nginx
server {
    listen 80;
    server_name api.example.com;

    client_max_body_size 150m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 7200s;
        proxy_read_timeout 7200s;
    }
}
```

应用配置后检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

正式公网环境应为域名配置 HTTPS。

### ComfyUI 部署在另一台服务器

例如后端为 `192.168.1.20:8000`，ComfyUI 为 `192.168.1.30:8188`：

```env
DEFAULT_COMFY_URL=http://192.168.1.30:8188
```

需要确认：

- 后端能访问 ComfyUI 的 `/prompt`、`/history`、`/view` 和 `/upload/image`；
- ComfyUI 不能只监听其本机 `127.0.0.1`；
- 防火墙仅允许后端服务器访问 ComfyUI；
- ComfyUI 已安装三个工作流需要的自定义节点和模型；
- InfiniteTalk 若遇到 AIMDO/Dynamic VRAM 错误，仍应使用
  `--disable-dynamic-vram` 启动 ComfyUI。

### 前端与跨域

前端页面顶部的“后端 API”填写正式地址，例如：

```text
https://api.example.com
```

后端 `.env` 同时加入实际前端来源（只写协议、域名和端口，不写路径）：

```env
FRONTEND_ORIGINS=https://web.example.com,http://192.168.1.50:8080
```

修改 `.env` 后需要重启后端。

### 数据迁移与备份

停止旧后端后，同时复制：

```text
data/jobs.db
data/jobs/
```

不能只复制数据库或只复制项目目录。不要复制旧服务器的 `.venv`、`Scripts`、
`pyvenv.cfg` 或 Python 缓存，新服务器应重新创建虚拟环境。

当前项目记录保存的是绝对文件路径，因此 Windows 到 Linux、Linux 到 Windows，或改变
部署目录时，历史项目路径不会自动转换。全新部署不受影响；迁移已有历史任务时需要
批量转换 `jobs.db` 中项目记录的 `project_dir`、图片、音频和视频路径。

### 部署验证

```text
GET /api/health
GET /docs
POST /api/comfyui/check
```

依次确认：后端健康、AI 配置、Whisper 路径、ComfyUI 节点检查、素材上传、音频生成、
时间轴对齐和视频结果下载。生成视频提交后会立即持久化 ComfyUI `prompt_id`；后端在
等待期间重启时，可以继续查询并下载已完成的视频。

## 测试

```powershell
python -m unittest discover -v
```

## 当前边界

- Whisper 词级时间戳仍是模型估计值；只有 segment 时，段内字符时间为插值结果；
- 本地 Whisper 与 ComfyUI 按顺序执行；IndexTTS2 生成音频后主动卸载，为本地 Whisper 和 InfiniteTalk 释放显存；
- SQLite 队列任务会在服务重启后重新排队；已保存 `video_prompt_id` 的视频任务会继续
  查询和下载，尚未持久化远端任务 ID 的其他手动阶段仍可能需要重新执行；
- ComfyUI 需要已经安装原工作流使用的自定义节点和模型；
- 当前视频模板按最长 5 分钟裁剪输入音频，定位为短视频生产。
