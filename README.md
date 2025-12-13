<p align="center">
  <img src="frontend/public/images/logo2.png" alt="Telegram Depiler Logo" width="500" />
</p>

# Telegram Depiler

[![Version](https://img.shields.io/badge/version-1.0.5-blue)](https://github.com/APecme/Telegram_Depiler/releases/tag/v1.0.5)
[![Docker Image](https://img.shields.io/docker/v/apecme/telegram-depiler?label=Docker&logo=docker)](https://hub.docker.com/r/apecme/telegram-depiler)
[![Docker Pulls](https://img.shields.io/docker/pulls/apecme/telegram-depiler)](https://hub.docker.com/r/apecme/telegram-depiler)
[![GitHub Stars](https://img.shields.io/github/stars/APecme/Telegram_Depiler)](https://github.com/APecme/Telegram_Depiler)
[![License](https://img.shields.io/github/license/APecme/Telegram_Depiler)](LICENSE)

🚀 强大的 Telegram 自动下载管理器 - 支持群聊监控、Bot 交互、Web 管理界面

> **最新版本 v1.0.5** - 简化Docker标签策略 🎉

## 一键部署

```bash
docker run -d \
  --name telegram-depiler \
  -p 8000:8000 \
  -v ./data:/app/data \
  -v ./downloads:/downloads \
  apecme/telegram-depiler:latest
```

访问 http://localhost:8000 开始使用

## ✨ 功能特性

### 核心功能
- 📥 **智能下载管理** - 自动下载 Telegram 群聊中的媒体文件
- 🤖 **Bot 命令处理** - 通过 Bot 接收和管理下载任务
- 🌐 **Web 管理界面** - 现代化的 React + TypeScript 前端界面
- 📊 **实时监控** - 下载进度实时更新和状态查看
- 🎯 **规则引擎** - 灵活的群聊下载规则配置

### 高级特性
- ⏸️ **下载控制** - 暂停、删除下载任务
- ⭐ **优先级管理** - 设置下载任务优先级
- 🔍 **智能过滤** - 支持文件类型、大小、关键词过滤
- 📝 **文件命名** - 自定义文件名模板
- 🔔 **Bot 通知** - 下载状态实时推送到 Telegram
- � **二步验证** - 支持 Telegram 二步验证登录

## 🚀 快速开始

### 方式一：使用 Docker Hub 镜像（推荐）

```bash
# 1. 创建 docker-compose.yml
curl -o docker-compose.yml https://raw.githubusercontent.com/APecme/Telegram_Depiler/main/docker-compose.yml

# 2. 修改 docker-compose.yml，使用 Docker Hub 镜像
# 将 build 部分替换为：
# image: apecme/telegram-depiler:latest

# 3. 启动服务
docker compose up -d

# 4. 访问 http://localhost:8000
```

### 方式二：从源码构建

#### 克隆项目
```bash
git clone https://github.com/APecme/Telegram_Depiler.git
cd Telegram_Depiler
```

### 🐳 Docker Compose 部署详解

#### Docker Compose 配置说明

项目的 `docker-compose.yml` 配置如下：

```yaml
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: telegram-manager
    volumes:
      - ./downloads:/downloads      # 下载文件存储
      - ./data:/app/data            # 配置和数据库
    ports:
      - "8000:8000"                 # Web 界面端口
    network_mode: bridge
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

## 📖 使用指南

### 初始配置

#### 第一步：访问 Web 界面
打开浏览器访问 http://localhost:8000

#### 第二步：配置 Telegram API
1. 点击右上角的**设置**图标进入设置页面
2. 获取 Telegram API 凭据：
   - 访问 https://my.telegram.org
   - 登录你的 Telegram 账号
   - 进入 "API development tools"
   - 创建应用获取 `api_id` 和 `api_hash`
3. 在设置页面填入：
   - **API ID**: 你的 api_id（纯数字）
   - **API Hash**: 你的 api_hash（32位字符串）
   - **手机号**: 国际格式，如 +8613800138000
4. **代理配置**（可选）：
   - 如果需要代理访问 Telegram，填入代理信息
   - 支持类型：HTTP / SOCKS5
   - 填入代理主机、端口、用户名和密码（如需要）
5. 点击**保存配置**

#### 第三步：登录 Telegram 账号
1. 在设置页面点击**发送验证码**
2. 在 Telegram 客户端查看收到的验证码
3. 在页面输入验证码并提交
4. 如果账号启用了二步验证：
   - 页面会提示输入密码
   - 输入你的二步验证密码
5. 登录成功后，页面会显示账号信息

### Bot 配置

1. **创建 Bot**
   - 在 Telegram 中找到 @BotFather
   - 发送 `/newbot` 创建新 Bot
   - 获取 Bot Token 和 Username

2. **配置 Bot**
   - 在设置页面填入 Bot Token 和 Username
   - 设置管理员用户 ID（多个用逗号分隔）
   - 点击保存配置

3. **启动 Bot**
   - 在主页点击"启动 Bot"按钮
   - Bot 会自动开始监听群聊消息

### 创建下载规则

#### 通过 Web 界面
1. 在主页点击"创建规则"
2. 选择目标群聊
3. 配置过滤条件：
   - **模式**: 监控新消息 / 下载历史消息
   - **文件类型**: 视频、图片、音频、文档等
   - **最小文件大小**: 过滤小文件
   - **关键词**: 包含/排除特定关键词
   - **时间范围**: 设置下载时间段（可选）
4. 保存规则

### 下载管理

#### Web 界面控制
在下载列表中，每个任务都有操作按钮：
- **⏸️ 暂停** - 暂停正在下载的任务
- **⭐ 置顶** - 设置高优先级（优先下载）
- **🗑️ 删除** - 删除任务和已下载的文件

## 🎨 功能详解

### 文件命名模板

支持以下变量：
- `{task_id}` - 下载任务 ID
- `{message_id}` - 消息 ID
- `{chat_title}` - 群聊标题
- `{timestamp}` - 时间戳
- `{file_name}` - 原始文件名

示例：`{task_id}_{chat_title}_{file_name}`

### 规则匹配模式

- **全部匹配** - 下载所有文件
- **包含关键词** - 仅下载文件名包含指定关键词的文件
- **排除关键词** - 排除文件名包含指定关键词的文件

### 文件类型过滤

支持的文件扩展名过滤，例如：
- 📹 **视频**: mp4, mkv, avi, mov
- 🖼️ **图片**: jpg, jpeg, png, gif, webp
- 🎵 **音频**: mp3, flac, wav, m4a
- 📄 **文档**: pdf, zip, rar, doc, docx

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情


## ⚠️ 免责声明

本工具仅供学习和研究使用，请遵守 Telegram 服务条款和当地法律法规。使用本工具下载的内容，用户需自行承担相应责任。请勿用于非法用途。

---

**Telegram Manager** - 让 Telegram 文件管理更简单 ✨
