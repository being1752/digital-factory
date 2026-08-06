<template>
  <view class="page">
    <view class="aurora one"></view><view class="aurora two"></view>
    <view class="topbar">
      <view class="brand"><text class="mark">DF</text><view><text class="brand-title">数字人工厂</text><text class="brand-sub">AI DIRECTED · COMFYUI POWERED</text></view></view>
      <view class="top-actions"><text class="badge">{{ healthText }}</text><button class="ghost small" @click="newProject">新建项目</button></view>
    </view>

    <view class="api-bar panel">
      <view><text class="label-title">后端 API</text><text class="hint">前后端已分离，默认连接本机 8000 端口</text></view>
      <input v-model="backendUrl" class="field" type="text" :maxlength="-1" placeholder="http://127.0.0.1:8000" />
      <button class="ghost small" @click="saveBackend">连接</button>
    </view>

    <view class="shell">
      <view class="sidebar panel">
        <view class="section-head"><text>任务队列</text><text class="refresh" @click="loadTasks">↻</text></view>
        <view v-if="!tasks.length" class="empty queue-empty">队列为空</view>
        <view v-for="task in tasks" :key="task.id" class="queue-item" :class="`queue-${taskDisplayClass(task)}`" @click="openProject(task.project_id)">
          <view class="queue-main"><text class="project-name">{{ task.project_title }}</text><text class="project-meta">{{ statusName(task.display_status) }} · {{ taskStatusContext(task) }} · {{ Number(task.display_progress || 0) }}%</text><text class="project-meta">{{ ttsEngineName(task.snapshot?.tts_engine) }}</text><text v-if="task.status_source==='project_after_terminal_task'" class="queue-history-note">上次自动任务{{ taskStatusName(task.status) }}，当前展示项目后续操作的实时状态</text><text v-if="task.stage==='ANALYSIS_RETRYING'&&task.error" class="queue-retry-error">第 {{ task.error.attempt }} 次失败，3 秒后重试：{{ task.error.message }}</text><text v-if="task.queue_position" class="queue-position">等待第 {{ task.queue_position }} 位</text></view>
          <button v-if="task.status==='QUEUED'||(task.status==='RUNNING'&&(task.stage==='ANALYZING'||task.stage==='ANALYSIS_RETRYING'))" class="queue-action" @click.stop="cancelTask(task)">取消</button>
          <button v-if="canRetryTask(task)" class="queue-action retry" @click.stop="retryTask(task)">重试</button>
          <button v-if="['FAILED','CANCELLED','COMPLETED'].includes(task.status)" class="queue-action delete" @click.stop="deleteTask(task)">删除</button>
        </view>
        <view class="section-head project-section-head"><text>最近项目</text><text class="refresh" @click="loadProjects">↻</text></view>
        <view v-if="!projects.length" class="empty">还没有项目</view>
        <view v-for="item in projects" :key="item.id" class="project-item" :class="{active: current?.id === item.id}" @click="openProject(item.id)">
          <view class="project-info"><text class="project-name">{{ item.title }}</text><text class="project-meta">{{ statusName(item.status) }} · {{ item.id }}</text></view>
          <button class="project-delete" @click.stop="deleteProject(item)">删除</button>
        </view>
      </view>

      <view class="workspace">
        <view v-if="!current" class="panel create-card">
          <text class="eyebrow">PHASE TWO</text><text class="hero">从一张照片，到完整口播视频</text>
          <text class="lead">本地 Whisper 精确对齐，AI 设计每 4 秒连续动作，ComfyUI 负责声音与视频生成。</text>
          <view class="form-grid two"><view><text class="label">项目名称</text><input v-model="form.title" class="field" type="text" :maxlength="-1" /></view><view><text class="label">ComfyUI URL</text><view class="inline"><textarea :value="form.comfy_url" class="field flex url-field" :maxlength="-1" auto-height placeholder="http://127.0.0.1:8188" @input="setComfyUrl('form', $event)"></textarea><button class="ghost small" @tap.stop="pasteComfyUrl('form')">粘贴</button><button class="ghost small" :disabled="comfyChecking" @tap.stop="checkComfy(form.comfy_url,form.tts_engine)">{{ comfyChecking ? '检测中' : '检测' }}</button></view><view v-if="comfyCheckResult" class="check-message" :class="{success:comfyCheckOk,error:!comfyCheckOk&&!comfyChecking}">{{ comfyCheckResult }}</view></view></view>
          <view><text class="label">音频生成流程</text><view class="engine-options"><button class="engine-option" :class="{active:!form.tts_engine||form.tts_engine==='indextts2_legacy'}" @click="form.tts_engine='indextts2_legacy'"><text>原版 IndexTTS2</text><text class="hint">使用 AI 情绪向量</text></button><button class="engine-option" :class="{active:form.tts_engine==='indextts2_voice_clone'}" @click="form.tts_engine='indextts2_voice_clone'"><text>新版 IndexTTS2</text><text class="hint">音色参考＋情感参考双音频</text></button></view></view>
          <checkbox-group class="auto-run-option" @change="setAutoRun"><label><checkbox value="auto" :checked="form.auto_run" color="#d7ff68" /><view><text class="auto-run-title">全自动一条龙执行</text><text class="hint">勾选后进入任务队列；AI 导演失败每 3 秒重试，成功后自动生成音频、对齐并生成视频</text></view></label></checkbox-group>
          <view><text class="label">原始口播文案</text><textarea v-model="form.original_script" class="textarea script-input" :maxlength="-1"></textarea></view>
          <view class="form-grid three"><view><text class="label">视频用途</text><input v-model="form.purpose" class="field" type="text" :maxlength="-1"/></view><view><text class="label">目标受众</text><input v-model="form.audience" class="field" type="text" :maxlength="-1"/></view><view><text class="label">期望风格</text><input v-model="form.requested_style" class="field" type="text" :maxlength="-1"/></view></view>
          <view class="uploads">
            <view class="upload-box image-upload" :class="{selected:Boolean(imagePath)}" @click="chooseImage">
              <image v-if="imagePath" class="upload-preview" :src="imagePath" mode="aspectFill" />
              <view class="upload-copy"><text>数字人图片</text><text v-if="imagePath" class="upload-status">✓ 已选择</text><text class="hint file-name">{{ imageFileName || '点击选择；不选使用默认图片' }}</text></view>
            </view>
            <view class="upload-box" :class="{selected:Boolean(voicePath)}" @click="chooseVoice">
              <text class="upload-icon">♪</text><text>音色参考音频</text><text v-if="voicePath" class="upload-status">✓ 已选择</text><text class="hint file-name">{{ voiceFileName || '点击选择；不选使用默认音色' }}</text>
            </view>
            <view v-if="form.tts_engine==='indextts2_voice_clone'" class="upload-box emotion-reference" :class="{selected:Boolean(emotionVoicePath)}" @click="chooseEmotionVoice">
              <text class="upload-icon">♫</text><text>情感参考音频</text><text v-if="emotionVoicePath" class="upload-status">✓ 已选择</text><text class="hint file-name">{{ emotionVoiceFileName || '新版必选；用于复制语气和情感' }}</text>
            </view>
          </view>
          <button class="primary wide" :disabled="submitting" @click="createProject">{{ submitting ? '正在提交…' : (form.auto_run ? '创建并加入任务队列 →' : '创建项目并手动操作 →') }}</button>
        </view>

        <template v-else>
          <view class="project-head panel"><view><text class="eyebrow">PRODUCTION</text><text class="project-title">{{ current.title }}</text><text class="hint">{{ current.id }}</text></view><view class="status-box"><text>{{ statusName(current.status) }}</text><view class="progress"><view :style="{width: `${current.progress || 0}%`}"></view></view><text class="hint">{{ durationSummary }}</text></view></view>

          <view class="panel content-card director-plan-card">
            <view class="section-head"><view><text class="plan-title">AI 导演方案</text><text class="hint inline-hint">{{ directorModeText }}</text></view><button v-if="current.script" class="ghost small" @click="saveDirector">保存修改</button></view>
            <view v-if="!current.script" class="director-empty"><text class="empty-title">导演方案尚未生成</text><text class="hint">点击开始分析后，这里会显示看图结论、口播稿、声音情绪和动作边界。</text><button class="primary small" :disabled="isBusy" @click="run('analyze')">{{ isBusy ? '正在分析…' : '开始 AI 导演分析' }}</button></view>
            <template v-else>
              <view class="script-source-lock"><text class="analysis-label">口播内容来源</text><text>已锁定为你提供的原始口播稿；AI只设计语气、情绪和动作，不改写内容。</text></view>
              <view class="director"><image class="portrait" :src="assetUrl('image')" mode="aspectFill"/><view class="director-main">
                <view class="analysis-grid">
                  <view v-for="item in analysisDetails" :key="item.label" class="analysis-item" :class="{wide:item.wide}"><text class="analysis-label">{{ item.label }}</text><text class="analysis-value">{{ item.value || '—' }}</text></view>
                </view>
                <view class="action-columns"><view class="action-box safe"><text class="analysis-label">适合动作</text><text>{{ actionText('safe_actions') }}</text></view><view class="action-box avoid"><text class="analysis-label">避免动作</text><text>{{ actionText('avoid_actions') }}</text></view></view>
                <view class="voice-plan"><text class="analysis-label">声音建议</text><text>语速 {{ voicePlan.pace || '—' }} · 能量 {{ formatRatio(voicePlan.energy) }} · 温暖度 {{ formatRatio(voicePlan.warmth) }}</text></view>
              </view></view>
              <text class="label">最终口播稿</text><textarea v-model="current.script" class="textarea editor" :maxlength="-1"></textarea>
              <view v-if="current.tts_engine!=='indextts2_voice_clone'"><text class="subheading">声音情绪</text><view class="emotion-grid"><view v-for="name in emotionNames" :key="name" class="emotion"><view class="emotion-head"><text>{{ name }}</text><text>{{ Number(current.emotion?.[name] || 0).toFixed(2) }}</text></view><slider :value="Number(current.emotion?.[name] || 0) * 100" min="0" max="100" activeColor="#d7ff68" block-size="14" @changing="setEmotion(name, $event.detail.value)"/></view></view></view>
              <view v-if="current.segments?.length" class="segment-summary"><text class="subheading">每 4 秒动作方案</text><view class="summary-list"><view v-for="segment in current.segments" :key="`summary-${segment.index}`" class="summary-item"><text class="summary-time">{{ formatTime(segment.start) }}–{{ formatTime(segment.end) }}</text><text class="summary-action">{{ segment.action_prompt }}</text></view></view></view>
            </template>
          </view>

          <view class="service panel"><view><text class="label-title">ComfyUI 服务</text><text class="hint">使用完整 URL，不自动追加端口</text></view><textarea :value="current.comfy_url" class="field url-field" :maxlength="-1" auto-height placeholder="http://127.0.0.1:8188" @input="setComfyUrl('current', $event)"></textarea><button class="ghost small" @tap.stop="pasteComfyUrl('current')">粘贴</button><button class="ghost small" :disabled="comfyChecking" @tap.stop="checkComfy(current.comfy_url,current.tts_engine)">{{ comfyChecking ? '检测中' : '检测' }}</button><button class="primary small" @tap.stop="saveComfy">保存</button><view v-if="comfyCheckResult" class="check-message service-message" :class="{success:comfyCheckOk,error:!comfyChecking&&!comfyCheckOk}">{{ comfyCheckResult }}</view></view>

          <view class="steps"><view class="step panel"><text class="step-no">01</text><view><text class="step-title">AI 导演分析</text><text class="hint">构图、文案与声音情绪</text></view><button class="primary small" :disabled="isBusy" @click="run('analyze')">开始分析</button></view><view class="step panel"><text class="step-no">02</text><view><text class="step-title">声音与时间轴</text><text class="hint">IndexTTS2 + 本地 Whisper</text></view><button class="primary small" :disabled="isBusy || !current.script" @click="run('audio')">生成音频</button></view><view class="step panel"><text class="step-no">03</text><view><text class="step-title">InfiniteTalk 视频</text><text class="hint">动态火车节连续生成</text></view><button class="primary small" :disabled="isBusy || !current.has_audio || !current.segments?.length" @click="run('video')">生成视频</button></view></view>

          <view v-if="current.has_audio" class="panel content-card">
            <view class="section-head"><view><text>声音与表演时间轴</text><text class="hint inline-hint">{{ alignmentSummary }}</text></view><view class="actions"><button class="ghost small" @click="run('align')">重新对齐</button><button class="ghost small" @click="saveSegments">保存动作</button></view></view>
            <view class="audio-player">
              <button class="primary small play-button" @click="toggleAudio">{{ audioPlaying ? '暂停' : '播放音频' }}</button>
              <text class="audio-time">{{ formatTime(audioCurrent) }} / {{ formatTime(audioTotal || current.audio_duration || 0) }}</text>
              <slider class="audio-slider" :value="audioCurrent" :max="Math.max(1, audioTotal || Number(current.audio_duration || 0))" min="0" step="0.1" activeColor="#d7ff68" block-size="14" @change="seekAudio"/>
            </view>
            <view v-if="current.alignment?.audio_quality" class="quality-card" :class="`quality-${current.alignment.audio_quality.status}`">
              <view class="quality-head"><text class="subheading quality-title">音频中断检查</text><text class="quality-status">{{ current.alignment.audio_quality.status }}</text></view>
              <text class="quality-summary">{{ current.alignment.audio_quality.summary }}</text>
              <view v-for="(issue, issueIndex) in current.alignment.audio_quality.issues || []" :key="`issue-${issueIndex}`" class="quality-issue" @click="seekToTime(issue.time)">
                <text class="issue-time">{{ issue.time == null ? '全局' : formatTime(issue.time) }}</text>
                <text>{{ issue.reason }}</text>
              </view>
              <text v-if="current.alignment.audio_quality.limitation" class="hint quality-limit">{{ current.alignment.audio_quality.limitation }}</text>
            </view>
            <view v-if="current.alignment?.sentences?.length" class="sentence-card">
              <text class="subheading">逐句精确时间</text>
              <view v-for="sentence in current.alignment.sentences" :key="`sentence-${sentence.index}`" class="sentence-row" @click="seekToTime(sentence.start)">
                <text class="sentence-time">{{ formatTime(sentence.start) }}–{{ formatTime(sentence.end) }}</text>
                <text class="sentence-text">{{ sentence.text }}</text>
                <text class="hint">句前停顿 {{ Number(sentence.pause_before || 0).toFixed(2) }}s · {{ Number(sentence.chars_per_second || 0).toFixed(2) }} 字/秒</text>
              </view>
            </view>
            <view class="segments"><view v-for="segment in current.segments" :key="segment.index" class="segment"><view class="segment-time"><text>{{ formatTime(segment.start) }}–{{ formatTime(segment.end) }}</text><text class="hint">第 {{ segment.index + 1 }} 节</text><text v-if="segment.starts_mid_sentence" class="flow">↳ 承接上句</text><text v-if="segment.ends_mid_sentence" class="flow">下节继续 ↪</text></view><view class="segment-body"><text class="spoken">窗口口播：{{ segment.spoken_text }}</text><view v-for="event in segment.speech_events || []" :key="`${event.local_start}-${event.text}`" class="event"><text class="event-time">{{ Number(event.local_start).toFixed(1) }}–{{ Number(event.local_end).toFixed(1) }}s</text><text>{{ event.text }}</text><text class="hint full-sentence">完整语句：{{ event.full_sentence }}</text></view><textarea v-model="segment.action_prompt" class="textarea action-editor" :maxlength="-1"></textarea></view></view></view>
          </view>

          <view v-if="current.has_video" class="panel content-card"><view class="section-head"><text>成品视频</text><button class="ghost small" @click="downloadVideo">下载视频</button></view><video class="video" :src="assetUrl('video')" controls></video></view>
          <view v-if="current.error" class="error">{{ current.error.type }}：{{ current.error.message }}</view>
        </template>
      </view>
    </view>
  </view>
</template>

<script>
import { fileUrl, getApiBase, request, setApiBase, upload } from '../../utils/api.js'

const BUSY = new Set(['QUEUE_WAITING','ANALYZE_QUEUED','ANALYZING_IMAGE','ANALYSIS_RETRYING','AUDIO_QUEUED','UPLOADING_REFERENCE_AUDIO','GENERATING_AUDIO','ALIGN_QUEUED','ALIGNING_SPEECH','PLANNING_ACTIONS','VIDEO_QUEUED','UPLOADING_VIDEO_ASSETS','GENERATING_VIDEO'])
const STATUS = {CREATED:'已创建',QUEUE_WAITING:'等待队列执行',QUEUE_CANCELLED:'队列任务已取消',ANALYZE_QUEUED:'等待分析',ANALYZING_IMAGE:'AI 正在分析图片',ANALYSIS_RETRYING:'AI 导演失败，3 秒后重试',SCRIPT_READY:'导演方案已就绪',AUDIO_QUEUED:'等待生成音频',UPLOADING_REFERENCE_AUDIO:'正在上传参考音色',GENERATING_AUDIO:'IndexTTS2 正在生成',ALIGN_QUEUED:'等待语音对齐',ALIGNING_SPEECH:'Whisper 正在对齐',PLANNING_ACTIONS:'正在按时间轴设计动作',PLAN_READY:'音频与动作计划就绪',VIDEO_QUEUED:'等待生成视频',UPLOADING_VIDEO_ASSETS:'正在上传视频素材',GENERATING_VIDEO:'InfiniteTalk 正在生成',COMPLETED:'制作完成',ERROR:'生成失败'}
const TASK_STATUS = {QUEUED:'等待执行',RUNNING:'正在执行',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'已取消'}
const TASK_STAGE = {WAITING:'等待资源',RECOVERING:'恢复任务',STARTING:'准备开始',ANALYZING:'AI 导演分析',ANALYSIS_RETRYING:'AI 导演将在 3 秒后重试',GENERATING_AUDIO:'音频与对齐',GENERATING_VIDEO:'视频生成',COMPLETED:'生产完成',FAILED:'执行失败',CANCELLED:'已取消'}

export default {
  data() { return {backendUrl:getApiBase(),health:{},projects:[],tasks:[],current:null,poll:null,queuePoll:null,submitting:false,comfyChecking:false,comfyCheckResult:'',comfyCheckOk:false,imagePath:'',imageFileName:'',voicePath:'',voiceFileName:'',emotionVoicePath:'',emotionVoiceFileName:'',audioPlaying:false,audioCurrent:0,audioTotal:0,audioSource:'',emotionNames:['Happy','Angry','Sad','Fear','Hate','Low','Surprise','Neutral'],form:{title:'健康管理口播',comfy_url:'http://127.0.0.1:8188',original_script:'百万亿健康管理蓝海市场，机遇就在眼前。友福同享智能科技有限公司，专注一站式AI健康管理五年多。现面向全国招募社区健康服务中心项目合伙人。如果你对健康管理感兴趣，想低门槛撬动高价值、高利润项目，友福就是你的最佳选择。友福三大核心优势，帮合伙人轻松开拓市场。',purpose:'品牌招商口播',audience:'关注健康产业的创业者',requested_style:'专业、温和、可信赖',auto_run:false}} },
  computed: {
    isBusy(){return BUSY.has(this.current?.status)},
    healthText(){return this.health.asr_enabled?(this.health.vision_enabled?'AI 导演 + 精确对齐':'精确对齐已就绪'):(this.health.ok?'后端已连接':'后端未连接')},
    durationSummary(){return this.current?.audio_duration?`${Number(this.current.audio_duration).toFixed(2)} 秒 · ${this.current.segments?.length||0} 节`:''},
    alignmentSummary(){const a=this.current?.alignment||{};return `${this.durationSummary} · ${a.mode==='asr_forced'?`精确对齐 ${Math.round((a.confidence||0)*100)}%`:'估算对齐'}`},
    directorModeText(){if(!this.current?.script)return '等待生成';if(this.current.ai_mode==='model')return 'GLM 看图 + AI 导演';if(this.current.ai_mode==='text_model_with_local_image_fallback')return '本地看图兜底 + 文本导演';return '规则导演'},
    analysisDetails(){const a=this.current?.image_analysis||{};return [{label:'人物',value:a.character_description,wide:true},{label:'穿着与配饰',value:a.clothing_accessories,wide:true},{label:'姿势',value:a.pose_description,wide:true},{label:'背景与光线',value:a.background_lighting,wide:true},{label:'整体风格',value:a.overall_style,wide:true},{label:'可用动作空间',value:a.visible_motion_space,wide:true},{label:'镜头景别',value:a.shot_type},{label:'视觉风格',value:a.visual_style},{label:'基础表情',value:a.baseline_expression},{label:'人物气质',value:a.persona},{label:'动作幅度',value:this.formatRatio(a.motion_level)}]},
    voicePlan(){return this.current?.image_analysis?.voice_suggestion||{}}
  },
  onLoad(){this.emotionVoicePath='';this.emotionVoiceFileName='';this.connectBackend()},
  onUnload(){clearInterval(this.poll);clearInterval(this.queuePoll);this.resetAudio()},
  methods: {
    toast(title,icon='none'){uni.showToast({title,icon,duration:2600})},
    async connectBackend(){try{this.health=await request('/api/health');await Promise.all([this.loadProjects(),this.loadTasks()]);this.startQueuePolling()}catch(e){this.toast(e.message)}},
    async saveBackend(){this.backendUrl=setApiBase(this.backendUrl);this.current=null;await this.connectBackend();this.toast('后端地址已保存','success')},
    async loadProjects(){this.projects=await request('/api/projects')},
    async loadTasks(){this.tasks=await request('/api/tasks?limit=100')},
    async openProject(id){clearInterval(this.poll);this.resetAudio();this.current=await request(`/api/projects/${id}`);this.startPolling()},
    deleteProject(item){uni.showModal({title:'删除项目',content:`确定删除“${item.title}”吗？项目素材、生成音频和视频都会一并删除，无法恢复。`,confirmText:'删除',confirmColor:'#ff7583',success:async result=>{if(!result.confirm)return;try{await request(`/api/projects/${item.id}`,{method:'DELETE'});if(this.current?.id===item.id){clearInterval(this.poll);this.resetAudio();this.current=null}await this.loadProjects();this.toast('项目已删除','success')}catch(error){this.toast(error.message)}}})},
    newProject(){clearInterval(this.poll);this.resetAudio();this.current=null},
    statusName(value){return STATUS[value]||value},
    ttsEngineName(value){return value==='indextts2_voice_clone'?'新版 IndexTTS2 音色＋情感':'原版 IndexTTS2'},
    taskStatusName(value){return TASK_STATUS[value]||value},
    taskStageName(value){return TASK_STAGE[value]||value},
    taskStatusContext(task){return task.status_source==='project_after_terminal_task'?'项目当前状态':(task.status_source==='project'?'项目实时阶段':this.taskStageName(task.stage))},
    taskDisplayClass(task){const status=task.display_status||task.status;if(status==='ERROR')return 'failed';if(BUSY.has(status)||task.status==='RUNNING')return 'running';if(status==='COMPLETED')return 'completed';return String(task.status||'').toLowerCase()},
    canRetryTask(task){return (task.status==='FAILED'||task.status==='CANCELLED')&&!BUSY.has(task.display_status)},
    actionText(field){const values=this.current?.image_analysis?.[field];return Array.isArray(values)&&values.length?values.join('、'):'—'},
    formatRatio(value){const number=Number(value);return Number.isFinite(number)?`${Math.round(number*100)}%`:'—'},
    setComfyUrl(target,event){const value=event?.detail?.value??'';if(target==='current'&&this.current)this.current.comfy_url=value;else this.form.comfy_url=value},
    pasteComfyUrl(target){uni.getClipboardData({success:result=>{const value=String(result.data||'').trim();if(target==='current'&&this.current)this.current.comfy_url=value;else this.form.comfy_url=value;this.toast(value?'URL 已粘贴':'剪贴板为空',value?'success':'none')},fail:error=>this.toast(error.errMsg||'无法读取剪贴板')})},
    async checkComfy(url,ttsEngine='indextts2_legacy'){
      const value=String(url||'').trim()
      if(!value){this.comfyCheckOk=false;this.comfyCheckResult='请先填写或粘贴完整的 ComfyUI URL';return}
      this.comfyChecking=true;this.comfyCheckOk=false;this.comfyCheckResult=`正在通过后端 ${getApiBase()} 检测 ${value} …`
      try{
        const result=await request('/api/comfyui/check',{method:'POST',data:{url:value,tts_engine:ttsEngine||'indextts2_legacy'},timeout:45000})
        this.comfyCheckOk=Boolean(result.available)
        this.comfyCheckResult=result.available?`连接正常，发现 ${result.node_count} 个节点。`:`连接成功，但缺少 ${result.missing_nodes.length} 个节点：${result.missing_nodes.join('、')}`
      }catch(error){this.comfyCheckOk=false;this.comfyCheckResult=`检测失败：${error.message}`}
      finally{this.comfyChecking=false}
    },
    chooseImage(){uni.chooseImage({count:1,success:r=>{this.imagePath=r.tempFilePaths[0];this.imageFileName=r.tempFiles?.[0]?.name||'已选择图片'}})},
    chooseVoice(){uni.chooseFile({count:1,extension:['wav','flac','mp3','m4a','m4s','mp4','ogg'],success:r=>{this.voicePath=r.tempFilePaths[0];this.voiceFileName=r.tempFiles?.[0]?.name||'已选择音色'}})},
    chooseEmotionVoice(){uni.chooseFile({count:1,extension:['wav','flac','mp3','m4a','m4s','mp4','ogg'],success:r=>{this.emotionVoicePath=r.tempFilePaths[0];this.emotionVoiceFileName=r.tempFiles?.[0]?.name||'已选择情感音频'}})},
    setAutoRun(event){this.form.auto_run=(event?.detail?.value||[]).includes('auto')},
    async createProject(){if(!this.form.comfy_url||!this.form.original_script.trim()){this.toast('请填写 ComfyUI URL 和文案');return}const needsEmotion=this.form.tts_engine==='indextts2_voice_clone';if(needsEmotion&&!this.emotionVoicePath){this.toast('新版 IndexTTS2 请选择情感参考音频');return}const autoRun=Boolean(this.form.auto_run);this.submitting=true;try{const createPayload={...this.form,expect_image_upload:Boolean(this.imagePath),expect_voice_upload:Boolean(this.voicePath),expect_emotion_voice_upload:Boolean(this.emotionVoicePath)};let p=await request('/api/projects/default',{method:'POST',data:createPayload});if(this.imagePath)p=await upload(`/api/projects/${p.id}/assets/image`,this.imagePath);if(this.voicePath)p=await upload(`/api/projects/${p.id}/assets/voice`,this.voicePath);if(this.emotionVoicePath)p=await upload(`/api/projects/${p.id}/assets/emotion_voice`,this.emotionVoicePath);if(!p.has_image||!p.has_voice||(needsEmotion&&!p.has_emotion_voice))throw new Error('图片、音色或情感参考音频未上传成功');let task=null;if(autoRun){task=await request(`/api/projects/${p.id}/enqueue`,{method:'POST'});this.current=null}else{this.current=p}this.imagePath='';this.imageFileName='';this.voicePath='';this.voiceFileName='';this.emotionVoicePath='';this.emotionVoiceFileName='';await Promise.all([this.loadProjects(),this.loadTasks()]);if(autoRun)this.toast(task.queue_position?`已加入队列，第 ${task.queue_position} 位`:'任务已开始执行','success');else this.toast('项目已创建，请按步骤手动操作','success')}catch(e){this.toast(e.message)}finally{this.submitting=false}},
    async cancelTask(task){try{await request(`/api/tasks/${task.id}/cancel`,{method:'POST'});await this.loadTasks();this.toast('任务已取消','success')}catch(error){this.toast(error.message)}},
    async retryTask(task){try{await request(`/api/tasks/${task.id}/retry`,{method:'POST'});await this.loadTasks();this.toast('任务已重新排队','success')}catch(error){this.toast(error.message)}},
    deleteTask(task){uni.showModal({title:'删除队列记录',content:`确定删除“${task.project_title}”的这条${this.taskStatusName(task.status)}记录吗？项目和生成素材会保留。`,confirmText:'删除记录',confirmColor:'#ff7583',success:async result=>{if(!result.confirm)return;try{await request(`/api/tasks/${task.id}`,{method:'DELETE'});await this.loadTasks();this.toast('队列记录已删除','success')}catch(error){this.toast(error.message)}}})},
    startQueuePolling(){clearInterval(this.queuePoll);this.queuePoll=setInterval(async()=>{try{await Promise.all([this.loadTasks(),this.loadProjects()]);if(this.current?.id&&this.tasks.some(task=>task.project_id===this.current.id&&task.status==='RUNNING'))this.current=await request(`/api/projects/${this.current.id}`)}catch(_){}},2500)},
    async saveComfy(){try{this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{comfy_url:this.current.comfy_url}});this.toast('ComfyUI URL 已保存','success')}catch(e){this.toast(e.message)}},
    setEmotion(name,value){if(!this.current.emotion)this.current.emotion={};this.current.emotion[name]=value/100},
    async saveDirector(){this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{script:this.current.script,emotion:this.current.emotion}});this.toast('导演方案已保存','success')},
    async saveSegments(){this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{segments:this.current.segments}});this.toast('动作计划已保存','success')},
    async run(stage){try{if(stage==='audio')await this.saveDirector();if(stage==='video')await this.saveSegments();this.current=await request(`/api/projects/${this.current.id}/run/${stage}`,{method:'POST'});this.startPolling()}catch(e){this.toast(e.message)}},
    startPolling(){clearInterval(this.poll);if(!this.isBusy)return;this.poll=setInterval(async()=>{try{this.current=await request(`/api/projects/${this.current.id}`);if(!this.isBusy){clearInterval(this.poll);await this.loadProjects()}}catch(e){clearInterval(this.poll);this.toast(e.message)}},2500)},
    assetUrl(kind){return this.current?fileUrl(`/api/projects/${this.current.id}/files/${kind}?v=${encodeURIComponent(this.current.updated_at||'')}`):''},
    formatTime(value){const n=Number(value||0);return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(Math.floor(n%60)).padStart(2,'0')}`},
    prepareAudio(){
      const source=this.assetUrl('audio')
      if(this._audioContext&&this.audioSource===source)return this._audioContext
      this.resetAudio()
      const context=uni.createInnerAudioContext()
      this._audioContext=context
      this.audioSource=source
      context.src=source
      context.onPlay(()=>{this.audioPlaying=true})
      context.onPause(()=>{this.audioPlaying=false})
      context.onStop(()=>{this.audioPlaying=false})
      context.onEnded(()=>{this.audioPlaying=false;this.audioCurrent=0})
      context.onTimeUpdate(()=>{this.audioCurrent=Number(context.currentTime||0);this.audioTotal=Number(context.duration||this.current?.audio_duration||0)})
      context.onCanplay(()=>{setTimeout(()=>{this.audioTotal=Number(context.duration||this.current?.audio_duration||0)},200)})
      context.onError(error=>{this.audioPlaying=false;this.toast(error.errMsg||'音频播放失败')})
      return context
    },
    toggleAudio(){const context=this.prepareAudio();if(this.audioPlaying)context.pause();else context.play()},
    seekAudio(event){const context=this.prepareAudio();const position=Number(event.detail.value||0);context.seek(position);this.audioCurrent=position},
    seekToTime(value){if(value==null)return;const context=this.prepareAudio();const position=Math.max(0,Number(value||0));context.seek(position);this.audioCurrent=position;context.play()},
    resetAudio(){if(this._audioContext){this._audioContext.stop();this._audioContext.destroy();this._audioContext=null}this.audioPlaying=false;this.audioCurrent=0;this.audioTotal=0;this.audioSource=''},
    downloadVideo(){
      const url=this.assetUrl('video')
      // #ifdef H5
      window.open(url,'_blank')
      // #endif
      // #ifndef H5
      uni.downloadFile({url,success:r=>uni.saveFile({tempFilePath:r.tempFilePath})})
      // #endif
    }
  }
}
</script>

<style scoped>
.error{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere}
.page{min-height:100vh;background:#0b0d12;color:#f5f6f8;padding-bottom:50px;position:relative}.aurora{position:fixed;width:520px;height:520px;border-radius:50%;filter:blur(120px);opacity:.12;pointer-events:none}.aurora.one{background:#8b7cff;right:-220px;top:-240px}.aurora.two{background:#80ffd1;left:10%;bottom:-350px}.topbar{height:72px;padding:0 28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.08);background:rgba(11,13,18,.86);position:sticky;top:0;z-index:10}.brand,.top-actions,.inline,.section-head,.emotion-head,.actions{display:flex;align-items:center}.brand{gap:12px}.mark{width:38px;height:38px;line-height:38px;text-align:center;border-radius:11px;background:#d7ff68;color:#10120b;font-weight:900}.brand-title,.brand-sub,.label-title,.hint,.label,.project-name,.project-meta,.project-title,.step-title,.spoken,.flow,.event text{display:block}.brand-title{font-weight:800}.brand-sub{font-size:9px;color:#9399a7;letter-spacing:1.4px}.top-actions{gap:10px}.badge,.tag{font-size:11px;padding:6px 9px;border-radius:99px;background:rgba(139,124,255,.14);color:#c8c0ff}.panel{background:rgba(20,23,31,.86);border:1px solid rgba(255,255,255,.09);border-radius:18px;box-shadow:0 18px 55px rgba(0,0,0,.3)}.api-bar{max-width:1420px;margin:18px auto 0;padding:14px 18px;display:grid;grid-template-columns:240px 1fr auto;gap:12px;align-items:center}.shell{max-width:1420px;margin:18px auto;display:grid;grid-template-columns:230px minmax(0,1fr);gap:20px;padding:0 20px}.sidebar{padding:15px;min-height:500px}.section-head{justify-content:space-between;margin-bottom:16px}.refresh{cursor:pointer;font-size:20px}.empty,.hint,.project-meta{font-size:11px;color:#969ca9}.project-item{padding:12px;border-radius:12px;margin-bottom:7px;border:1px solid transparent}.project-item.active{background:rgba(255,255,255,.05);border-color:rgba(255,255,255,.08)}.project-name{font-size:13px;font-weight:700}.project-meta{margin-top:5px}.workspace{min-width:0}.create-card,.content-card{padding:36px}.eyebrow{display:block;color:#d7ff68;letter-spacing:2px;font-size:10px;font-weight:800}.hero{display:block;font-size:38px;font-weight:850;margin:9px 0}.lead{display:block;color:#969ca9;margin-bottom:30px}.form-grid{display:grid;gap:16px}.form-grid.two{grid-template-columns:1fr 1.5fr}.form-grid.three{grid-template-columns:repeat(3,1fr)}.label{font-size:12px;color:#bdc1ca;margin:14px 0 7px}.field,.textarea{box-sizing:border-box;width:100%;background:rgba(0,0,0,.22);border:1px solid rgba(255,255,255,.1);border-radius:11px;color:#f5f6f8;padding:11px 13px;font-size:14px}.textarea{line-height:1.7}.script-input{height:180px}.flex{flex:1}.inline{gap:8px}.uploads{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:17px 0}.upload-box{padding:16px;border:1px dashed rgba(255,255,255,.18);border-radius:13px}.upload-box .hint{margin-top:5px}.primary,.ghost{margin:0;border-radius:10px;font-weight:700;font-size:13px}.primary{background:#d7ff68;color:#10120b}.ghost{background:transparent;color:#f5f6f8;border:1px solid rgba(255,255,255,.12)}.small{padding:0 14px;height:38px;line-height:38px}.wide{width:100%;margin-top:8px}.project-head{padding:22px 26px;display:flex;justify-content:space-between}.project-title{font-size:23px;font-weight:800;margin:6px 0}.status-box{text-align:right;min-width:210px}.progress{height:5px;background:rgba(255,255,255,.08);border-radius:9px;margin:9px 0}.progress view{height:100%;background:linear-gradient(90deg,#8b7cff,#d7ff68)}.service{display:grid;grid-template-columns:170px 1fr auto auto;gap:9px;align-items:center;padding:15px 20px;margin-top:14px}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:14px 0}.step{display:grid;grid-template-columns:auto 1fr;gap:10px;padding:16px}.step button{grid-column:1/-1}.step-no{font-size:10px;color:#d7ff68}.step-title{font-size:14px;font-weight:750}.content-card{margin-top:14px;padding:24px}.inline-hint{display:inline;margin-left:10px}.director{display:grid;grid-template-columns:200px 1fr;gap:20px}.portrait{width:200px;height:320px;border-radius:14px}.tags{display:flex;flex-wrap:wrap;gap:6px}.editor{height:220px}.subheading{display:block;font-size:13px;font-weight:700;margin:20px 0 10px}.emotion-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.emotion{background:rgba(0,0,0,.18);border:1px solid rgba(255,255,255,.08);border-radius:11px;padding:9px}.emotion-head{justify-content:space-between;font-size:11px;color:#aeb3bd}.media,.video{width:100%;margin:5px 0 16px}.segments{display:flex;flex-direction:column;gap:9px}.segment{display:grid;grid-template-columns:95px 1fr;gap:12px;padding:13px;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(0,0,0,.16)}.segment-time{color:#d7ff68;font-size:11px}.segment-time .hint,.flow{margin-top:6px}.flow{font-size:9px;padding:3px 5px;border-radius:5px;background:rgba(215,255,104,.08)}.spoken{font-size:12px;color:#c4c7cf;margin-bottom:7px}.event{display:grid;grid-template-columns:60px 1fr;gap:4px 8px;padding:7px 9px;margin:5px 0;border-left:2px solid #8b7cff;background:rgba(139,124,255,.07)}.event-time{font-size:10px;color:#c8c0ff}.full-sentence{grid-column:2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.action-editor{height:75px;margin-top:5px}.error{margin-top:14px;padding:14px;border:1px solid rgba(255,117,131,.3);background:rgba(255,117,131,.1);color:#ff9ca7;border-radius:12px}
.aurora{z-index:0!important}.api-bar,.shell{position:relative;z-index:2}.field,.textarea{position:relative;z-index:3;pointer-events:auto!important;user-select:text;-webkit-user-select:text}.url-field{height:42px!important;min-height:42px!important;max-height:42px;line-height:20px;overflow:hidden;padding-top:10px;padding-bottom:10px}.service{grid-template-columns:170px minmax(0,1fr) auto auto auto}.check-message{margin-top:8px;padding:9px 11px;border-radius:8px;font-size:11px;color:#c4c8d0;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09);word-break:break-all}.check-message.success{color:#d7ff68;border-color:rgba(215,255,104,.25);background:rgba(215,255,104,.07)}.check-message.error{color:#ff9ca7;border-color:rgba(255,117,131,.28);background:rgba(255,117,131,.08)}.service-message{grid-column:2/-1;margin-top:0}.audio-player{display:grid;grid-template-columns:auto 100px 1fr;align-items:center;gap:10px;padding:12px;margin:5px 0 18px;border:1px solid rgba(255,255,255,.09);border-radius:12px;background:rgba(0,0,0,.18)}.play-button{margin:0}.audio-time{font-size:11px;color:#bdc1ca;text-align:center}.audio-slider{margin:0}
.quality-card,.sentence-card{padding:14px;margin:0 0 14px;border:1px solid rgba(255,255,255,.09);border-radius:12px;background:rgba(0,0,0,.16)}.quality-warning{border-color:rgba(255,117,131,.38);background:rgba(255,117,131,.07)}.quality-review{border-color:rgba(255,205,104,.3)}.quality-passed{border-color:rgba(215,255,104,.25)}.quality-head{display:flex;align-items:center;justify-content:space-between}.quality-title{margin:0}.quality-status{font-size:10px;text-transform:uppercase;color:#d7ff68}.quality-summary{display:block;margin:8px 0;font-size:12px}.quality-issue{display:grid;grid-template-columns:60px 1fr;gap:8px;padding:8px;margin-top:6px;border-radius:8px;background:rgba(255,255,255,.04);font-size:11px;cursor:pointer}.issue-time,.sentence-time{color:#d7ff68}.quality-limit{display:block;margin-top:9px}.sentence-row{display:grid;grid-template-columns:105px 1fr 230px;gap:10px;padding:9px 0;border-top:1px solid rgba(255,255,255,.07);cursor:pointer}.sentence-text{font-size:12px}
.director-plan-card{border-color:rgba(215,255,104,.2)}.plan-title{font-size:18px;font-weight:800}.director-empty{padding:25px;border:1px dashed rgba(255,255,255,.14);border-radius:13px;text-align:center}.director-empty .hint{margin:7px 0 15px}.empty-title{display:block;font-size:16px;font-weight:750}.director-main{min-width:0}.analysis-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.analysis-item,.voice-plan,.action-box{padding:11px;border:1px solid rgba(255,255,255,.08);border-radius:10px;background:rgba(0,0,0,.17)}.analysis-item.wide{grid-column:1/-1}.analysis-label{display:block;color:#969ca9;font-size:10px;margin-bottom:5px}.analysis-value{display:block;font-size:13px;line-height:1.65}.action-columns{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:9px}.action-box{font-size:12px;line-height:1.65}.action-box.safe{border-left:2px solid #d7ff68}.action-box.avoid{border-left:2px solid #ff7583}.voice-plan{margin-top:9px;font-size:12px}.segment-summary{margin-top:20px;padding-top:1px;border-top:1px solid rgba(255,255,255,.08)}.summary-list{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.summary-item{padding:10px;border-radius:10px;background:rgba(139,124,255,.07);border:1px solid rgba(139,124,255,.13)}.summary-time{display:block;color:#d7ff68;font-size:10px;margin-bottom:5px}.summary-action{display:block;font-size:11px;line-height:1.55}
@media(max-width:900px){.api-bar{margin:12px;grid-template-columns:1fr}.shell{grid-template-columns:1fr;padding:0 12px}.sidebar{display:none}.form-grid.two,.form-grid.three,.uploads,.steps,.service{grid-template-columns:1fr}.create-card{padding:22px}.hero{font-size:28px}.director{grid-template-columns:1fr}.portrait{width:100%;height:360px}.analysis-grid,.action-columns,.summary-list{grid-template-columns:1fr}.emotion-grid{grid-template-columns:repeat(2,1fr)}.audio-player{grid-template-columns:auto 1fr}.audio-slider{grid-column:1/-1}.topbar{padding:0 14px}.brand-sub{display:none}}
.script-source-lock{padding:11px 13px;margin-bottom:14px;border:1px solid rgba(215,255,104,.22);border-left:3px solid #d7ff68;border-radius:10px;background:rgba(215,255,104,.06);font-size:12px;line-height:1.65}
.project-item{padding:9px 9px 9px 12px;display:flex;align-items:center;gap:7px}.project-info{min-width:0;flex:1}.project-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.project-delete{flex:none;margin:0;padding:0 8px;height:28px;line-height:28px;border-radius:7px;background:rgba(255,117,131,.08);border:1px solid rgba(255,117,131,.2);color:#ff9ca7;font-size:10px}.project-delete:after{border:0}
.project-section-head{margin-top:20px}.queue-empty{margin-bottom:14px}.queue-item{display:flex;align-items:center;gap:7px;padding:9px;margin-bottom:7px;border:1px solid rgba(255,255,255,.08);border-radius:10px;background:rgba(0,0,0,.14);cursor:pointer}.queue-main{min-width:0;flex:1}.queue-position{display:block;margin-top:4px;font-size:10px;color:#d7ff68}.queue-running{border-color:rgba(215,255,104,.35);background:rgba(215,255,104,.06)}.queue-failed{border-color:rgba(255,117,131,.3)}.queue-action{flex:none;margin:0;padding:0 7px;height:27px;line-height:27px;border-radius:7px;background:transparent;border:1px solid rgba(255,255,255,.14);color:#bdc1ca;font-size:10px}.queue-action.retry{color:#d7ff68;border-color:rgba(215,255,104,.25)}.queue-action.delete{color:#ff9ca7;border-color:rgba(255,117,131,.3)}.queue-action:after{border:0}
.engine-options{display:grid;grid-template-columns:1fr 1fr;gap:10px}.engine-option{margin:0;padding:13px;text-align:left;height:auto;line-height:1.4;border-radius:11px;background:rgba(0,0,0,.18);color:#f5f6f8;border:1px solid rgba(255,255,255,.1)}.engine-option.active{border-color:#d7ff68;background:rgba(215,255,104,.08)}.engine-option .hint{margin-top:4px}.engine-option:after{border:0}
.uploads{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}.upload-box{position:relative;min-height:82px;box-sizing:border-box;cursor:pointer;transition:border-color .2s,background .2s}.upload-box.selected{border-style:solid;border-color:#d7ff68;background:rgba(215,255,104,.06)}.image-upload{display:flex;align-items:center;gap:13px}.upload-preview{width:72px;height:72px;flex:0 0 72px;border-radius:10px;background:#080a0e}.upload-copy{min-width:0;flex:1}.upload-status{display:inline-block;margin-top:6px;color:#d7ff68;font-size:11px;font-weight:750}.upload-icon{display:block;margin-bottom:8px;color:#d7ff68;font-size:22px;line-height:1}.file-name{word-break:break-all;overflow-wrap:anywhere}.upload-box.selected .file-name{color:#cbd0d8}
.auto-run-option{display:block;margin:16px 0 4px;padding:14px 16px;border:1px solid rgba(255,255,255,.1);border-radius:12px;background:rgba(0,0,0,.16)}.auto-run-option label{display:flex;align-items:flex-start;gap:10px}.auto-run-option checkbox{flex:none;margin-top:2px}.auto-run-title{display:block;font-size:13px;font-weight:750;margin-bottom:4px}.queue-retry-error{display:-webkit-box;margin-top:5px;color:#ffbf75;font-size:10px;line-height:1.35;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.queue-history-note{display:block;margin-top:5px;color:#8bd8ff;font-size:10px;line-height:1.35}
</style>
