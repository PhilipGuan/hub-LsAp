<template>
  <!-- 全局布局：顶部导航 + 路由页面 -->
  <el-container class="layout">
    <el-header>
      <span class="title">多模态 RAG 问答系统</span>
      <el-menu mode="horizontal" router>
        <el-menu-item index="/documents">文档管理</el-menu-item>
        <el-menu-item index="/chat">多模态问答</el-menu-item>
      </el-menu>
    </el-header>
    <el-main><router-view /></el-main>
  </el-container>
</template>

<script setup>
// TODO(M2~M4): 布局微调
</script>
