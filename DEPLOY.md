# Telegram Manager 部署说明

## 快速部署

### 1. 启动服务

```bash
docker compose up -d
```

### 2. 访问应用

打开浏览器访问：http://localhost:8000

### 3. 配置应用

1. 点击右上角设置图标
2. 填入 Telegram API 凭据（从 https://my.telegram.org 获取）
3. 配置 Bot Token（从 @BotFather 获取）
4. 完成登录验证

## 数据持久化

所有配置和数据保存在以下目录：
- `./data/` - 数据库和会话文件
- `./downloads/` - 下载的文件

## 常用命令

```bash
# 查看日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 重新构建
docker compose up -d --build
```

## 注意事项

1. 首次启动会自动创建 `data` 和 `downloads` 目录
2. 所有配置通过 Web 界面完成，无需手动编辑配置文件
3. 配置保存在 `data/state.db` 数据库中
4. 如需使用代理，在设置页面配置，宿主机代理地址使用 `host.docker.internal:端口`
