<template>
<view class="panel create-card">
          <text class="eyebrow">PHASE TWO</text><text class="hero">从一张照片，到完整口播视频</text>
          <text class="lead">根据口播内容自动匹配配音、表情和动作，生成自然连贯的数字人口播视频。</text>
          <view class="create-section">
            <view class="create-section-head"><view><text class="section-index">01</text><text class="plan-title">任务内容</text></view><text class="hint">{{ scriptSummary }}</text></view>
          <view><text class="label">项目名称</text><textarea :value="form.title" class="field project-title-field" maxlength="100" auto-height @input="$emit('set-title',$event)"></textarea></view>
          <view><text class="label">原始口播文案</text><textarea v-model="form.original_script" class="textarea script-input" :maxlength="-1" @input="$emit('sync-script',$event)"></textarea></view>
          </view>
          <view class="create-section">
            <view class="create-section-head"><view><text class="section-index">02</text><text class="plan-title">数字人与声音素材</text></view><text class="hint">选择声音效果，并上传需要的参考素材</text></view>
          <view><text class="label">配音方式</text><view class="engine-options"><button class="engine-option" :class="{active:!form.tts_engine||form.tts_engine==='indextts2_legacy'}" @click="form.tts_engine='indextts2_legacy'"><text>情绪参数配音</text><text class="hint">使用参考音色，可在导演方案中调整整体情绪比例</text></button><button class="engine-option" :class="{active:form.tts_engine==='indextts2_voice_clone'}" @click="form.tts_engine='indextts2_voice_clone'"><text>音色与情感参考</text><text class="hint">分别参考说话音色和情绪表达，还原更接近示例的声音</text></button></view></view>
          <view class="uploads">
            <view class="upload-box image-upload" :class="{selected:Boolean(imagePath)}" @click="$emit('choose-image')">
              <image v-if="imagePath" class="upload-preview" :src="imagePath" mode="aspectFill" />
              <view class="upload-copy"><text>数字人图片</text><text v-if="imagePath" class="upload-status">✓ 已选择</text><text class="hint file-name">{{ imageFileName || '点击选择；不选使用默认图片' }}</text></view>
            </view>
            <view class="upload-box" :class="{selected:Boolean(voicePath)}" @click="$emit('choose-voice')">
              <text class="upload-icon">♪</text><text>音色参考音频</text><text v-if="voicePath" class="upload-status">✓ 已选择</text><text class="hint file-name">{{ voiceFileName || '点击选择；不选使用默认音色' }}</text>
            </view>
            <view v-if="form.tts_engine==='indextts2_voice_clone'" class="upload-box emotion-reference" :class="{selected:Boolean(emotionVoicePath)}" @click="$emit('choose-emotion-voice')">
              <text class="upload-icon">♫</text><text>情感参考音频</text><text v-if="emotionVoicePath" class="upload-status">✓ 已选择</text><text class="hint file-name">{{ emotionVoiceFileName || '用于参考说话语气和情绪表达' }}</text>
            </view>
          </view>
          </view>
          <view class="create-section">
            <view class="create-section-head"><view><text class="section-index">03</text><text class="plan-title">执行方式</text></view><text class="setting-state" :class="{ok:form.auto_run}">{{ form.auto_run ? '全自动' : '手动执行' }}</text></view>
          <checkbox-group class="auto-run-option" @change="$emit('set-auto-run',$event)"><label><checkbox value="auto" :checked="form.auto_run" color="#d7ff68" /><view><text class="auto-run-title">全自动制作</text><text class="hint">提交后自动完成内容分析、配音、动作设计和视频制作，失败时会自动重试</text></view></label></checkbox-group>
          </view>
          <view class="create-section">
            <view class="create-section-head"><view><text class="section-index">04</text><text class="plan-title">成品后期设置</text></view><text class="hint">默认配置可直接使用，需要精调时再展开</text></view>
            <view class="post-accordion create-accordion">
              <view class="post-accordion-head" @click="$emit('toggle-panel','bgm')"><view><text class="auto-run-title">背景音乐</text><text class="hint">自动匹配时长并调节音量，让人声保持清晰</text></view><view class="accordion-meta"><text class="accordion-status" :class="{enabled:form.bgm_enabled}">{{ form.bgm_enabled ? '已开启' : '未开启' }}</text><text class="accordion-arrow">{{ createPanels.bgm ? '▲' : '▼' }}</text></view></view>
              <view v-if="createPanels.bgm" class="post-accordion-body">
          <checkbox-group class="auto-run-option" @change="$emit('set-bgm-enabled',$event)"><label><checkbox value="bgm" :checked="form.bgm_enabled" color="#d7ff68" /><view><text class="auto-run-title">视频生成后添加背景音乐</text><text class="hint">自动匹配视频时长并调节音乐音量，同时保留无配乐版本。</text></view></label></checkbox-group>
              <view v-if="form.bgm_enabled" class="upload-box" :class="{selected:Boolean(bgmPath)}" @click="$emit('choose-bgm')">
                <text class="upload-icon">♬</text><text>背景音乐</text><text v-if="bgmPath" class="upload-status">✓ 已选择</text><text class="hint file-name">{{ bgmFileName || '启用配乐后必选；支持 MP3、WAV、M4A、FLAC 等' }}</text>
              </view>
          <view v-if="form.bgm_enabled" class="bgm-settings">
            <view><text class="label">配乐音量 {{ Math.round(Number(form.bgm_volume || 0) * 100) }}%</text><slider :value="Number(form.bgm_volume || 0) * 100" min="0" max="100" step="1" activeColor="#d7ff68" block-size="14" @changing="form.bgm_volume=$event.detail.value/100"/></view>
            <checkbox-group @change="$emit('set-bgm-ducking',$event)"><label class="compact-check"><checkbox value="duck" :checked="form.bgm_ducking" color="#d7ff68" /><text>人声出现时自动降低配乐</text></label></checkbox-group>
            <view class="bgm-times"><view><text class="label">淡入秒数</text><input v-model="form.bgm_fade_in" class="field" type="digit" /></view><view><text class="label">淡出秒数</text><input v-model="form.bgm_fade_out" class="field" type="digit" /></view></view>
          </view>
              </view>
            </view>
            <view class="post-accordion create-accordion">
              <view class="post-accordion-head" @click="$emit('toggle-panel','title')"><view><text class="auto-run-title">视频上方标题</text><text class="hint">从口播稿自动提取，最多三行</text></view><view class="accordion-meta"><text class="accordion-status" :class="{enabled:form.video_title_enabled}">{{ form.video_title_enabled ? '已开启' : '未开启' }}</text><text class="accordion-arrow">{{ createPanels.title ? '▲' : '▼' }}</text></view></view>
              <view v-if="createPanels.title" class="post-accordion-body">
          <checkbox-group class="auto-run-option" @change="$emit('set-title-enabled',$event)"><label><checkbox value="title" :checked="form.video_title_enabled" color="#d7ff68" /><view><text class="auto-run-title">视频上方常驻标题</text><text class="hint">从口播稿自动提取，可编辑；从 0 秒显示到视频结束。</text></view></label></checkbox-group>
            <view v-if="form.video_title_enabled" class="video-title-editor">
              <view class="subtitle-preset-bar"><view><text class="auto-run-title">视频号顶部标题</text><text class="hint">统一标题颜色 · 88号粗体 · 黑色描边 · 距顶部10%</text></view><button class="ghost small" @click="$emit('apply-title-preset')">应用预设</button></view>
              <view><text class="label">标题文案（最多三行，每行12字）</text><textarea v-model="form.video_title" class="textarea video-title-input" :maxlength="100" placeholder="第一行标题&#10;第二行标题&#10;第三行标题"></textarea><button class="ghost small title-regenerate" @click="form.video_title=suggestVideoTitleText(form.original_script)">从口播稿重新提取</button></view>
              <view class="subtitle-grid"><view><text class="label">字体</text><picker :range="fontOptions" @change="form.video_title_font_name=fontOptions[$event.detail.value]"><view class="field picker-field">{{ form.video_title_font_name }}</view></picker></view><view><text class="label">字号 {{ Number(form.video_title_font_size||88) }}</text><slider :value="Number(form.video_title_font_size||88)" min="48" max="140" step="1" activeColor="#d7ff68" block-size="14" @changing="form.video_title_font_size=$event.detail.value"/></view><view><text class="label">距顶部 {{ Number(form.video_title_position||10) }}%</text><slider :value="Number(form.video_title_position||10)" min="3" max="35" step="1" activeColor="#d7ff68" block-size="14" @changing="form.video_title_position=$event.detail.value"/></view><view><text class="label">黑色描边 {{ Number(form.video_title_stroke_width||4) }}</text><slider :value="Number(form.video_title_stroke_width||4)" min="0" max="8" step="0.5" activeColor="#d7ff68" block-size="14" @changing="form.video_title_stroke_width=$event.detail.value"/></view><view><text class="label">标题颜色</text><input v-model="form.video_title_primary_color" class="field color-field" maxlength="7" /></view></view>
            </view>
              </view>
            </view>
            <view class="post-accordion create-accordion">
              <view class="post-accordion-head" @click="$emit('toggle-panel','subtitle')"><view><text class="auto-run-title">口播字幕</text><text class="hint">字幕自动跟随口播内容和节奏</text></view><view class="accordion-meta"><text class="accordion-status" :class="{enabled:form.subtitle_enabled}">{{ form.subtitle_enabled ? '已开启' : '未开启' }}</text><text class="accordion-arrow">{{ createPanels.subtitle ? '▲' : '▼' }}</text></view></view>
              <view v-if="createPanels.subtitle" class="post-accordion-body">
          <checkbox-group class="auto-run-option" @change="$emit('set-subtitle-enabled',$event)"><label><checkbox value="subtitle" :checked="form.subtitle_enabled" color="#d7ff68" /><view><text class="auto-run-title">视频生成后添加字幕</text><text class="hint">自动生成与口播同步的字幕，同时保留无字幕版本。</text></view></label></checkbox-group>
            <view v-if="form.subtitle_enabled" class="subtitle-preset-bar"><view><text class="auto-run-title">视频号口播字幕</text><text class="hint">粗体 · 64号 · 黑色描边 · 距顶部73% · 单条14字</text></view><button class="ghost small" @click="$emit('apply-subtitle-preset')">应用预设</button></view>
            <view v-if="form.subtitle_enabled" class="subtitle-grid">
              <view><text class="label">字体</text><picker :range="fontOptions" @change="form.subtitle_font_name=fontOptions[$event.detail.value]"><view class="field picker-field">{{ form.subtitle_font_name }}</view></picker></view>
              <checkbox-group @change="$emit('set-subtitle-bold',$event)"><label class="compact-check subtitle-bold"><checkbox value="bold" :checked="form.subtitle_font_bold" color="#d7ff68" /><text>粗体字幕</text></label></checkbox-group>
              <view><text class="label">位置</text><view class="position-options"><button v-for="item in subtitlePositions" :key="item.value" class="position-button" :class="{active:form.subtitle_position===item.value}" @click="form.subtitle_position=item.value">{{ item.label }}</button></view></view>
              <view v-if="form.subtitle_position==='custom'"><text class="label">距顶部 {{ Number(form.subtitle_custom_position||0) }}%</text><slider :value="Number(form.subtitle_custom_position||0)" min="0" max="100" step="1" activeColor="#d7ff68" block-size="14" @changing="form.subtitle_custom_position=$event.detail.value"/></view>
              <view><text class="label">字号 {{ Number(form.subtitle_font_size||60) }}</text><slider :value="Number(form.subtitle_font_size||60)" min="20" max="100" step="1" activeColor="#d7ff68" block-size="14" @changing="form.subtitle_font_size=$event.detail.value"/></view>
              <view><text class="label">单条最大字数</text><input v-model="form.subtitle_max_chars" class="field" type="number" /></view>
              <view><text class="label">文字颜色（#RRGGBB）</text><input v-model="form.subtitle_font_color" class="field color-field" maxlength="7" /><view class="color-options"><button v-for="color in subtitleColors" :key="`form-${color}`" class="color-swatch" :class="{active:String(form.subtitle_font_color||'').toUpperCase()===color}" :style="{backgroundColor:color}" :title="color" @click="form.subtitle_font_color=color"></button></view></view>
              <view><text class="label">黑色描边 {{ Number(form.subtitle_stroke_width||0) }}</text><slider :value="Number(form.subtitle_stroke_width||0)" min="0" max="8" step="0.5" activeColor="#d7ff68" block-size="14" @changing="form.subtitle_stroke_width=$event.detail.value"/></view>
            </view>
            <checkbox-group @change="$emit('set-subtitle-background',$event)"><label class="compact-check"><checkbox value="background" :checked="form.subtitle_background_enabled" color="#d7ff68" /><text>显示半透明字幕背景</text></label></checkbox-group>
            <view v-if="form.subtitle_background_enabled" class="subtitle-grid">
              <view><text class="label">背景颜色</text><input v-model="form.subtitle_background_color" class="field color-field" maxlength="7" /></view>
              <view><text class="label">背景不透明度 {{ Number(form.subtitle_background_opacity||0) }}%</text><slider :value="Number(form.subtitle_background_opacity||0)" min="0" max="100" step="1" activeColor="#d7ff68" block-size="14" @changing="form.subtitle_background_opacity=$event.detail.value"/></view>
            </view>
              </view>
            </view>
            <view v-if="(createPanels.title||createPanels.subtitle)&&(form.video_title_enabled||form.subtitle_enabled)" class="subtitle-preview-frame"><image v-if="imagePath" class="subtitle-preview-image" :src="imagePath" mode="aspectFill"/><view class="subtitle-safe-zone">视频号界面安全区</view><view v-if="form.video_title_enabled" class="video-title-preview" :style="videoTitlePositionStyle(form)"><text v-for="(line,index) in videoTitleLines(form.video_title)" :key="`form-title-${index}`" :style="videoTitleLineStyle(form,index)">{{ line }}</text></view><view v-if="form.subtitle_enabled" class="subtitle-preview-text" :style="[subtitlePreviewStyle(form),subtitlePreviewPositionStyle(form)]"><text>{{ form.original_script.slice(0, Number(form.subtitle_max_chars||14)) || '字幕效果预览' }}</text></view></view>
          </view>
          <view class="create-section create-submit-section">
            <view class="create-section-head"><view><text class="section-index">05</text><text class="plan-title">提交检查</text></view><text class="setting-state" :class="{ok:createReady}">{{ createReady ? '可以提交' : '请补全必填项' }}</text></view>
            <view class="create-checklist"><view v-for="item in createChecks" :key="item.label" class="create-check" :class="{ok:item.ok,required:!item.ok&&item.required}"><text class="check-symbol">{{ item.ok ? '✓' : (item.required ? '!' : '•') }}</text><view><text>{{ item.label }}</text><text class="hint">{{ item.detail }}</text></view></view></view>
          <view v-if="submitting&&uploadLabel" class="upload-progress-panel"><view class="task-progress"><view :style="{width:`${uploadProgress}%`}"></view></view><text class="hint">{{ uploadLabel }} · {{ uploadProgress }}%</text></view>
          <button class="primary wide" :disabled="submitting||!createReady" @click="$emit('submit')">{{ submitting ? (uploadLabel || '正在创建任务…') : (form.auto_run ? '加入任务队列' : '创建手动任务') }}</button>
          </view>
        </view>
</template>

<script>
export default {
  name:'CreateTask',
  props:{form:{type:Object,required:true},createPanels:{type:Object,required:true},fontOptions:{type:Array,default:()=>[]},subtitleColors:{type:Array,default:()=>[]},subtitlePositions:{type:Array,default:()=>[]},scriptSummary:{type:String,default:''},createChecks:{type:Array,default:()=>[]},createReady:Boolean,imagePath:{type:String,default:''},imageFileName:{type:String,default:''},voicePath:{type:String,default:''},voiceFileName:{type:String,default:''},emotionVoicePath:{type:String,default:''},emotionVoiceFileName:{type:String,default:''},bgmPath:{type:String,default:''},bgmFileName:{type:String,default:''},submitting:Boolean,uploadLabel:{type:String,default:''},uploadProgress:{type:Number,default:0}},
  emits:['set-title','sync-script','choose-image','choose-voice','choose-emotion-voice','choose-bgm','toggle-panel','set-auto-run','set-bgm-enabled','set-bgm-ducking','set-title-enabled','set-subtitle-enabled','set-subtitle-bold','set-subtitle-background','apply-title-preset','apply-subtitle-preset','submit'],
  methods:{
    suggestVideoTitleText(script){const text=String(script||'').replace(/\s+/g,'').trim();if(!text)return '';const sentences=text.split(/[\u3002\uff01\uff1f!?]+/).filter(Boolean),first=sentences[0]||text,clauses=first.split(/[\uff0c,\uff1b;]/).map(item=>item.trim()).filter(Boolean);let lines=clauses.slice(0,3);if(lines.length===1&&lines[0].length>12){const value=lines[0];lines=[];for(let offset=0;offset<Math.min(value.length,36);offset+=12)lines.push(value.slice(offset,offset+12))}if(lines.length===1&&sentences.length>1)lines.push(...sentences.slice(1,3));return lines.slice(0,3).map(line=>line.slice(0,12)).join('\n')},
    videoTitleLines(value){const lines=[];for(const raw of String(value||'').replace(/\r/g,'').split('\n')){let line=raw.replace(/\s+/g,'');while(line&&lines.length<3){lines.push(line.slice(0,12));line=line.slice(12)}if(lines.length>=3)break}return lines.length?lines:['\u89c6\u9891\u6807\u9898']},
    videoTitlePositionStyle(value){return {top:`${Math.max(3,Math.min(35,Number(value?.video_title_position??10)))}%`}},
    videoTitleLineStyle(value){const width=Math.max(0,Number(value?.video_title_stroke_width??4)/4.5),stroke=String(value?.video_title_stroke_color||'#000000');return {display:'block',fontFamily:String(value?.video_title_font_name||'sans-serif'),fontSize:`${Math.max(14,Number(value?.video_title_font_size||88)/4.5)}px`,fontWeight:'800',lineHeight:'1.2',color:String(value?.video_title_primary_color||'#FFFFFF'),WebkitTextStroke:width?`${width}px ${stroke}`:'0',paintOrder:'stroke fill'}},
    subtitlePreviewPositionStyle(value){const position=String(value?.subtitle_position||'custom'),top=position==='top'?14:position==='center'?50:position==='bottom'?76:Number(value?.subtitle_custom_position??73);return {top:`${Math.max(5,Math.min(90,top))}%`}},
    subtitlePreviewStyle(value){const hex=String(value?.subtitle_background_color||'#000000'),alpha=Math.max(0,Math.min(1,Number(value?.subtitle_background_opacity||0)/100)),rgb=/^#[0-9a-fA-F]{6}$/.test(hex)?`${parseInt(hex.slice(1,3),16)},${parseInt(hex.slice(3,5),16)},${parseInt(hex.slice(5,7),16)}`:'0,0,0',strokeWidth=Math.max(0,Number(value?.subtitle_stroke_width??3)/4.5),stroke=String(value?.subtitle_stroke_color||'#000000');return {fontFamily:String(value?.subtitle_font_name||'sans-serif'),fontSize:`${Math.max(12,Number(value?.subtitle_font_size||64)/4.5)}px`,fontWeight:value?.subtitle_font_bold?'700':'400',color:String(value?.subtitle_font_color||'#FFFFFF'),WebkitTextStroke:strokeWidth?`${strokeWidth}px ${stroke}`:'0',paintOrder:'stroke fill',textShadow:'none',backgroundColor:value?.subtitle_background_enabled?`rgba(${rgb},${alpha})`:'transparent'}}
  }
}
</script>
