<template>
  <view class="settings-shell">
    <view class="settings-heading"><text class="eyebrow">SYSTEM</text><text class="page-title">系统设置</text><text class="lead">集中管理 ComfyUI、本地音乐库和生产能力，新建任务时无需重复填写。</text></view>
    <view class="api-bar panel comfy-settings">
      <view><text class="label-title">ComfyUI 全局配置</text><text class="hint">保存一次，之后所有手动和队列任务统一使用该地址</text></view>
      <textarea v-model="settings.comfy_url" class="field url-field" :maxlength="-1" auto-height placeholder="http://127.0.0.1:8188"></textarea>
      <view class="inline"><button class="ghost small" @tap.stop="$emit('paste')">粘贴</button><button class="ghost small" :disabled="checking" @tap.stop="$emit('check')">{{ checking ? '检测中' : '检测' }}</button></view>
      <view v-if="checkResult" class="check-message global-check-message" :class="{success:checkOk,error:!checkOk&&!checking}">{{ checkResult }}</view>
    </view>
    <view class="api-bar panel comfy-settings music-library-settings">
      <view><text class="label-title">随机配乐文件夹</text><text class="hint">填写后端服务器上的文件夹路径；随机模式会从其中选择支持的音频文件</text></view>
      <textarea v-model="settings.music_library_path" class="field url-field" :maxlength="-1" auto-height placeholder="C:\code\digital_factory\material\music"></textarea>
      <text class="hint">当前识别到 {{ Number(settings.music_library_count || 0) }} 首音乐</text>
    </view>
    <view class="settings-save-row"><button class="primary small" :disabled="saving" @tap.stop="$emit('save')">{{ saving ? '保存中' : '保存全部设置' }}</button></view>
    <view class="settings-grid">
      <view class="panel setting-card"><text class="setting-icon">AI</text><view><text class="auto-run-title">AI 导演</text><text class="hint">{{ health.vision_enabled ? '视觉模型已配置' : '视觉模型尚未配置' }}</text></view><text class="setting-state" :class="{ok:health.vision_enabled}">{{ health.vision_enabled ? '正常' : '检查配置' }}</text></view>
      <view class="panel setting-card"><text class="setting-icon">W</text><view><text class="auto-run-title">Whisper CLI</text><text class="hint">{{ health.whisper_executable || '未检测到命令' }}</text></view><text class="setting-state" :class="{ok:health.asr_enabled}">{{ health.asr_enabled ? '正常' : '不可用' }}</text></view>
      <view class="panel setting-card"><text class="setting-icon">Q</text><view><text class="auto-run-title">生产队列</text><text class="hint">单 GPU 安全顺序执行</text></view><text class="setting-state ok">{{ health.queue_worker==='running' ? '运行中' : '已停止' }}</text></view>
    </view>
  </view>
</template>

<script>
export default {name:'SettingsPage',props:{settings:{type:Object,required:true},health:{type:Object,default:()=>({})},saving:Boolean,checking:Boolean,checkResult:{type:String,default:''},checkOk:Boolean},emits:['paste','check','save']}
</script>