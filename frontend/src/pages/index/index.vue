<template>
  <view class="page">
    <view class="aurora one"></view><view class="aurora two"></view>
    <AppChrome :health="health" :health-text="healthText" :navigation="navigation" :view-mode="viewMode" :active-task-count="activeTaskCount" @navigate="navigateTo" />

    <SettingsPage v-if="viewMode==='settings'" :settings="appSettings" :health="health" :saving="settingsSaving" :checking="comfyChecking" :check-result="comfyCheckResult" :check-ok="comfyCheckOk" @paste="pasteComfyUrl" @check="checkComfy(appSettings.comfy_url,form.tts_engine)" @save="saveAppSettings" />

    <TaskQueuePage v-else-if="viewMode==='queue'" :tasks="tasks" :running-count="runningTaskCount" :queued-count="queuedTaskCount" :failed-count="failedTaskCount" :realtime-connected="realtimeConnected" :status-name="statusName" :task-stage-name="taskStageName" :tts-engine-name="ttsEngineName" :task-progress-text="taskProgressText" :task-display-class="taskDisplayClass" :can-retry-task="canRetryTask" @refresh="loadTasks" @create="navigateTo('create')" @open="openProject" @cancel="cancelTask" @retry="retryTask" @delete="deleteTask" />

    <view v-if="viewMode==='create'||viewMode==='projects'" class="shell" :class="{'create-shell':viewMode==='create'}">
      <ProjectBrowser v-if="viewMode==='projects'" sidebar :projects="projects" :current-id="current?.id||''" :status-name="statusName" @refresh="loadProjects" @open="openProject" @delete="deleteProject" />

      <view class="workspace">
        <ProjectBrowser v-if="viewMode==='projects'&&!current" :projects="projects" :status-name="statusName" @create="navigateTo('create')" @open="openProject" @delete="deleteProject" />
        <CreateTask v-if="viewMode==='create'" :form="form" :create-panels="createPanels" :font-options="fontOptions" :subtitle-colors="subtitleColors" :subtitle-positions="subtitlePositions" :script-summary="scriptSummary" :create-checks="createChecks" :create-ready="createReady" :image-path="imagePath" :image-file-name="imageFileName" :voice-path="voicePath" :voice-file-name="voiceFileName" :emotion-voice-path="emotionVoicePath" :emotion-voice-file-name="emotionVoiceFileName" :bgm-path="bgmPath" :bgm-file-name="bgmFileName" :submitting="submitting" :upload-label="uploadLabel" :upload-progress="uploadProgress" @set-title="setProjectTitle" @sync-script="syncCreateTitle" @choose-image="chooseImage" @choose-voice="chooseVoice" @choose-emotion-voice="chooseEmotionVoice" @choose-bgm="chooseBgm" @toggle-panel="toggleCreatePanel" @set-auto-run="setAutoRun" @set-bgm-enabled="setBgmEnabled" @set-bgm-ducking="setBgmDucking" @set-title-enabled="setVideoTitleEnabled($event,form)" @set-subtitle-enabled="setSubtitleEnabled" @set-subtitle-bold="setSubtitleBold($event,form)" @set-subtitle-background="setSubtitleBackground" @apply-title-preset="applyChannelsTitlePreset(form,form.original_script)" @apply-subtitle-preset="applyChannelsSubtitlePreset(form)" @submit="createProject" />


        <template v-if="viewMode==='projects'&&current">
          <view class="project-head panel"><view class="project-head-main"><button class="ghost small project-back" @click="backToProjectLibrary">← 返回项目库</button><view class="project-identity"><text class="eyebrow">PRODUCTION</text><input v-model="current.title" class="project-title-edit" maxlength="100" @blur="saveCurrentTitle" /><text class="hint">{{ current.id }}</text></view></view><view class="status-box"><text>{{ statusName(current.status) }}</text><view class="progress"><view :style="{width: `${current.progress || 0}%`}"></view></view><text class="hint">{{ durationSummary }}</text></view></view>

          <ProjectTabs :tabs="projectTabs" :value="projectTab" :has-video="current.has_video" @change="projectTab=$event" />
          <PersistentError :error="current.error" :status="statusName(current.status)" @copy="copyError" @queue="navigateTo('queue')" />

          <view v-if="projectTab==='overview'" class="panel content-card source-material-card">
            <view class="section-head"><view><text class="plan-title">任务原始素材</text><text class="hint inline-hint">提交后立即可查看</text></view><button v-if="current.can_edit_original_script" class="ghost small" @click="saveOriginalScript">保存口播稿</button></view>
            <view class="source-material">
              <image v-if="current.has_image" class="source-portrait" :src="assetUrl('image')" mode="aspectFill" />
              <view class="source-script"><text class="label">原始口播稿</text><textarea v-model="current.original_script" class="textarea source-script-editor" :disabled="!current.can_edit_original_script" :maxlength="-1"></textarea><text class="hint">{{ current.can_edit_original_script ? '音频开始生成前可以修改；保存后会重新执行 AI 导演。' : '音频已经开始生成，原始口播稿已锁定。' }}</text></view>
            </view>
          </view>

          <view v-if="projectTab==='director'" class="panel content-card director-plan-card">
            <view class="section-head"><view><text class="plan-title">AI 导演方案</text><text class="hint inline-hint">{{ directorModeText }}</text></view><button v-if="current.script&&!productionComplete" class="ghost small" @click="saveDirector">保存修改</button></view>
            <view v-if="!current.script" class="director-empty"><text class="empty-title">导演方案尚未生成</text><text class="hint">点击开始分析后，这里会显示看图结论、口播稿、声音情绪和动作边界。</text><button v-if="!productionComplete" class="primary small" :disabled="isBusy" @click="run('analyze')">{{ isBusy ? '正在分析…' : '开始智能导演分析' }}</button></view>
            <template v-else>
              <view class="script-source-lock"><text class="analysis-label">口播内容来源</text><text>已锁定为你提供的原始口播稿；AI只设计语气、情绪和动作，不改写内容。</text></view>
              <view class="director"><image class="portrait" :src="assetUrl('image')" mode="aspectFill"/><view class="director-main">
                <view class="analysis-grid">
                  <view v-for="item in analysisDetails" :key="item.label" class="analysis-item" :class="{wide:item.wide}"><text class="analysis-label">{{ item.label }}</text><text class="analysis-value">{{ item.value || '—' }}</text></view>
                </view>
                <view class="action-columns"><view class="action-box safe"><text class="analysis-label">适合动作</text><text>{{ actionText('safe_actions') }}</text></view><view class="action-box avoid"><text class="analysis-label">避免动作</text><text>{{ actionText('avoid_actions') }}</text></view></view>
                <view class="voice-plan"><text class="analysis-label">声音建议</text><text>语速 {{ voicePlan.pace || '—' }} · 能量 {{ formatRatio(voicePlan.energy) }} · 温暖度 {{ formatRatio(voicePlan.warmth) }}</text></view>
              </view></view>
              <text class="label">最终口播稿</text><textarea v-model="current.script" class="textarea editor" :disabled="productionComplete" :maxlength="-1"></textarea>
              <view v-if="current.tts_engine!=='indextts2_voice_clone'"><text class="subheading">声音情绪</text><view class="emotion-grid"><view v-for="name in emotionNames" :key="name" class="emotion"><view class="emotion-head"><text>{{ emotionNameLabel(name) }}</text><text>{{ Number(current.emotion?.[name] || 0).toFixed(2) }}</text></view><slider :disabled="productionComplete" :value="Number(current.emotion?.[name] || 0) * 100" min="0" max="100" activeColor="#d7ff68" block-size="14" @changing="setEmotion(name, $event.detail.value)"/></view></view></view>
              <view v-if="current.segments?.length" class="segment-summary"><text class="subheading">分段动作与表情方案</text><view class="summary-list"><view v-for="segment in current.segments" :key="`summary-${segment.index}`" class="summary-item"><text class="summary-time">{{ formatTime(segment.start) }}–{{ formatTime(segment.end) }}</text><text class="summary-action">{{ segment.action_prompt }}</text></view></view></view>
            </template>
          </view>

          <view v-if="projectTab==='overview'&&!productionComplete" class="steps"><view class="step panel"><text class="step-no">01</text><view><text class="step-title">智能导演分析</text><text class="hint">分析人物形象、口播内容和表达方式</text></view><button class="primary small" :disabled="isBusy" @click="run('analyze')">开始分析</button></view><view class="step panel"><text class="step-no">02</text><view><text class="step-title">生成配音</text><text class="hint">根据文案生成声音，并匹配每句话的时间</text></view><button class="primary small" :disabled="isBusy || !current.script" @click="run('audio')">生成音频</button></view><view class="step panel"><text class="step-no">03</text><view><text class="step-title">生成数字人视频</text><text class="hint">根据口播节奏和动作方案生成连贯画面</text></view><button class="primary small" :disabled="isBusy || !current.has_audio || !current.segments?.length" @click="run('video')">生成视频</button></view></view>

          <view v-if="current.has_audio&&projectTab==='audio'" class="panel content-card">
            <view class="section-head"><view><text>配音与动作节奏</text><text class="hint inline-hint">{{ alignmentSummary }}</text></view><view v-if="!productionComplete" class="actions"><button class="ghost small" @click="run('align')">重新匹配</button><button class="ghost small" @click="saveSegments">保存动作</button></view></view>
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
            <view class="segments"><view v-for="segment in current.segments" :key="segment.index" class="segment"><view class="segment-time"><text>{{ formatTime(segment.start) }}–{{ formatTime(segment.end) }}</text><text class="hint">第 {{ segment.index + 1 }} 段</text><text v-if="segment.starts_mid_sentence" class="flow">↳ 延续上一段</text><text v-if="segment.ends_mid_sentence" class="flow">下一段继续 ↪</text></view><view class="segment-body"><text class="spoken">本段口播：{{ segment.spoken_text }}</text><view v-for="event in segment.speech_events || []" :key="`${event.local_start}-${event.text}`" class="event"><text class="event-time">{{ Number(event.local_start).toFixed(1) }}–{{ Number(event.local_end).toFixed(1) }}s</text><text>{{ event.text }}</text><text class="hint full-sentence">完整语句：{{ event.full_sentence }}</text></view><textarea v-model="segment.action_prompt" class="textarea action-editor" :disabled="productionComplete" :maxlength="-1"></textarea></view></view></view>
          </view>

          <PostEditor v-if="current.has_video&&projectTab==='post'" :current="current" :video-source="videoSource" :is-busy="isBusy" :post-panels="postPanels" :font-options="fontOptions" :subtitle-colors="subtitleColors" :subtitle-positions="subtitlePositions" :image-url="assetUrl('image')" @download="downloadVideo" @load-video="loadVideo" @video-error="videoLoadError" @toggle-panel="togglePostPanel" @apply-title-preset="applyChannelsTitlePreset(current,current.script||current.original_script)" @set-title-enabled="setCurrentVideoTitleEnabled" @apply-subtitle-preset="applyChannelsSubtitlePreset(current)" @set-subtitle-enabled="setCurrentSubtitleEnabled" @set-subtitle-bold="setSubtitleBold($event,current)" @set-subtitle-background="setCurrentSubtitleBackground" @save-subtitle="saveSubtitleAndRun" @choose-bgm="chooseProjectBgm" @set-bgm-ducking="setCurrentBgmDucking" @save-bgm="saveBgmAndRun" />
          <view v-if="current.error" class="error">{{ current.error.type }}：{{ current.error.message }}</view>
        </template>
      </view>
    </view>
  </view>
</template>

<script>
import { fileUrl, getApiBase, request, upload } from '../../utils/api.js'
import AppChrome from '../../components/AppChrome.vue'
import SettingsPage from '../../components/SettingsPage.vue'
import TaskQueuePage from '../../components/TaskQueuePage.vue'
import ProjectBrowser from '../../components/ProjectBrowser.vue'
import ProjectTabs from '../../components/ProjectTabs.vue'
import PersistentError from '../../components/PersistentError.vue'
import PostEditor from '../../components/PostEditor.vue'
import CreateTask from '../../components/CreateTask.vue'

const BUSY = new Set(['QUEUE_WAITING','UPLOADING_ASSETS','ANALYZE_QUEUED','ANALYZING_IMAGE','ANALYSIS_RETRYING','AUDIO_QUEUED','UPLOADING_REFERENCE_AUDIO','GENERATING_AUDIO','ALIGN_QUEUED','ALIGNING_SPEECH','PLANNING_ACTIONS','VIDEO_QUEUED','UPLOADING_VIDEO_ASSETS','GENERATING_VIDEO','BGM_QUEUED','MIXING_BGM'])
const STATUS = {CREATED:'已创建',QUEUE_WAITING:'等待队列执行',UPLOADING_ASSETS:'正在上传任务素材',QUEUE_CANCELLED:'队列任务已取消',ANALYZE_QUEUED:'等待分析',ANALYZING_IMAGE:'正在分析人物图片',ANALYSIS_RETRYING:'分析失败，正在自动重试',SCRIPT_READY:'导演方案已就绪',AUDIO_QUEUED:'等待生成配音',UPLOADING_REFERENCE_AUDIO:'正在上传参考声音',GENERATING_AUDIO:'正在生成配音',ALIGN_QUEUED:'等待匹配口播时间',ALIGNING_SPEECH:'正在匹配口播时间',PLANNING_ACTIONS:'正在设计动作和表情',PLAN_READY:'配音与动作方案已就绪',VIDEO_QUEUED:'等待生成视频',UPLOADING_VIDEO_ASSETS:'正在准备视频素材',GENERATING_VIDEO:'正在生成数字人视频',VIDEO_READY:'无配乐视频已就绪',BGM_QUEUED:'等待添加配乐',MIXING_BGM:'正在添加背景音乐',BGM_ERROR:'配乐失败，原视频可用',COMPLETED:'制作完成',ERROR:'生成失败'}
const TASK_STATUS = {QUEUED:'等待执行',RUNNING:'正在执行',COMPLETED:'已完成',FAILED:'失败',CANCELLED:'已取消'}
const TASK_STAGE = {WAITING:'等待资源',UPLOADING_ASSETS:'上传任务素材',RECOVERING:'恢复任务',STARTING:'准备开始',ANALYZING:'智能导演分析',ANALYSIS_RETRYING:'正在自动重新分析',GENERATING_AUDIO:'生成配音',GENERATING_VIDEO:'视频生成',COMPLETED:'生产完成',FAILED:'执行失败',CANCELLED:'已取消'}
for(const value of ['SUBTITLE_QUEUED','BURNING_SUBTITLES'])BUSY.add(value)
Object.assign(STATUS,{VIDEO_READY:'原始视频已就绪',SUBTITLE_QUEUED:'等待添加标题和字幕',BURNING_SUBTITLES:'正在添加标题和字幕',SUBTITLE_READY:'标题/字幕版已就绪',BGM_ERROR:'配乐失败，前一版本可用'})
Object.assign(TASK_STAGE,{BURNING_SUBTITLES:'添加标题和字幕',MIXING_BGM:'添加背景音乐'})

export default {
  components:{AppChrome,SettingsPage,TaskQueuePage,ProjectBrowser,ProjectTabs,PersistentError,PostEditor,CreateTask},
  data() { return {viewMode:'create',projectTab:'overview',pageVisible:true,realtimeConnected:false,uploadProgress:0,uploadLabel:'',health:{},appSettings:{comfy_url:''},settingsSaving:false,availableFonts:[],projects:[],tasks:[],current:null,poll:null,queuePoll:null,submitting:false,comfyChecking:false,comfyCheckResult:'',comfyCheckOk:false,imagePath:'',imageFileName:'',voicePath:'',voiceFileName:'',emotionVoicePath:'',emotionVoiceFileName:'',bgmPath:'',bgmFileName:'',audioPlaying:false,audioCurrent:0,audioTotal:0,audioSource:'',videoSource:'',lastSuggestedVideoTitle:'',createPanels:{title:false,subtitle:false,bgm:false},postPanels:{title:false,subtitle:false},emotionNames:['Happy','Angry','Sad','Fear','Hate','Low','Surprise','Neutral'],form:{title:'健康管理口播',tts_engine:'indextts2_legacy',original_script:'百万亿健康管理蓝海市场，机遇就在眼前。友福同享智能科技有限公司，专注一站式AI健康管理五年多。现面向全国招募社区健康服务中心项目合伙人。如果你对健康管理感兴趣，想低门槛撬动高价值、高利润项目，友福就是你的最佳选择。友福三大核心优势，帮合伙人轻松开拓市场。',auto_run:true,bgm_enabled:false,bgm_volume:0.25,bgm_ducking:true,bgm_fade_in:1.5,bgm_fade_out:2}} },
  computed: {
    navigation(){return [{label:'新建任务',value:'create',icon:'＋'},{label:'任务队列',value:'queue',icon:'↻'},{label:'项目库',value:'projects',icon:'▦'},{label:'系统设置',value:'settings',icon:'⚙'}]},
    projectTabs(){return [{label:'概览',value:'overview'},{label:'导演方案',value:'director'},{label:'音频与动作',value:'audio'},{label:'成片后期',value:'post'}]},
    activeTaskCount(){return this.tasks.filter(task=>task.status==='QUEUED'||task.status==='RUNNING').length},
    runningTaskCount(){return this.tasks.filter(task=>task.status==='RUNNING').length},
    queuedTaskCount(){return this.tasks.filter(task=>task.status==='QUEUED').length},
    failedTaskCount(){return this.tasks.filter(task=>task.status==='FAILED').length},
    fontOptions(){return this.availableFonts.length?this.availableFonts:['Microsoft YaHei','SimHei','Arial','Noto Sans CJK SC']},
    subtitleColors(){return ['#FFFFFF','#FFD84D','#D7FF68','#67E8F9','#FF8FB3']},
    subtitlePositions(){return [{label:'顶部',value:'top'},{label:'居中',value:'center'},{label:'底部',value:'bottom'},{label:'自定义',value:'custom'}]},
    isBusy(){return BUSY.has(this.current?.status)},
    productionComplete(){return Boolean(this.current?.has_video)},
    scriptSummary(){const count=String(this.form.original_script||'').replace(/\s+/g,'').length;return count?`${count} 字 · 预计 ${Math.max(1,Math.round(count/4))} 秒`:'待填写口播稿'},
    createChecks(){const clone=this.form.tts_engine==='indextts2_voice_clone',checks=[{label:'口播文案',ok:Boolean(String(this.form.original_script||'').trim()),required:true,detail:String(this.form.original_script||'').trim()?'已填写':'必填'},{label:'数字人图片',ok:Boolean(this.imagePath),required:false,detail:this.imagePath?(this.imageFileName||'已选择'):'未选择时使用默认图片'},{label:'音色参考音频',ok:Boolean(this.voicePath),required:false,detail:this.voicePath?(this.voiceFileName||'已选择'):'未选择时使用默认音色'}];if(clone)checks.push({label:'情感参考音频',ok:Boolean(this.emotionVoicePath),required:true,detail:this.emotionVoicePath?(this.emotionVoiceFileName||'已选择'):'当前配音方式需要此文件'});if(this.form.bgm_enabled)checks.push({label:'背景音乐',ok:Boolean(this.bgmPath),required:true,detail:this.bgmPath?(this.bgmFileName||'已选择'):'已开启配乐，请选择文件'});return checks},
    createReady(){return this.createChecks.every(item=>!item.required||item.ok)},
    healthText(){return this.health.asr_enabled?(this.health.vision_enabled?'智能制作已就绪':'口播处理已就绪'):(this.health.ok?'服务已连接':'服务未连接')},
    durationSummary(){return this.current?.audio_duration?`${Number(this.current.audio_duration).toFixed(2)} 秒 · ${this.current.segments?.length||0} 段`:''},
    alignmentSummary(){const a=this.current?.alignment||{};return `${this.durationSummary} · ${a.mode==='asr_forced'?`口播匹配度 ${Math.round((a.confidence||0)*100)}%`:'时间为预估结果'}`},
    directorModeText(){return this.current?.script?'图片与内容分析完成':'等待生成'},
    analysisDetails(){const a=this.current?.image_analysis||{};return [{label:'人物',value:a.character_description,wide:true},{label:'穿着与配饰',value:a.clothing_accessories,wide:true},{label:'姿势',value:a.pose_description,wide:true},{label:'背景与光线',value:a.background_lighting,wide:true},{label:'整体风格',value:a.overall_style,wide:true},{label:'可用动作空间',value:a.visible_motion_space,wide:true},{label:'镜头景别',value:a.shot_type},{label:'视觉风格',value:a.visual_style},{label:'基础表情',value:a.baseline_expression},{label:'人物气质',value:a.persona},{label:'动作幅度',value:this.formatRatio(a.motion_level)}]},
    voicePlan(){return this.current?.image_analysis?.voice_suggestion||{}}
  },
  onLoad(){this.emotionVoicePath='';this.emotionVoiceFileName='';Object.assign(this.form,{subtitle_enabled:true,subtitle_font_name:'Microsoft YaHei',subtitle_font_size:64,subtitle_font_bold:true,subtitle_font_color:'#FFFFFF',subtitle_position:'custom',subtitle_custom_position:73,subtitle_stroke_color:'#000000',subtitle_stroke_width:3,subtitle_background_enabled:false,subtitle_background_color:'#000000',subtitle_background_opacity:40,subtitle_max_chars:14,video_title_enabled:true,video_title:'',video_title_font_name:'Microsoft YaHei',video_title_font_size:88,video_title_primary_color:'#FFFFFF',video_title_secondary_color:'#FFFFFF',video_title_position:10,video_title_stroke_color:'#000000',video_title_stroke_width:4});this.form.video_title=this.suggestVideoTitleText(this.form.original_script);this.lastSuggestedVideoTitle=this.form.video_title;this.setupVisibilityTracking();this.connectBackend()},
  onShow(){this.handlePageVisibility(true)},
  onHide(){this.handlePageVisibility(false)},
  onUnload(){this.teardownVisibilityTracking();clearInterval(this.poll);clearTimeout(this.queuePoll);clearTimeout(this._realtimeTimer);this.stopRealtimeUpdates();this.resetAudio();this.resetVideo()},
  methods: {
    toast(title,icon='none'){uni.showToast({title,icon,duration:2600})},
    setupVisibilityTracking(){
      if(typeof document==='undefined')return
      this.pageVisible=!document.hidden
      this._visibilityHandler=()=>this.handlePageVisibility(!document.hidden)
      document.addEventListener('visibilitychange',this._visibilityHandler)
    },
    teardownVisibilityTracking(){if(typeof document!=='undefined'&&this._visibilityHandler)document.removeEventListener('visibilitychange',this._visibilityHandler);this._visibilityHandler=null},
    async handlePageVisibility(visible){
      const next=Boolean(visible)
      if(this.pageVisible===next&&next)return
      this.pageVisible=next
      if(!next){clearTimeout(this.queuePoll);clearInterval(this.poll);return}
      try{await Promise.all([this.loadTasks(),this.loadProjects()]);if(this.current?.id)this.current=await request(`/api/projects/${this.current.id}`)}catch(_){}
      this._pendingRealtimeRefresh=false
      if(!this.realtimeConnected)this.startQueuePolling()
      this.startPolling()
    },
    navigateTo(mode){if(mode==='projects'&&this.viewMode==='projects'&&this.current){this.backToProjectLibrary();return}this.viewMode=mode;if(mode!=='projects'){clearInterval(this.poll);this.resetAudio();this.resetVideo()}if(mode==='create'){this.current=null;this.createPanels={title:false,subtitle:false,bgm:false}};if(mode==='projects'&&!this.current)this.loadProjects();if(mode==='queue')this.loadTasks()},
    backToProjectLibrary(){clearInterval(this.poll);this.resetAudio();this.resetVideo();this.current=null;this.projectTab='overview';this.postPanels={title:false,subtitle:false};if(!this.projects.length)this.loadProjects()},
    taskProgressText(task){if(task.video_segment_total){const current=Number(task.video_segment_current||0);if(task.video_progress_mode==='http_fallback'&&!current)return '视频生成中 · 正在恢复进度显示';return current?`视频生成 ${current}/${task.video_segment_total}`:'视频任务正在准备'}if(task.queue_position)return `队列第 ${task.queue_position} 位`;return this.taskStageName(task.stage)},
    copyError(error){const value=typeof error==='string'?error:JSON.stringify(error,null,2);uni.setClipboardData({data:value,success:()=>this.toast('错误信息已复制','success')})},
    async connectBackend(){let ready=false;try{this.health=await request('/api/health');await Promise.all([this.loadAppSettings(),this.loadFonts(),this.loadProjects(),this.loadTasks()]);ready=true}catch(e){this.toast(e.message)}finally{this._needsRealtimeResync=!ready;this.startRealtimeUpdates()}},
    setProjectTitle(event){this.form.title=event?.detail?.value??event?.target?.value??event?.currentTarget?.value??''},
    syncCreateTitle(event){const value=event?.detail?.value??event?.target?.value??this.form.original_script??'',next=this.suggestVideoTitleText(value);if(!String(this.form.video_title||'').trim()||this.form.video_title===this.lastSuggestedVideoTitle)this.form.video_title=next;this.lastSuggestedVideoTitle=next},
    async loadAppSettings(){this.appSettings=await request('/api/settings')},
    async loadFonts(){try{const fonts=await request('/api/fonts');if(Array.isArray(fonts)&&fonts.length)this.availableFonts=fonts}catch(_){}},
    pasteComfyUrl(){uni.getClipboardData({success:result=>{const value=String(result.data||'').trim();this.appSettings.comfy_url=value;this.toast(value?'URL 已粘贴':'剪贴板为空',value?'success':'none')},fail:error=>this.toast(error.errMsg||'无法读取剪贴板')})},
    async saveAppSettings(){const value=String(this.appSettings.comfy_url||'').trim();if(!value){this.toast('请填写 ComfyUI URL');return}this.settingsSaving=true;try{this.appSettings=await request('/api/settings',{method:'PATCH',data:{comfy_url:value}});this.toast('ComfyUI 全局配置已保存','success')}catch(error){this.toast(error.message)}finally{this.settingsSaving=false}},
    async loadProjects(){this.projects=await request('/api/projects/summary')},
    async loadTasks(){this.tasks=await request('/api/tasks/summary?limit=100')},
    async openProject(id){clearInterval(this.poll);this.resetAudio();this.resetVideo();this.postPanels={title:false,subtitle:false};this.viewMode='projects';this.projectTab='overview';this.current=await request(`/api/projects/${id}`);this.startPolling()},
    deleteProject(item){uni.showModal({title:'删除项目',content:`确定删除“${item.title}”吗？项目素材、生成音频和视频都会一并删除，无法恢复。`,confirmText:'删除',confirmColor:'#ff7583',success:async result=>{if(!result.confirm)return;try{await request(`/api/projects/${item.id}`,{method:'DELETE'});if(this.current?.id===item.id){clearInterval(this.poll);this.resetAudio();this.current=null}await this.loadProjects();this.toast('项目已删除','success')}catch(error){this.toast(error.message)}}})},
    newProject(){this.navigateTo('create')},
    statusName(value){return STATUS[value]||value},
    ttsEngineName(value){return value==='indextts2_voice_clone'?'音色与情感参考':'情绪参数配音'},
    emotionNameLabel(value){return {Happy:'开心',Angry:'愤怒',Sad:'悲伤',Fear:'害怕',Hate:'厌恶',Low:'低落',Surprise:'惊讶',Neutral:'平静'}[value]||value},
    taskStatusName(value){return TASK_STATUS[value]||value},
    taskStageName(value){return TASK_STAGE[value]||value},
    taskStatusContext(task){return task.status_source==='project_after_terminal_task'?'项目当前状态':(task.status_source==='project'?'项目实时阶段':this.taskStageName(task.stage))},
    taskDisplayClass(task){const status=task.display_status||task.status;if(status==='ERROR')return 'failed';if(BUSY.has(status)||task.status==='RUNNING')return 'running';if(status==='COMPLETED')return 'completed';return String(task.status||'').toLowerCase()},
    canRetryTask(task){return (task.status==='FAILED'||task.status==='CANCELLED')&&!BUSY.has(task.display_status)},
    async saveCurrentTitle(){try{this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{title:String(this.current.title||'').trim()}});await Promise.all([this.loadProjects(),this.loadTasks()]);this.toast('任务名称已保存','success')}catch(error){this.toast(error.message);this.current=await request(`/api/projects/${this.current.id}`)}},
    async saveOriginalScript(){try{this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{original_script:String(this.current.original_script||'').trim()}});await this.loadTasks();this.toast('口播稿已保存，将按新稿重新分析','success')}catch(error){this.toast(error.message);this.current=await request(`/api/projects/${this.current.id}`)}},
    actionText(field){const values=this.current?.image_analysis?.[field];return Array.isArray(values)&&values.length?values.join('、'):'—'},
    formatRatio(value){const number=Number(value);return Number.isFinite(number)?`${Math.round(number*100)}%`:'—'},
    async checkComfy(url,ttsEngine='indextts2_legacy'){
      const value=String(url||'').trim()
      if(!value){this.comfyCheckOk=false;this.comfyCheckResult='请先填写或粘贴完整的 ComfyUI URL';return}
      this.comfyChecking=true;this.comfyCheckOk=false;this.comfyCheckResult=`正在通过后端 ${getApiBase()} 检测 ${value} …`
      try{
        const result=await request('/api/comfyui/check',{method:'POST',data:{url:value,tts_engine:ttsEngine||'indextts2_legacy'},timeout:75000})
        this.comfyCheckOk=Boolean(result.available)
        this.comfyCheckResult=result.node_check_complete===false
          ?(result.warning||'ComfyUI 已连接，但节点兼容性检查超时。')
          :(result.available?`连接正常，发现 ${result.node_count} 个节点。`:`连接成功，但缺少 ${result.missing_nodes.length} 个节点：${result.missing_nodes.join('、')}`)
      }catch(error){this.comfyCheckOk=false;this.comfyCheckResult=`检测失败：${error.message}`}
      finally{this.comfyChecking=false}
    },
    chooseImage(){uni.chooseImage({count:1,success:r=>{this.imagePath=r.tempFilePaths[0];this.imageFileName=r.tempFiles?.[0]?.name||'已选择图片'}})},
    chooseVoice(){uni.chooseFile({count:1,extension:['wav','flac','mp3','m4a','m4s','mp4','ogg'],success:r=>{this.voicePath=r.tempFilePaths[0];this.voiceFileName=r.tempFiles?.[0]?.name||'已选择音色'}})},
    chooseEmotionVoice(){uni.chooseFile({count:1,extension:['wav','flac','mp3','m4a','m4s','mp4','ogg'],success:r=>{this.emotionVoicePath=r.tempFilePaths[0];this.emotionVoiceFileName=r.tempFiles?.[0]?.name||'已选择情感音频'}})},
    chooseBgm(){uni.chooseFile({count:1,extension:['wav','flac','mp3','m4a','m4s','mp4','ogg','aac'],success:r=>{this.bgmPath=r.tempFilePaths[0];this.bgmFileName=r.tempFiles?.[0]?.name||'已选择背景音乐'}})},
    setAutoRun(event){this.form.auto_run=(event?.detail?.value||[]).includes('auto')},
    setBgmEnabled(event){this.form.bgm_enabled=(event?.detail?.value||[]).includes('bgm')},
    setBgmDucking(event){this.form.bgm_ducking=(event?.detail?.value||[]).includes('duck')},
    setCurrentBgmDucking(event){this.current.bgm_ducking=(event?.detail?.value||[]).includes('duck')},
    setVideoTitleEnabled(event,target){target.video_title_enabled=(event?.detail?.value||[]).includes('title')},
    toggleCreatePanel(name){if(name==='title'||name==='subtitle'||name==='bgm')this.createPanels={...this.createPanels,[name]:!this.createPanels[name]}},
    togglePostPanel(name){if(name==='title'||name==='subtitle')this.postPanels={...this.postPanels,[name]:!this.postPanels[name]}},
    setCurrentVideoTitleEnabled(event){this.setVideoTitleEnabled(event,this.current)},
    setSubtitleEnabled(event){this.form.subtitle_enabled=(event?.detail?.value||[]).includes('subtitle')},
    setSubtitleBackground(event){this.form.subtitle_background_enabled=(event?.detail?.value||[]).includes('background')},
    setCurrentSubtitleEnabled(event){this.current.subtitle_enabled=(event?.detail?.value||[]).includes('subtitle')},
    setCurrentSubtitleBackground(event){this.current.subtitle_background_enabled=(event?.detail?.value||[]).includes('background')},
    setSubtitleBold(event,target){target.subtitle_font_bold=(event?.detail?.value||[]).includes('bold')},
    suggestVideoTitleText(script){const text=String(script||'').replace(/\s+/g,'').trim();if(!text)return '';const sentences=text.split(/[\u3002\uff01\uff1f!?]+/).filter(Boolean),first=sentences[0]||text,clauses=first.split(/[\uff0c,\uff1b;]/).map(item=>item.trim()).filter(Boolean);let lines=clauses.slice(0,3);if(lines.length===1&&lines[0].length>12){const value=lines[0];lines=[];for(let offset=0;offset<Math.min(value.length,36);offset+=12)lines.push(value.slice(offset,offset+12))}if(lines.length===1&&sentences.length>1)lines.push(...sentences.slice(1,3));return lines.slice(0,3).map(line=>line.slice(0,12)).join('\n')},
    videoTitleLines(value){const lines=[];for(const raw of String(value||'').replace(/\r/g,'').split('\n')){let line=raw.replace(/\s+/g,'');while(line&&lines.length<3){lines.push(line.slice(0,12));line=line.slice(12)}if(lines.length>=3)break}return lines.length?lines:['\u89c6\u9891\u6807\u9898']},
    applyChannelsTitlePreset(target,script){Object.assign(target,{video_title_enabled:true,video_title_font_name:'Microsoft YaHei',video_title_font_size:88,video_title_primary_color:'#FFFFFF',video_title_secondary_color:'#FFFFFF',video_title_position:10,video_title_stroke_color:'#000000',video_title_stroke_width:4});if(!String(target.video_title||'').trim())target.video_title=this.suggestVideoTitleText(script);this.toast('已应用视频号顶部标题预设','success')},
    videoTitlePositionStyle(value){return {top:`${Math.max(3,Math.min(35,Number(value?.video_title_position??10)))}%`}},
    videoTitleLineStyle(value){const width=Math.max(0,Number(value?.video_title_stroke_width??4)/4.5),stroke=String(value?.video_title_stroke_color||'#000000');return {display:'block',fontFamily:String(value?.video_title_font_name||'sans-serif'),fontSize:`${Math.max(14,Number(value?.video_title_font_size||88)/4.5)}px`,fontWeight:'800',lineHeight:'1.2',color:String(value?.video_title_primary_color||'#FFFFFF'),WebkitTextStroke:width?`${width}px ${stroke}`:'0',paintOrder:'stroke fill'}},
    applyChannelsSubtitlePreset(target){Object.assign(target,{subtitle_font_name:'Microsoft YaHei',subtitle_font_size:64,subtitle_font_bold:true,subtitle_font_color:'#FFFFFF',subtitle_position:'custom',subtitle_custom_position:73,subtitle_stroke_color:'#000000',subtitle_stroke_width:3,subtitle_background_enabled:false,subtitle_background_color:'#000000',subtitle_background_opacity:40,subtitle_max_chars:14});this.toast('已应用视频号口播字幕预设','success')},
    subtitlePreviewPositionStyle(value){const position=String(value?.subtitle_position||'custom');const top=position==='top'?14:position==='center'?50:position==='bottom'?76:Number(value?.subtitle_custom_position??73);return {top:`${Math.max(5,Math.min(90,top))}%`}},
    subtitlePreviewStyle(value){
      const hex=String(value?.subtitle_background_color||'#000000')
      const alpha=Math.max(0,Math.min(1,Number(value?.subtitle_background_opacity||0)/100))
      const rgb=/^#[0-9a-fA-F]{6}$/.test(hex)?`${parseInt(hex.slice(1,3),16)},${parseInt(hex.slice(3,5),16)},${parseInt(hex.slice(5,7),16)}`:'0,0,0'
      const strokeWidth=Math.max(0,Number(value?.subtitle_stroke_width??3)/4.5),stroke=String(value?.subtitle_stroke_color||'#000000')
      return {fontFamily:String(value?.subtitle_font_name||'sans-serif'),fontSize:`${Math.max(12,Number(value?.subtitle_font_size||64)/4.5)}px`,fontWeight:value?.subtitle_font_bold?'700':'400',color:String(value?.subtitle_font_color||'#FFFFFF'),WebkitTextStroke:strokeWidth?`${strokeWidth}px ${stroke}`:'0',paintOrder:'stroke fill',textShadow:'none',backgroundColor:value?.subtitle_background_enabled?`rgba(${rgb},${alpha})`:'transparent'}
    },
    async createProject(){this.uploadProgress=0;this.uploadLabel='';if(!this.form.original_script.trim()){this.toast('请填写口播文案');return}const needsEmotion=this.form.tts_engine==='indextts2_voice_clone';if(needsEmotion&&!this.emotionVoicePath){this.toast('请选择情感参考音频');return}if(this.form.bgm_enabled&&!this.bgmPath){this.toast('启用视频配乐后请选择背景音乐');return}const autoRun=Boolean(this.form.auto_run);let task=null;this.submitting=true;try{const createPayload={...this.form,expect_image_upload:Boolean(this.imagePath),expect_voice_upload:Boolean(this.voicePath),expect_emotion_voice_upload:Boolean(this.emotionVoicePath),expect_bgm_upload:Boolean(this.bgmPath)};let p=await request('/api/projects/default',{method:'POST',data:createPayload});if(autoRun){task=await request(`/api/projects/${p.id}/enqueue`,{method:'POST'});await this.loadTasks()}try{if(this.imagePath){this.uploadLabel='正在上传数字人图片';p=await upload(`/api/projects/${p.id}/assets/image`,this.imagePath,'file',value=>this.uploadProgress=value)}if(this.voicePath){this.uploadLabel='正在上传参考声音';p=await upload(`/api/projects/${p.id}/assets/voice`,this.voicePath,'file',value=>this.uploadProgress=value)}if(this.emotionVoicePath){this.uploadLabel='正在上传情感音频';p=await upload(`/api/projects/${p.id}/assets/emotion_voice`,this.emotionVoicePath,'file',value=>this.uploadProgress=value)}if(this.bgmPath){this.uploadLabel='正在上传背景音乐';p=await upload(`/api/projects/${p.id}/assets/bgm`,this.bgmPath,'file',value=>this.uploadProgress=value)}if(!p.has_image||!p.has_voice||(needsEmotion&&!p.has_emotion_voice)||(this.form.bgm_enabled&&!p.has_bgm))throw new Error('图片、音频或背景音乐素材未上传成功')}catch(uploadError){if(task)try{await request(`/api/tasks/${task.id}/cancel`,{method:'POST'})}catch(_){}throw uploadError}if(autoRun)this.current=null;else this.current=p;this.imagePath='';this.imageFileName='';this.voicePath='';this.voiceFileName='';this.emotionVoicePath='';this.emotionVoiceFileName='';this.bgmPath='';this.bgmFileName='';await Promise.all([this.loadProjects(),this.loadTasks()]);if(autoRun)this.toast('任务已入队，素材上传完成','success');else this.toast('项目已创建，请按步骤手动操作','success')}catch(e){this.toast(e.message)}finally{this.submitting=false;this.uploadProgress=0;this.uploadLabel=''}},
    async cancelTask(task){try{await request(`/api/tasks/${task.id}/cancel`,{method:'POST'});await this.loadTasks();this.toast('任务已取消','success')}catch(error){this.toast(error.message)}},
    async retryTask(task){try{await request(`/api/tasks/${task.id}/retry`,{method:'POST'});await this.loadTasks();this.toast('任务已重新排队','success')}catch(error){this.toast(error.message)}},
    deleteTask(task){const running=task.status==='QUEUED'||task.status==='RUNNING';uni.showModal({title:'删除任务',content:`确定删除“${task.project_title}”吗？${running?'当前执行会先安全停止；':''}项目素材、生成音频和视频都会一并删除，无法恢复。`,confirmText:'删除任务',confirmColor:'#ff7583',success:async result=>{if(!result.confirm)return;task.deleting=true;try{await request(`/api/tasks/${task.id}`,{method:'DELETE',timeout:45000});if(this.current?.id===task.project_id){clearInterval(this.poll);this.resetAudio();this.current=null}await Promise.all([this.loadTasks(),this.loadProjects()]);this.toast('任务及项目已删除','success')}catch(error){task.deleting=false;this.toast(error.message)}}})},
    startRealtimeUpdates(){
      this.stopRealtimeUpdates()
      if(typeof EventSource==='undefined'){this.startQueuePolling();return}
      try{
        const source=new EventSource(`${getApiBase()}/api/events/tasks`)
        this._eventSource=source
        source.onopen=async()=>{
          const shouldResync=Boolean(this._needsRealtimeResync)
          this.realtimeConnected=true;this._needsRealtimeResync=false;clearTimeout(this.queuePoll);clearInterval(this.poll)
          if(shouldResync&&this.pageVisible)try{const [health]=await Promise.all([request('/api/health'),this.loadAppSettings(),this.loadFonts(),this.loadTasks(),this.loadProjects()]);this.health=health;if(this.current?.id)this.current=await request(`/api/projects/${this.current.id}`)}catch(_){}
        }
        source.onmessage=event=>{let message={};try{message=JSON.parse(event.data||'{}')}catch(_){}if(message.entity==='connected')return;this.scheduleRealtimeRefresh(message)}
        source.onerror=()=>{this.realtimeConnected=false;this._needsRealtimeResync=true;this.startQueuePolling()}
      }catch(_){this.startQueuePolling()}
    },
    stopRealtimeUpdates(reset=true){if(this._eventSource){this._eventSource.close();this._eventSource=null}if(reset)this.realtimeConnected=false},
    sortRealtimeTasks(tasks){
      const rank={RUNNING:0,QUEUED:1,FAILED:2,CANCELLED:3}
      const sorted=[...tasks].sort((left,right)=>{const status=(rank[left.status]??9)-(rank[right.status]??9);if(status)return status;return String(left.created_at||'').localeCompare(String(right.created_at||''))})
      let position=0
      return sorted.map(task=>task.status==='QUEUED'?{...task,queue_position:++position}:{...task,queue_position:null})
    },
    applyRealtimeMessage(message={}){
      const entity=message.entity,payload=message.payload||{}
      if(entity==='project_deleted'){
        this.projects=this.projects.filter(item=>item.id!==payload.id)
        this.tasks=this.tasks.filter(task=>task.project_id!==payload.id)
        if(this.current?.id===payload.id){this.resetAudio();this.resetVideo();this.current=null}
        return false
      }
      if(entity==='task_deleted'){
        this.tasks=this.tasks.filter(task=>task.id!==payload.id)
        return false
      }
      if(entity==='task'){
        if(payload.status==='COMPLETED')this.tasks=this.tasks.filter(task=>task.id!==payload.id)
        else{
          const existing=this.tasks.find(task=>task.id===payload.id)
          const merged=existing?{...existing,...payload}:payload
          this.tasks=this.sortRealtimeTasks(existing?this.tasks.map(task=>task.id===payload.id?merged:task):[...this.tasks,merged])
        }
        return false
      }
      if(entity!=='project'||!payload.id)return false
      const existing=this.projects.find(item=>item.id===payload.id)
      const merged=existing?{...existing,...payload}:payload
      this.projects=(existing?this.projects.map(item=>item.id===payload.id?merged:item):[merged,...this.projects]).sort((left,right)=>String(right.updated_at||'').localeCompare(String(left.updated_at||'')))
      this.tasks=this.tasks.map(task=>task.project_id===payload.id?{...task,project_status:payload.status,project_progress:payload.progress,project_stage_progress:payload.stage_progress,display_status:task.status==='RUNNING'||task.status==='QUEUED'?payload.status:task.display_status,display_progress:payload.progress,video_segment_current:payload.video_segment_current,video_segment_completed:payload.video_segment_completed,video_segment_total:payload.video_segment_total,video_segment_progress:payload.video_segment_progress,error:payload.error||task.error}:task)
      if(this.current?.id!==payload.id)return false
      const previousStatus=this.current.status
      this.current={...this.current,...payload}
      return Boolean(payload.status&&payload.status!==previousStatus)
    },
    scheduleRealtimeRefresh(message={}){
      if(!this.pageVisible){this._pendingRealtimeRefresh=true;return}
      const refreshCurrent=this.applyRealtimeMessage(message)
      if(!refreshCurrent||!this.current?.id)return
      const projectId=this.current.id
      clearTimeout(this._realtimeTimer)
      this._realtimeTimer=setTimeout(async()=>{try{if(this.pageVisible&&this.current?.id===projectId)this.current=await request(`/api/projects/${projectId}`)}catch(_){}},220)
    },
    startQueuePolling(){
      clearTimeout(this.queuePoll)
      if(!this.pageVisible||this.realtimeConnected||!this.tasks.some(task=>task.status==='RUNNING'||task.status==='QUEUED'))return
      const tick=async()=>{
        if(this.realtimeConnected||!this.pageVisible)return
        try{await this.loadTasks();if(this.viewMode==='projects')await this.loadProjects();if(this.current?.id&&this.tasks.some(task=>task.project_id===this.current.id&&(task.status==='RUNNING'||task.status==='QUEUED')))this.current=await request(`/api/projects/${this.current.id}`)}catch(_){}
        if(this.realtimeConnected||!this.pageVisible||!this.tasks.some(task=>task.status==='RUNNING'||task.status==='QUEUED'))return
        this.queuePoll=setTimeout(tick,5000)
      }
      this.queuePoll=setTimeout(tick,1000)
    },
    setEmotion(name,value){if(!this.current.emotion)this.current.emotion={};this.current.emotion[name]=value/100},
    async saveDirector(){this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{script:this.current.script,emotion:this.current.emotion}});this.toast('导演方案已保存','success')},
    async saveSegments(){this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{segments:this.current.segments}});this.toast('动作计划已保存','success')},
    async run(stage){try{if(stage==='audio')await this.saveDirector();if(stage==='video')await this.saveSegments();this.current=await request(`/api/projects/${this.current.id}/run/${stage}`,{method:'POST'});this.startPolling()}catch(e){this.toast(e.message)}},
    startPolling(){clearInterval(this.poll);if(!this.pageVisible||!this.isBusy||this.realtimeConnected)return;this.poll=setInterval(async()=>{try{this.current=await request(`/api/projects/${this.current.id}`);if(!this.isBusy){clearInterval(this.poll);await this.loadProjects()}}catch(e){clearInterval(this.poll);this.toast(e.message)}},5000)},
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
    loadVideo(kind='video'){this.videoSource=this.assetUrl(kind)},
    resetVideo(){this.videoSource=''},
    videoLoadError(){this.videoSource='';this.toast('视频加载失败，请重试')},
    chooseProjectBgm(){uni.chooseFile({count:1,extension:['wav','flac','mp3','m4a','m4s','mp4','ogg','aac'],success:async r=>{try{this.current=await upload(`/api/projects/${this.current.id}/assets/bgm`,r.tempFilePaths[0]);this.resetVideo();this.toast('背景音乐已上传，请重新生成配乐版','success')}catch(error){this.toast(error.message)}}})},
    async saveBgmAndRun(){try{this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data:{bgm_enabled:true,bgm_volume:Number(this.current.bgm_volume||0),bgm_ducking:Boolean(this.current.bgm_ducking),bgm_fade_in:Number(this.current.bgm_fade_in||0),bgm_fade_out:Number(this.current.bgm_fade_out||0)}});this.resetVideo();this.current=await request(`/api/projects/${this.current.id}/run/bgm`,{method:'POST'});this.startPolling()}catch(error){this.toast(error.message)}},
    async saveSubtitleAndRun(){try{
      const fields=['subtitle_enabled','subtitle_font_name','subtitle_font_size','subtitle_font_bold','subtitle_font_color','subtitle_position','subtitle_custom_position','subtitle_stroke_color','subtitle_stroke_width','subtitle_background_enabled','subtitle_background_color','subtitle_background_opacity','subtitle_max_chars','video_title_enabled','video_title','video_title_font_name','video_title_font_size','video_title_primary_color','video_title_secondary_color','video_title_position','video_title_stroke_color','video_title_stroke_width'],numeric=new Set(['subtitle_font_size','subtitle_custom_position','subtitle_stroke_width','subtitle_background_opacity','subtitle_max_chars','video_title_font_size','video_title_position','video_title_stroke_width'])
      const data={};for(const field of fields)data[field]=numeric.has(field)?Number(this.current[field]):this.current[field]
      this.current=await request(`/api/projects/${this.current.id}`,{method:'PATCH',data});this.resetVideo()
      const stage=(this.current.subtitle_enabled||this.current.video_title_enabled)?'subtitle':(this.current.bgm_enabled?'bgm':'')
      if(stage){this.current=await request(`/api/projects/${this.current.id}/run/${stage}`,{method:'POST'});this.startPolling()}else this.toast('标题与字幕已关闭','success')
    }catch(error){this.toast(error.message)}},
    downloadVideo(kind='video'){
      const url=this.assetUrl(kind)
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
