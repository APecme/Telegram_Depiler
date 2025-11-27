Param(
  [switch]$Force  # 跳过未提交检查或版本不一致时的确认
)

$ErrorActionPreference = "Stop"

Write-Host "=== Telegram Depiler Release Script ===" -ForegroundColor Cyan

# 基本检查
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Error "git 命令未找到，请先安装 Git 并确保其在 PATH 中。"
  exit 1
}

if (-not (Test-Path ".git")) {
  Write-Error "当前目录不是 Git 仓库，请在项目根目录下运行 release.ps1。"
  exit 1
}

# 检查是否有未提交修改
$status = git status --porcelain
if ($status -ne "" -and -not $Force) {
  Write-Warning "工作区存在未提交的修改："
  git status
  $answer = Read-Host "继续发布（仅基于当前 HEAD 创建 tag）？(y/N)"
  if ($answer -notin @('y','Y')) {
    Write-Host "已取消发布。"
    exit 1
  }
}

# 从 VERSION 读取版本
$versionFile = "VERSION"
if (-not (Test-Path $versionFile)) {
  Write-Error "未找到 $versionFile 文件。请确保版本号写在 VERSION 文件中。"
  exit 1
}

$version = (Get-Content $versionFile -Raw).Trim()
if (-not $version) {
  Write-Error "VERSION 文件内容为空。"
  exit 1
}

Write-Host "VERSION 文件中的版本号: $version" -ForegroundColor Green

# 可选：对比 frontend/package.json 中的 version
$packageJsonPath = "frontend/package.json"
if (Test-Path $packageJsonPath) {
  try {
    $pkgJson = Get-Content $packageJsonPath -Raw | ConvertFrom-Json
    $pkgVersion = $pkgJson.version
    if ($pkgVersion -and $pkgVersion -ne $version) {
      Write-Warning "VERSION ($version) 与 frontend/package.json 中的 version ($pkgVersion) 不一致。"
      if (-not $Force) {
        $ans = Read-Host "仍然使用 VERSION=$version 发布？(y/N)"
        if ($ans -notin @('y','Y')) {
          Write-Host "已取消发布。"
          exit 1
        }
      }
    }
  }
  catch {
    Write-Warning "解析 frontend/package.json 失败: $_"
  }
}

# 生成 tag 名字
$tag = "v$version"
Write-Host "将要创建的 Git tag: $tag" -ForegroundColor Yellow

# 检查本地是否已有同名 tag
$localTagExists = git tag -l $tag
if ($localTagExists) {
  Write-Error "本地已存在 tag $tag，请修改 VERSION 或删除已有 tag。"
  exit 1
}

# 检查远程是否已有同名 tag
$remoteTagExists = git ls-remote --tags origin $tag
if ($remoteTagExists) {
  Write-Error "远程 origin 上已存在 tag $tag，请修改 VERSION 或清理远程 tag。"
  exit 1
}

# 创建并推送 tag（不推送 main 分支）
Write-Host "正在创建本地 tag $tag..." -ForegroundColor Cyan
git tag -a $tag -m "Release $tag"

Write-Host "正在推送 tag $tag 到 origin（不会推送 main 分支）..." -ForegroundColor Cyan
git push origin $tag

Write-Host "发布完成: $tag" -ForegroundColor Green
Write-Host "如果 docker-publish.yml 配置了 on.push.tags: 'v*.*.*'，将会自动触发 Docker Build and Push workflow。"