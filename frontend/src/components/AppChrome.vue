<template>
  <view>
    <view class="topbar">
      <view class="brand"><text class="mark">DF</text><view><text class="brand-title">数字人工厂</text><text class="brand-sub">AI DIRECTED · COMFYUI POWERED</text></view></view>
      <view class="top-actions"><button class="service-status" :class="{online:health.ok}" @click="$emit('navigate','settings')"><text class="status-dot"></text>{{ healthText }}</button><button class="primary small" @click="$emit('navigate','create')">＋ 新建任务</button></view>
    </view>
    <view class="main-nav panel">
      <button v-for="item in navigation" :key="item.value" class="nav-button" :class="{active:viewMode===item.value}" @click="$emit('navigate',item.value)"><text class="nav-icon">{{ item.icon }}</text><text>{{ item.label }}</text><text v-if="item.value==='queue'&&activeTaskCount" class="nav-count">{{ activeTaskCount }}</text></button>
    </view>
  </view>
</template>

<script>
export default {
  name:'AppChrome',
  props:{health:{type:Object,default:()=>({})},healthText:{type:String,default:''},navigation:{type:Array,default:()=>[]},viewMode:{type:String,default:'create'},activeTaskCount:{type:Number,default:0}},
  emits:['navigate']
}
</script>
