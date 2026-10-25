# 设置与更新

设置页按基础连接、Telegram 登录、下载设置、版本更新、面板账号分为五个标签页。桌面端采用双列参数布局，手机端切为单列，标签栏可横向滚动。

## 下载控制

Bot 下载和频道规则下载共用数据库中的下载参数：

| 参数 | 默认值 | 行为 |
| --- | --- | --- |
| 并行任务数 | 5 | 全局共享，范围 1–32。减少时不会取消已运行任务，队列等待空位。 |
| 每任务速度上限 | 0 KiB/s | 0 不限速；每个任务分别限速。 |
| 无进度超时 | 90 秒 | 仅新增字节才刷新计时，重复上报相同进度不能延长等待。 |
| 单次下载总超时 | 0 秒 | 0 不限总时长，无进度超时仍然有效。 |
| 失败重试次数 | 3 | 首次尝试之外最多重试 3 次；重试从文件开头开始。 |
| 重试等待时间 | 3 秒 | Telegram 限流要求更长等待时遵循 Telegram。 |

并发参数立即影响队列；速度、超时和重试策略用于新开始的任务。限速产生的主动等待不算无进度，但计入单次下载总时长。下载停止产生字节后，运行速度会清零；超时后先取消旧传输再重试，最终失败会释放队列位置。获取原始消息及发送通知等前置网络请求也有超时。

API：`GET /api/download-settings`、`PUT /api/download-settings`，需要面板管理员会话。参数保存于 `state.db` 的 `download_options` 配置中，不依赖填写 Telegram API 凭据。

## 完整应用更新

版本检查统一使用 GitHub Releases 中的已发布应用包。正式版对应普通 Release，体验版对应 `bata.YYYYMMDDHHMMSS` 的预发布；允许从体验版切回正式版。保存更新偏好不立即安装，只有点击安装或已开启自动安装时才执行更新。

发布工作流构建前端后生成 `telegram-depiler-app.zip` 与 `.sha256`，其中包含后端 Python 文件、编译后的前端、VERSION 和依赖清单。下载使用已保存的 HTTP/SOCKS 代理，不修改全局 socket。安装前验证 SHA256、解压路径、必需文件、目标版本和依赖清单。依赖清单变化时要求更新 Docker 镜像。

监督进程从新包的 backend 目录启动新版本，明确设置静态资源目录，并固定原有数据和下载目录。Telegram 初始化在后台进行，不再阻塞管理页面及健康检查。启动失败时沿用原有回滚机制。容器重启后，未完成的更新任务会恢复为成功或失败状态，不会永久显示执行中。

前端资源缓存：`index.html` 与 SPA 兜底响应带 `Cache-Control: no-cache, must-revalidate`，`/assets/` 下带内容哈希的产物为 `public, max-age=31536000, immutable`，其余静态文件每次回源校验。应用更新后浏览器不会继续使用旧入口页，无需强制刷新。

**首次迁移：** 旧镜像中的监督进程和更新器不能自行修复自身。该改动发布为新镜像后，需要在宿主机执行一次 `docker compose pull && docker compose up -d`。挂载原来的 `/app/data`，保留登录会话、规则和记录；后续依赖兼容的版本可用应用内更新。源码部署可以检查版本，更新代码及重启由部署者完成。

验证命令：

```powershell
python -m unittest discover -s backend/tests -v
cd frontend
npm test
npm run type-check
npm run build
```

## 规则诊断（只读）

规则看起来"不下载"时，用 `POST /api/group-rules/{rule_id}/diagnose?limit=50`（需要面板管理员会话）扫描该规则最近的消息，逐条返回媒体类型、文件名、扩展名、大小，以及会不会被下载、被哪一步拒绝：

| `reason` 分类 | 含义 |
| --- | --- |
| `extension` | 扩展名不在规则白名单内 |
| `size` | 文件大小超出规则的上下限 |
| `keyword` | 关键词匹配失败 |
| `no_media` / `no_text` | 消息没有媒体，或文本规则没有文本 |
| `comment` | 是评论/回复，但规则未启用评论下载 |
| `duplicate` | 全局去重命中：已有相同 Telegram 文件的任务，按规则下载会被跳过 |
| `error` | 规则判断过程抛异常 |

诊断与真实下载共用同一份判断逻辑（`_rule_rejection_reason`），结果不会漂移。它不创建下载记录、不推进 `last_seen_message_id`、不占用并发位、不消耗限额，可反复调用。返回里的 `notes` 会点明 monitor 规则不回补历史消息、扩展名白名单、限额未参与判断等前提；`items[].below_last_seen` 标出该消息是否早于游标（monitor 规则不会处理）。
