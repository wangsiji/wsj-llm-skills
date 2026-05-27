---
name: vnc-clip
description: "使用 VNC Chrome + Obsidian Web Clipper 扩展自动化剪藏网页到 Obsidian 的 Clippings 目录"
category: devops
triggers:
  - 剪藏
  - clip
  - 保存文章
  - save article
  - vnc
  - web clipper
---

# VNC Clip — 自动化网页剪藏

用 Xvfb + openbox + Chrome CDP + Obsidian Web Clipper 扩展，将任意网页保存为 markdown 到指定 Clippings 目录。

## 前置依赖

- Xvfb :2（1920×1080x24）已运行
- openbox 窗口管理器已运行
- Chrome with `--remote-debugging-port=9222`（使用非默认 user-data-dir）
- Obsidian Web Clipper 扩展 `cnjifjpddelmedmihgijeibhnjfabmlf` 已安装
- Web Clipper 已配置为 "Save file" 模式，保存路径指向 Clippings 目录
- X.com 需要登录态（auth_token + ct0 在 `~/.config/xfetch/session.json`）

## 脚本文件

- `scripts/clip.py` — 主脚本，CDP 导航 + 扩展 SW 触发保存

## 启动 VNC 环境

如果 Xvfb / openbox / Chrome CDP 未运行，用以下命令启动：

```bash
# 1. Xvfb
Xvfb :2 -screen 0 1920x1080x24 &

# 2. openbox（窗口管理器）
DISPLAY=:2 openbox --replace &

# 3. x11vnc（VNC 服务）
x11vnc -display :2 -forever -shared -rfbport 5900 -rfbauth ~/.vnc/passwd &

# 4. websockify（noVNC）
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 &

# 5. Chrome CDP（用独立 profile）
DISPLAY=:2 google-chrome \
  --remote-debugging-port=9222 \
  --no-first-run --disable-gpu --disable-software-rasterizer \
  --user-data-dir=/home/wangsiji/.config/chrome-cdp
```

验证：
```bash
curl -s http://127.0.0.1:9222/json/version | python3 -c "import json,sys; print(json.load(sys.stdin)['Browser'])"
# 应返回：Chrome/145.0.7632.75
```

## 使用

```bash
# 剪藏一条链接
python3 ~/.hermes/skills/vnc-clip/scripts/clip.py <URL>

# 示例
python3 ~/.hermes/skills/vnc-clip/scripts/clip.py https://x.com/rwayne/status/2059274464622469575

# 普通网页
python3 ~/.hermes/skills/vnc-clip/scripts/clip.py https://paulgraham.com/greatwork.html
```

输出文件保存到 Clippings 目录，自动替换 frontmatter 为以下格式：

```yaml
---
title: "文章标题"
source: "https://..."
author:
  - "[[@handle]]"    # X.com 自动从 URL 提取
published: 2026-05-24  # 如有 meta 信息
created: 2026-05-27     # 剪藏日期
description: "..."      # 如有 meta 信息
tags:
  - "clippings"
---
```

## 剪藏后：确保文件同步到手机

剪藏保存的是服务器本地文件，要同步到手机需要通过 Syncthing 或 WebDAV。如果手机收不到新文件，触发一次 Syncthing 扫描：

```bash
curl -X POST "http://127.0.0.1:8384/rest/db/scan?folder=obsidian-vault" \
  -H "X-API-Key: m4HTqYUfEetb7gMjJk7nmeKzhe7Qq9fa"
```

更全面的健康检查：
```bash
bash ~/.hermes/skills/devops/syncthing-setup/scripts/syncthing-status.sh
```

## 工作原理

### 核心流程

```
1. CDP Page.navigate → 在 Chrome 中打开目标 URL
   ├── X.com 类站点 → 注入 auth_token + ct0 cookie
   └── 普通站点 → 直接导航

2. CDP 连接到扩展的 Service Worker
   ├── chrome.tabs.query() → 找到目标页面的 tabId
   ├── chrome.tabs.sendMessage(tabId, {action: "ping"}) → 确保 content script 已加载
   └── chrome.tabs.sendMessage(tabId, {action: "saveMarkdownToFile"}) → 触发保存

3. Content script 执行：
   ├── flattenShadowDom() → 展平 Shadow DOM
   ├── parseForClip(document) → 用 Defuddle 提取内容
   ├── createMarkdownContent() → 转成 markdown
   └── saveFile() → 通过 <a download> 触发下载
```

### 技术选型

- **CDP** (Chrome DevTools Protocol)：通过 WebSocket 直接控制 Chrome，无需视觉反馈
- **扩展 Service Worker**：直接调用扩展 API，绕过"需要用户手势"的安全限制
- **openbox**：轻量窗口管理器，让 Xvfb 下的窗口管理正常（之前因缺少 WM 导致 xdotool 无法激活窗口）
- **快捷键方案放弃原因**：`xdotool` 在 Xvfb 下无法可靠地将键盘事件传递给 Chrome 扩展，而 `chrome.action.openPopup()` 要求用户手势

## 文件结构

```
~/.hermes/skills/vnc-clip/
├── SKILL.md           ← 本文档
└── scripts/
    └── clip.py        ← 主剪藏脚本
```

## 排错

| 症状 | 原因 | 解决 |
|------|------|------|
| `Extension SW not found!` | Chrome 刚启动，扩展未加载 | 等待 5-10 秒重试 |
| `Cannot access a chrome:// URL` | 找到了错误 tab（如 bookmarks） | 脚本会自动跳过 chrome:// 页面 |
| `Could not find an active browser window` | Xvfb 下没有"活跃窗口"概念 | 脚本不使用 `openPopup()`，改用 SW 直接发消息 |
| 文件没保存到 Clippings 目录 | Web Clipper 配置丢失 | 检查 Chrome 扩展设置中的保存路径 |
| X.com 内容少或空白 | Cookie 过期 | 刷新 `~/.config/xfetch/session.json` 中的 token |
