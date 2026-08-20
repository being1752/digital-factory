<template>
  <view>
    <view v-if="sidebar" class="sidebar panel">
      <view class="section-head"><text>项目库</text><text class="refresh" @click="$emit('refresh')">↻</text></view>
      <view v-if="!projects.length" class="empty">还没有项目</view>
      <view v-for="item in projects" :key="item.id" class="project-item" :class="{active:currentId===item.id}" @click="$emit('open',item.id)"><image v-if="item.has_image" class="project-thumb-small" :src="thumbnailUrl(item)" mode="aspectFill" lazy-load/><view class="project-info"><text class="project-name">{{ item.title }}</text><text class="project-meta">{{ statusName(item.status) }} · {{ item.id }}</text></view><button class="project-delete" @click.stop="$emit('delete',item)">删除</button></view>
    </view>
    <view v-else class="project-library"><view class="page-heading"><view><text class="eyebrow">LIBRARY</text><text class="page-title">项目库</text><text class="lead">查看已完成项目或继续编辑生产中的项目。</text></view><button class="primary small" @click="$emit('create')">＋ 新建任务</button></view><view v-if="!projects.length" class="panel page-empty"><text class="empty-title">还没有项目</text><text class="hint">完成第一次任务提交后，项目会出现在这里。</text></view><view v-else class="project-grid"><view v-for="item in projects" :key="item.id" class="panel project-card" @click="$emit('open',item.id)"><view class="project-card-cover"><image v-if="item.has_image" class="project-card-image" :src="thumbnailUrl(item)" mode="aspectFill" lazy-load/><text v-else>DF</text></view><view class="project-card-copy"><text class="task-card-title">{{ item.title }}</text><text class="hint">{{ statusName(item.status) }}</text><text class="project-id">{{ item.id }}</text></view><button class="project-delete" @click.stop="$emit('delete',item)">删除</button></view></view></view>
  </view>
</template>

<script>
import { fileUrl } from '../utils/api.js'
export default {name:'ProjectBrowser',props:{projects:{type:Array,default:()=>[]},currentId:{type:String,default:''},sidebar:Boolean,statusName:{type:Function,required:true}},emits:['refresh','create','open','delete'],methods:{thumbnailUrl(item){return fileUrl(`/api/projects/${item.id}/thumbnail?v=${encodeURIComponent(item.thumbnail_version||'')}`)}}}
</script>
