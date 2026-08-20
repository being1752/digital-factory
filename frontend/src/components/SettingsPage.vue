<template>
  <view class="settings-shell">
    <view class="settings-heading"><text class="eyebrow">SYSTEM</text><text class="page-title">系统设置</text><text class="lead">集中管理 ComfyUI 和本地生产能力，新建任务时无需重复填写。</text></view>
    <view class="api-bar panel comfy-settings">
      <view><text class="label-title">ComfyUI 全局配置</text><text class="hint">保存一次，之后所有手动和队列任务统一使用该地址</text></view>
      <textarea v-model="settings.comfy_url" class="field url-field" :maxlength="-1" auto-height placeholder="http://127.0.0.1:8188"></textarea>
      <view class="inline"><button class="ghost small" @tap.stop="$emit('paste')">粘贴</button><button class="ghost small" :disabled="checking" @tap.stop="$emit('check')">{{ checking ? '检测中' : '检测' }}</button><button class="primary small" :disabled="saving" @tap.stop="$emit('save')">{{ saving ? '保存中' : '保存' }}</button></view>
      <view v-if="checkResult" class="check-message global-check-message" :class="{success:checkOk,error:!checkOk&&!checking}">{{ checkResult }}</view>
    </view>
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
