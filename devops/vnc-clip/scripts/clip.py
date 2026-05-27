#!/usr/bin/env python3
"""
VNC Clip — 用 VNC Chrome + Obsidian Web Clipper 扩展自动化剪藏网页。
CDP 导航 + 扩展 Service Worker 触发保存 + 自定义 frontmatter。
"""
import asyncio, json, sys, os, re, urllib.request
import websockets
from datetime import datetime

CDP = "http://127.0.0.1:9222"
EXT_ID = "cnjifjpddelmedmihgijeibhnjfabmlf"
CLIPPING_DIR = "/home/wangsiji/projects/wsj-second-brain/Inbox/LLM-WiKi/Raw/Clippings"

async def cdp_list():
    return json.loads(urllib.request.urlopen(f"{CDP}/json").read())

async def get_ws():
    for t in await cdp_list():
        if t["type"] == "page" and not t["url"].startswith(("devtools://", "chrome://")):
            return t["webSocketDebuggerUrl"]
    return None

async def get_ext_ws():
    for t in await cdp_list():
        if EXT_ID in t.get("url", "") and t["type"] == "service_worker":
            return t["webSocketDebuggerUrl"]
    return None

async def cdp(ws, method, params=None):
    msg = {"id": 1, "method": method}
    if params: msg["params"] = params
    await ws.send(json.dumps(msg))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == 1: return resp

async def eval_js(ws, expr):
    r = await cdp(ws, "Runtime.evaluate", {
        "expression": expr, "returnByValue": True, "awaitPromise": True
    })
    v = r.get("result", {}).get("result", {})
    return None if v.get("subtype") == "error" else v.get("value")

async def wait_load(ws):
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("method") == "Page.loadEventFired": return

def extract_author_handle(url):
    """Extract X/Twitter handle from URL."""
    m = re.search(r'x\.com/([^/]+)/status/', url) or re.search(r'twitter\.com/([^/]+)/status/', url)
    return f"@{m.group(1)}" if m else None

def rewrite_frontmatter(filepath, meta):
    """Read file, replace frontmatter with custom format."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Remove existing frontmatter (between --- markers)
    body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, count=1, flags=re.DOTALL)
    body = body.strip()
    
    title = meta.get('title', '').strip().strip('"')
    source = meta.get('source', '')
    author_handle = meta.get('author_handle')
    published = meta.get('published', '')
    created = meta.get('created', datetime.now().strftime('%Y-%m-%d'))
    description = meta.get('description', '').strip().strip('"').replace('"', '\\"')
    
    lines = ['---']
    if title:
        lines.append(f'title: "{title}"')
    if source:
        lines.append(f'source: "{source}"')
    if author_handle:
        lines.append('author:')
        lines.append(f'  - "[[{author_handle}]]"')
    if published:
        lines.append(f'published: {published}')
    lines.append(f'created: {created}')
    if description:
        # Truncate long descriptions
        if len(description) > 200:
            description = description[:197] + '...'
        lines.append(f'description: "{description}"')
    lines.append('tags:')
    lines.append('  - "clippings"')
    lines.append('---')
    lines.append('')
    lines.append(body)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

async def clip(url):
    # ---------------------------------------------------------------
    # Step 1: Navigate to article via CDP
    # ---------------------------------------------------------------
    page_ws = await get_ws()
    if not page_ws:
        print("❌ No page tab found. Is Chrome CDP running?", file=sys.stderr)
        return 1

    async with websockets.connect(page_ws, max_size=20*1024*1024) as ws:
        await cdp(ws, "Page.enable")
        await cdp(ws, "Runtime.enable")
        await cdp(ws, "Network.enable")

        # Inject X/Twitter cookies
        if 'x.com' in url or 'twitter.com' in url:
            sess_file = os.path.expanduser("~/.config/xfetch/session.json")
            if os.path.exists(sess_file):
                with open(sess_file) as f:
                    s = json.load(f)
                for name, value in [("auth_token", s.get("authToken", "")),
                                   ("ct0", s.get("ct0", "")),
                                   ("guest_id", "v1%3A1723456789")]:
                    await cdp(ws, "Network.setCookie", {
                        "name": name, "value": value, "domain": ".x.com",
                        "path": "/", "secure": True, "httpOnly": name == "auth_token"
                    })

        print(f"🌐 Opening: {url}")
        await cdp(ws, "Page.navigate", {"url": url})
        await wait_load(ws)
        await asyncio.sleep(5)

        title = await eval_js(ws, "document.title")
        print(f"   Title: {title}")

        # Extract metadata for frontmatter
        print(f"📋 Extracting metadata...")
        meta_raw = await eval_js(ws, """
        JSON.stringify({
            title: (document.querySelector('meta[property=\"og:title\"]') || {}).content
                || (document.querySelector('meta[name=\"twitter:title\"]') || {}).content
                || document.title || '',
            description: (document.querySelector('meta[name=\"description\"]') || {}).content
                || (document.querySelector('meta[property=\"og:description\"]') || {}).content
                || (document.querySelector('meta[name=\"twitter:description\"]') || {}).content
                || '',
            published: (document.querySelector('meta[property=\"article:published_time\"]') || {}).content
                || (document.querySelector('meta[name=\"date\"]') || {}).content
                || '',
            author_name: (document.querySelector('meta[name=\"twitter:creator\"]') || {}).content
                || (document.querySelector('meta[name=\"author\"]') || {}).content
                || '',
            site_name: (document.querySelector('meta[property=\"og:site_name\"]') || {}).content || ''
        })
        """)

        meta = json.loads(meta_raw) if meta_raw else {}

    # ---------------------------------------------------------------
    # Step 2: Connect to extension Service Worker, trigger save
    # ---------------------------------------------------------------
    ext_ws = await get_ext_ws()
    if not ext_ws:
        print("❌ Extension service worker not found", file=sys.stderr)
        return 1

    async with websockets.connect(ext_ws, max_size=10*1024*1024) as sw:
        await cdp(sw, "Runtime.enable")

        tab_info = await eval_js(sw, f"""
        (async function() {{
            try {{
                var tabs = await chrome.tabs.query({{}});
                for (var t of tabs) {{
                    if ((t.url || '') === {json.dumps(url)}) {{
                        return JSON.stringify({{tabId: t.id, url: t.url}});
                    }}
                }}
                for (var t of tabs) {{
                    var u = t.url || '';
                    if (u.startsWith('http') && !u.startsWith('chrome-extension://')) {{
                        return JSON.stringify({{tabId: t.id, url: u}});
                    }}
                }}
                return null;
            }} catch(e) {{ return null; }}
        }})()
        """)

        if not tab_info:
            print("❌ Could not find target tab", file=sys.stderr)
            return 1

        data = json.loads(tab_info)
        tab_id = data["tabId"]

        before = set(os.listdir(CLIPPING_DIR)) if os.path.isdir(CLIPPING_DIR) else set()

        print(f"📝 Saving via Web Clipper...")
        result = await eval_js(sw, f"""
        (async function() {{
            try {{
                try {{ await chrome.tabs.sendMessage({tab_id}, {{action: "ping"}}); }}
                catch(e) {{
                    if (chrome.scripting) {{
                        await chrome.scripting.executeScript({{
                            target: {{tabId: {tab_id}}}, files: ['content.js']
                        }});
                    }}
                }}
                var r = await chrome.tabs.sendMessage({tab_id}, {{action: "saveMarkdownToFile"}});
                return JSON.stringify(r);
            }} catch(e) {{ return JSON.stringify({{error: e.message}}); }}
        }})()
        """)

        await asyncio.sleep(4)

        after = set(os.listdir(CLIPPING_DIR)) if os.path.isdir(CLIPPING_DIR) else set()
        new_files = after - before

        if not new_files:
            print(f"❌ No files created. Result: {result}", file=sys.stderr)
            return 1

        # ---------------------------------------------------------------
        # Step 3: Post-process - rewrite frontmatter
        # ---------------------------------------------------------------
        print(f"✏️  Rewriting frontmatter...")
        for fname in sorted(new_files):
            fpath = os.path.join(CLIPPING_DIR, fname)
            size = os.path.getsize(fpath)

            # Build metadata for frontmatter
            meta_data = {
                'title': re.sub(r'^X 上的 | / X$| \| X$', '', meta.get('title', '')).strip(),
                'source': url,
                'author_handle': meta.get('author_name') or extract_author_handle(url),
                'published': meta.get('published', '')[:10] if meta.get('published') else '',
                'created': datetime.now().strftime('%Y-%m-%d'),
                'description': meta.get('description', ''),
            }

            rewrite_frontmatter(fpath, meta_data)
            new_size = os.path.getsize(fpath)
            print(f"✅ {fname} ({new_size} bytes)")

    return 0

if __name__ == "__main__":
    if len(sys.argv) < 2:
        url = "https://x.com/rwayne/status/2059274464622469575"
        print(f"Usage: clip.py <URL>\nDemo: {url}\n")
    else:
        url = sys.argv[1]
    sys.exit(asyncio.run(clip(url)))
