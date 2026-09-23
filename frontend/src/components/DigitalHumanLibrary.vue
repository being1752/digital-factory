<template>
  <view class="character-library">
    <template v-if="!editorOpen">
      <view class="page-heading">
        <view><text class="eyebrow">CHARACTERS</text><text class="page-title">数字人库</text><text class="lead">统一管理常用数字人图片、音色和情感参考。</text></view>
        <button class="primary small" @click="$emit('create')">＋ 新建数字人</button>
      </view>
      <view v-if="!profiles.length" class="panel page-empty"><text class="empty-title">还没有数字人配置</text><text class="hint">创建后，新建任务时可以直接选择并复用全部素材。</text><button class="primary small" @click="$emit('create')">创建第一个数字人</button></view>
      <view v-else class="character-grid">
        <view v-for="item in profiles" :key="item.id" class="panel character-card" @click="$emit('open',item)">
          <view class="character-card-cover"><image v-if="item.has_image" class="character-card-image" :src="profileImage(item)" mode="aspectFill" lazy-load/><text v-else>DF</text><text class="project-status-badge">{{ engineName(item.default_tts_engine) }}</text></view>
          <view class="character-card-copy"><text class="task-card-title">{{ item.name }}</text><text class="character-assets">{{ item.has_voice ? '✓ 音色' : '缺少音色' }} · {{ item.has_emotion_voice ? '✓ 情感参考' : '无情感参考' }}</text></view>
          <button class="project-delete" @click.stop="$emit('delete',item)">删除</button>
        </view>
      </view>
    </template>
    <template v-else>
      <view class="page-heading character-editor-heading">
        <view><button class="ghost small character-back" @click="$emit('back')">← 返回数字人库</button><text class="eyebrow">CHARACTER PROFILE</text><text class="page-title">{{ editingProfile ? ('修改：' + (draft.name || editingProfile.name)) : '新建数字人配置' }}</text><text class="lead">任务会复制一份独立素材，修改配置不会影响历史项目。</text></view>
      </view>
      <view class="panel character-editor">
        <view class="character-editor-layout">
          <view class="character-editor-preview"><image v-if="imagePath||existingImage" :src="imagePath||existingImage" mode="aspectFill"/><view v-else class="character-preview-empty">选择一张数字人图片</view></view>
          <view class="character-editor-form">
            <text class="label">配置名称</text><textarea :value="draft.name" class="field character-name-field" maxlength="100" auto-height placeholder="例如：陈彬" @input="$emit('update-draft',{field:'name',value:$event.detail.value})"></textarea><text v-if="editingProfile" class="hint character-current-name">当前保存名称：{{ editingProfile.name }}</text>
            <text class="label">备注</text><textarea :value="draft.note" class="textarea character-note" maxlength="500" placeholder="可填写人物、用途或声音特点" @input="$emit('update-draft',{field:'note',value:$event.detail.value})"></textarea>
            <text class="label">默认配音方式</text>
            <view class="engine-options"><button class="engine-option" :class="{active:draft.default_tts_engine==='indextts2_legacy'}" @click="$emit('update-draft',{field:'default_tts_engine',value:'indextts2_legacy'})"><text>情绪参数配音</text></button><button class="engine-option" :class="{active:draft.default_tts_engine==='indextts2_voice_clone'}" @click="$emit('update-draft',{field:'default_tts_engine',value:'indextts2_voice_clone'})"><text>音色与情感参考</text></button></view>
          </view>
        </view>
        <view class="uploads character-editor-assets" :class="{'two-assets':draft.default_tts_engine==='indextts2_legacy'}">
          <view class="upload-box" :class="{selected:Boolean(imagePath||editingProfile?.has_image)}" @click="$emit('choose-asset','image')"><text class="upload-icon">▣</text><text>数字人图片</text><text class="upload-status">{{ imagePath ? '✓ 已选择新图片' : (editingProfile?.has_image ? '✓ 已保存' : '请选择') }}</text><text class="hint file-name">{{ imageFileName || '支持 PNG、JPG、WEBP' }}</text></view>
          <view class="upload-box" :class="{selected:Boolean(voicePath||editingProfile?.has_voice)}" @click="$emit('choose-asset','voice')"><text class="upload-icon">♪</text><text>音色参考音频</text><text class="upload-status">{{ voicePath ? '✓ 已选择新音色' : (editingProfile?.has_voice ? '✓ 已保存' : '请选择') }}</text><text class="hint file-name">{{ voiceFileName || '用于还原人物音色' }}</text></view>
          <view v-if="draft.default_tts_engine==='indextts2_voice_clone'" class="upload-box" :class="{selected:Boolean(emotionVoicePath||editingProfile?.has_emotion_voice)}" @click="$emit('choose-asset','emotion_voice')"><text class="upload-icon">♫</text><text>情感参考音频</text><text class="upload-status">{{ emotionVoicePath ? '✓ 已选择新参考' : (editingProfile?.has_emotion_voice ? '✓ 已保存' : '请选择') }}</text><text class="hint file-name">{{ emotionVoiceFileName || '用于参考说话语气和情绪表达' }}</text></view>
        </view>
        <view class="actions character-editor-actions"><button class="ghost small" @click="$emit('back')">取消</button><button class="primary small" :disabled="saving" @click="$emit('save')">{{ saving ? '正在保存…' : (editingProfile ? '保存修改' : '保存为新配置') }}</button></view>
      </view>
    </template>
  </view>
</template>

<script>
import { fileUrl } from '../utils/api.js'
export default {
  name:'DigitalHumanLibrary',
  props:{profiles:{type:Array,default:()=>[]},editorOpen:Boolean,editingProfile:{type:Object,default:null},draft:{type:Object,required:true},imagePath:{type:String,default:''},imageFileName:{type:String,default:''},voicePath:{type:String,default:''},voiceFileName:{type:String,default:''},emotionVoicePath:{type:String,default:''},emotionVoiceFileName:{type:String,default:''},saving:Boolean},
  emits:['create','open','back','save','delete','choose-asset','update-draft'],
  computed:{existingImage(){return this.editingProfile?.has_image?this.profileImage(this.editingProfile):''}},
  methods:{profileImage(item){return fileUrl(item.image_url)},engineName(value){return value==='indextts2_voice_clone'?'音色＋情感参考':'情绪参数配音'}}
}
</script>
