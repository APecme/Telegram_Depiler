# 发布到 GitHub 和 Docker Hub 指南

## 准备工作

### 1. 安装 Git
- 下载：https://git-scm.com/download/win
- 安装后重启终端

### 2. 注册账号
- **GitHub**: https://github.com/signup
- **Docker Hub**: https://hub.docker.com/signup

## 发布步骤

### 第一步：上传到 GitHub

#### 1. 初始化 Git 仓库
```bash
cd C:\Users\apecm\Desktop\Telegram_Depiler
git init
git add .
git commit -m "Initial commit"
```

#### 2. 在 GitHub 创建仓库
1. 访问 https://github.com/new
2. 仓库名：`Telegram_Depiler`
3. 描述：`🚀 强大的 Telegram 自动下载管理器 - 支持群聊监控、Bot 交互、Web 管理界面`
4. 选择 Public（公开）
5. 不要勾选任何初始化选项
6. 点击 Create repository

#### 3. 推送代码到 GitHub
```bash
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/Telegram_Depiler.git
git push -u origin main
```

### 第二步：配置 Docker Hub 自动构建

#### 1. 创建 Docker Hub Access Token
1. 登录 https://hub.docker.com
2. 点击右上角头像 → Account Settings
3. 进入 Security → New Access Token
4. Token 名称：`github-actions`
5. 权限：Read, Write, Delete
6. 复制生成的 Token（只显示一次）

#### 2. 在 GitHub 配置 Secrets
1. 进入你的 GitHub 仓库
2. Settings → Secrets and variables → Actions
3. 点击 New repository secret
4. 添加两个 Secret：
   - Name: `DOCKERHUB_USERNAME`，Value: 你的 Docker Hub 用户名
   - Name: `DOCKERHUB_TOKEN`，Value: 刚才复制的 Token

#### 3. 触发自动构建
推送代码到 GitHub 后，GitHub Actions 会自动：
- 构建 Docker 镜像
- 推送到 Docker Hub
- 支持 linux/amd64 和 linux/arm64 架构

查看构建状态：
- GitHub 仓库 → Actions 标签页

### 第三步：发布版本

#### 创建版本标签
```bash
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0
```

这会触发构建并推送以下镜像标签：
- `YOUR_USERNAME/telegram-depiler:latest`
- `YOUR_USERNAME/telegram-depiler:v1.0.0`
- `YOUR_USERNAME/telegram-depiler:1.0`
- `YOUR_USERNAME/telegram-depiler:1`

## 用户使用方式

### 方式一：直接使用 Docker Hub 镜像

用户只需创建 `docker-compose.yml`：

```yaml
services:
  app:
    image: YOUR_USERNAME/telegram-depiler:latest
    container_name: telegram-depiler
    volumes:
      - ./downloads:/app/downloads
      - ./data:/app/data
    ports:
      - "8000:8000"
    restart: unless-stopped
```

然后运行：
```bash
docker compose up -d
```

### 方式二：从源码构建

```bash
git clone https://github.com/YOUR_USERNAME/Telegram_Depiler.git
cd Telegram_Depiler
docker compose up -d
```

## 更新镜像

### 推送新版本
```bash
# 修改代码后
git add .
git commit -m "Update: 描述更新内容"
git push

# 发布新版本
git tag -a v1.0.1 -m "Release version 1.0.1"
git push origin v1.0.1
```

### 用户更新镜像
```bash
docker compose pull
docker compose up -d
```

## 在 README 中添加徽章

在 README.md 顶部添加：

```markdown
[![Docker Image](https://img.shields.io/docker/v/YOUR_USERNAME/telegram-depiler?label=Docker&logo=docker)](https://hub.docker.com/r/YOUR_USERNAME/telegram-depiler)
[![Docker Pulls](https://img.shields.io/docker/pulls/YOUR_USERNAME/telegram-depiler)](https://hub.docker.com/r/YOUR_USERNAME/telegram-depiler)
[![GitHub Stars](https://img.shields.io/github/stars/YOUR_USERNAME/Telegram_Depiler)](https://github.com/YOUR_USERNAME/Telegram_Depiler)
[![License](https://img.shields.io/github/license/YOUR_USERNAME/Telegram_Depiler)](LICENSE)
```

## 注意事项

1. **替换占位符**：
   - `YOUR_USERNAME` → 你的 GitHub 用户名
   - `YOUR_DOCKERHUB_USERNAME` → 你的 Docker Hub 用户名

2. **保护敏感信息**：
   - 不要提交 `.env` 文件
   - 不要提交 `data/` 目录
   - 已在 `.gitignore` 中配置

3. **网络问题**：
   - 如果推送失败，配置 Git 代理：
     ```bash
     git config --global http.proxy http://127.0.0.1:7890
     git config --global https.proxy http://127.0.0.1:7890
     ```

4. **构建时间**：
   - 首次构建约需 5-10 分钟
   - 后续构建利用缓存会更快

## 分享链接

发布后，你可以分享：
- **GitHub 仓库**: `https://github.com/YOUR_USERNAME/Telegram_Depiler`
- **Docker Hub**: `https://hub.docker.com/r/YOUR_USERNAME/telegram-depiler`
- **一键部署**: 
  ```bash
  docker run -d -p 8000:8000 -v ./data:/app/data -v ./downloads:/app/downloads YOUR_USERNAME/telegram-depiler:latest
  ```
