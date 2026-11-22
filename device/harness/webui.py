"""Embedded single-page UI for the on-device web console."""

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>ESP32 Harness</title>
<link rel="stylesheet" href="/app.css">
</head>
<body>
<div id="app">
  <section id="loginView" class="view auth-view" hidden>
    <div class="auth-card">
      <div class="auth-brand">
        <span class="brand-mark" aria-hidden="true">H</span>
        <div class="auth-brand-text">
          <h1>ESP32 Harness</h1>
          <p class="auth-tagline">设备端智能体控制台</p>
        </div>
      </div>
      <p class="muted auth-copy">登录后即可对话、执行 / 命令和修改设备配置。</p>
      <p id="loginNotice" class="hint" hidden></p>
      <form id="loginForm" class="auth-form">
        <label class="field">管理密码<input id="loginPassword" type="password" autocomplete="current-password" required></label>
        <button type="submit" class="btn-block">登录</button>
        <p id="loginError" class="error" hidden></p>
      </form>
      <p class="auth-foot">仅限可信局域网使用 · 请勿映射到公网</p>
    </div>
  </section>

  <section id="setupView" class="view auth-view" hidden>
    <div class="auth-card auth-card-wide">
      <div class="auth-brand">
        <span class="brand-mark" aria-hidden="true">H</span>
        <div class="auth-brand-text">
          <h1>首次设置</h1>
          <p class="auth-tagline">连接设备 AP，配置网络与模型</p>
        </div>
      </div>
      <p id="setupHint" class="muted auth-copy"></p>
      <form id="setupForm" class="auth-form">
        <section class="form-section">
          <h2 class="section-title">管理密码</h2>
          <p class="hint">至少 8 位，用于登录网页控制台。</p>
          <label class="field">管理密码（至少 8 位）<input id="setupPassword" type="password" minlength="8" autocomplete="new-password" required></label>
          <label class="field">重复管理密码<input id="setupConfirm" type="password" minlength="8" autocomplete="new-password" required></label>
        </section>
        <section class="form-section">
          <h2 class="section-title">Wi-Fi</h2>
          <p class="hint">ESP32-S3 仅支持 2.4 GHz 网络。</p>
          <label class="field">SSID<input id="setupSsid" autocomplete="off" placeholder="家庭 Wi-Fi 名称"></label>
          <label class="field">密码<input id="setupWifiPassword" type="password" autocomplete="off" placeholder="留空表示开放网络"></label>
        </section>
        <section class="form-section">
          <h2 class="section-title">模型 <span class="badge soft">可稍后设置</span></h2>
          <label class="field">API 格式<select id="setupProvider">
            <option value="openai">OpenAI 兼容（chat/completions）</option>
            <option value="anthropic">Anthropic Messages</option>
          </select></label>
          <label class="field">Base URL<input id="setupBaseUrl" placeholder="https://api.deepseek.com"></label>
          <label class="field">Model<input id="setupModel" placeholder="deepseek-flash"></label>
          <label class="field">API Key<input id="setupApiKey" type="password" autocomplete="off"></label>
        </section>
        <button type="submit" class="btn-block">保存并进入</button>
        <p id="setupError" class="error" hidden></p>
      </form>
    </div>
  </section>

  <section id="mainView" class="view app-view" hidden>
    <header class="topbar">
      <div class="topbar-main">
        <div class="brand">
          <span class="brand-mark small" aria-hidden="true">H</span>
          <span class="brand-name">ESP32 Harness</span>
          <span id="autoBadge" class="badge danger" hidden>AUTO</span>
        </div>
        <div class="topmeta">
          <button id="undoBtn" class="ghost" type="button" title="回退最近一轮对话">回退</button>
          <button id="refreshBtn" class="ghost" type="button">刷新</button>
          <button id="settingsBtn" class="ghost" type="button">设置</button>
          <button id="logoutBtn" class="ghost" type="button">退出</button>
        </div>
      </div>
      <div class="statusline">
        <span id="connDot" class="conn-dot" aria-hidden="true"></span>
        <span id="modeText" class="status-text"></span>
      </div>
    </header>

    <main id="chatPane" class="pane">
      <div id="chatEmpty" class="empty-state">
        <span class="empty-icon" aria-hidden="true">H</span>
        <h2>开始新的对话</h2>
        <p>发送消息与设备上的 Agent 交流。代码、工具调用和执行结果会以卡片显示。</p>
      </div>
      <div id="chatLog" class="chat-log"></div>
      <form id="chatForm" class="composer">
        <div class="composer-options" aria-label="对话行为">
          <label class="composer-thinking">Thinking
            <select id="chatThinking" aria-label="Thinking 强度">
              <option value="disabled">关闭</option>
              <option value="low">Low</option>
              <option value="high">High</option>
              <option value="max">Max</option>
            </select>
          </label>
          <label class="check auto-toggle danger-text" title="自动运行模型生成的代码；开启需要确认">
            <input id="chatAuto" type="checkbox"> AUTO
          </label>
        </div>
        <textarea id="chatInput" rows="2" placeholder="输入消息；Enter 发送，Shift+Enter 换行"></textarea>
        <button id="stopBtn" type="button" class="ghost" hidden>停止</button>
        <button id="sendBtn" type="submit">发送</button>
      </form>
    </main>

    <main id="commandsPane" class="pane" hidden>
      <div id="commandEmpty" class="empty-state">
        <span class="empty-icon" aria-hidden="true">/</span>
        <h2>执行白名单命令</h2>
        <p>输入 /help 查看可用命令；交互式命令请回到串口控制台。</p>
      </div>
      <div id="commandLog" class="chat-log command-log"></div>
      <form id="commandForm" class="composer">
        <input id="commandInput" placeholder="/status、/context、/compact ...">
        <button type="submit">执行</button>
      </form>
    </main>

    <nav class="tabs" aria-label="主导航">
      <button class="tab active" data-tab="chat" type="button">对话</button>
      <button class="tab" data-tab="commands" type="button">命令</button>
    </nav>
  </section>

  <section id="settingsView" class="view settings-view" hidden>
    <header class="topbar">
      <div class="topbar-main">
        <div class="brand">
          <span class="brand-mark small" aria-hidden="true">H</span>
          <span class="brand-name">设备设置</span>
        </div>
        <button id="closeSettingsBtn" class="ghost" type="button">返回</button>
      </div>
    </header>
    <div class="settings-scroll">
      <section class="settings-card">
        <div class="card-head">
          <h2>网络</h2>
          <p id="netStatus" class="muted"></p>
        </div>
        <label class="field">Wi-Fi SSID<input id="cfgSsid" autocomplete="off"></label>
        <label class="field">Wi-Fi 密码<input id="cfgWifiPassword" type="password" autocomplete="off" placeholder="留空保留；勾选清除"></label>
        <label class="check"><input id="cfgWifiClear" type="checkbox">清除 Wi-Fi 密码</label>
        <div class="button-row"><button id="wifiSaveBtn" type="button">保存并连接</button></div>
        <p class="hint">修改 Wi-Fi 会先开启 AP；若当前页面断线，请连接设备 AP 后重新打开。</p>
      </section>
      <section class="settings-card">
        <div class="card-head">
          <h2>模型</h2>
          <p class="hint">OpenAI 兼容或 Anthropic Messages；留空的密钥字段会保留原值。</p>
        </div>
        <label class="field">API 格式<select id="cfgProvider">
          <option value="openai">OpenAI 兼容（chat/completions）</option>
          <option value="anthropic">Anthropic Messages</option>
        </select></label>
        <label class="field">Base URL<input id="cfgBaseUrl"></label>
        <label class="field">Model<input id="cfgModel"></label>
        <label class="field">API Key<input id="cfgApiKey" type="password" autocomplete="off" placeholder="留空保留；勾选清除"></label>
        <label class="check"><input id="cfgApiClear" type="checkbox">清除 API Key</label>
        <label class="check"><input id="cfgTls" type="checkbox">验证 TLS 证书</label>
        <p id="caStatus" class="hint">不想自定义 CA 时，保持当前路径和 TLS 验证开启即可。</p>
        <details id="caAdvanced" class="ca-advanced">
          <summary>高级：自定义 CA（通常无需修改）</summary>
          <p class="hint">仅私有 CA、企业内网或自建 API 需要修改。修改路径后点“保存模型配置”；上传 PEM 后点“保存 CA”。</p>
          <label class="field">CA 路径<input id="cfgCaFile" value="/ca.pem"></label>
          <label class="field">CA PEM（粘贴后保存）<textarea id="cfgCaPem" rows="4" placeholder="-----BEGIN CERTIFICATE-----"></textarea></label>
          <button id="caSaveBtn" type="button" class="ghost">保存 CA</button>
        </details>
        <div class="button-row">
          <button id="modelSaveBtn" type="button">保存模型配置</button>
          <button id="testWifiBtn" type="button" class="ghost">测试 Wi-Fi</button>
          <button id="testApiBtn" type="button" class="ghost">测试 API</button>
        </div>
      </section>
      <section class="settings-card">
        <div class="card-head">
          <h2>MCP（远程工具）</h2>
          <p class="hint">仅支持远程 HTTP/SSE MCP，不支持 stdio 子进程或 OAuth 浏览器登录。Header 值不会回显；省略 headers 字段表示保留原值。</p>
        </div>
        <label class="check"><input id="cfgMcpEnabled" type="checkbox">启用 MCP</label>
        <label class="field">MCP 服务器 JSON<textarea id="cfgMcpServers" rows="7" spellcheck="false" placeholder='[{"name":"github","url":"https://api.githubcopilot.com/mcp/","auto":false}]'></textarea></label>
        <div class="button-row">
          <button id="mcpSaveBtn" type="button">保存 MCP 配置</button>
          <button id="mcpRefreshBtn" type="button" class="ghost">刷新 MCP 工具</button>
        </div>
        <p id="mcpStatus" class="hint"></p>
      </section>
      <section class="settings-card">
        <div class="card-head">
          <h2>网页搜索</h2>
          <p class="hint">auto 会跟随当前模型 API：Anthropic 模型使用内置 web_search，OpenAI 官方端点使用 Responses web_search，DeepSeek 直连端点使用 deepseek_web_search 服务端搜索；OpenCode Go 和其他只提供模型推理的兼容端点使用 Bing 免 Key 搜索。原生搜索不可用时自动回退到 Bing。Bing 使用 RSS/HTML，无需任何 Key。</p>
        </div>
        <label class="check"><input id="cfgSearchEnabled" type="checkbox">启用 __search</label>
        <label class="field">搜索提供方
          <select id="cfgSearchProvider">
            <option value="auto">auto（跟随模型 API，推荐）</option>
            <option value="bing">Bing（免 Key）</option>
          </select>
        </label>
        <p id="searchEffective" class="hint"></p>
        <label class="check"><input id="cfgHttpToolEnabled" type="checkbox">启用 __http 公共网络抓取（HTTPS）</label>
        <label class="check"><input id="cfgHttpAllowPlain" type="checkbox">允许明文 HTTP（不安全，默认关闭）</label>
        <div class="button-row"><button id="searchSaveBtn" type="button">保存搜索与网络设置</button></div>
      </section>
      <section class="settings-card">
        <div class="card-head">
          <h2>行为</h2>
          <p class="hint">Thinking 强度和 AUTO 开关已移至对话框旁。AUTO 控制是否自动运行模型生成的代码；此处仅配置思考文本显示和执行结果自动反馈。</p>
        </div>
        <label class="check"><input id="cfgShowThinking" type="checkbox">显示思考文本</label>
        <label class="check"><input id="cfgAutoFeedback" type="checkbox">自动反馈执行结果</label>
        <label class="check"><input id="cfgBackground" type="checkbox">后台执行代码（完成后自动继续对话）</label>
        <label class="field">同时运行的后台程序数（1-4）<input id="cfgBackgroundMax" type="number" min="1" max="4" step="1"></label>
        <label class="field">自动反馈最大轮数（1-50）<input id="cfgMaxTurns" type="number" min="1" max="50" step="1"></label>
        <label class="check"><input id="cfgPersistConversation" type="checkbox">保存对话上下文（重启后恢复）</label>
        <div class="button-row"><button id="behaviorSaveBtn" type="button">保存显示与反馈</button></div>
      </section>
      <section class="settings-card">
        <div class="card-head">
          <h2>安全</h2>
          <p id="securityStatus" class="muted"></p>
        </div>
        <label class="field">当前管理密码<input id="cfgCurrentPassword" type="password" autocomplete="current-password"></label>
        <label class="field">新管理密码<input id="cfgNewPassword" type="password" autocomplete="new-password"></label>
        <label class="field">重复新密码<input id="cfgNewPassword2" type="password" autocomplete="new-password"></label>
        <div class="button-row"><button id="passwordSaveBtn" type="button">修改管理密码</button></div>
        <label class="field">AP 密码（下次 AP 启动生效）<input id="cfgApPassword" type="password" autocomplete="new-password"></label>
        <div class="button-row"><button id="apPasswordBtn" type="button">修改 AP 密码</button></div>
      </section>
      <section class="settings-card">
        <div class="card-head">
          <h2>系统</h2>
          <p id="sysStatus" class="muted"></p>
        </div>
        <div class="button-row">
          <button id="clearBtn" type="button" class="danger">清空对话</button>
          <button id="compactBtn" type="button">压缩上下文</button>
          <button id="historyBtn" type="button" class="ghost">刷新历史</button>
        </div>
      </section>
      <section class="settings-card settings-card-danger">
        <div class="card-head">
          <h2>恢复出厂设置</h2>
          <p class="hint">删除 config.json、secrets.json、MEMORY.md、待恢复代码、Skill、旧版 Tool 目录、工作区和对话记录，然后重启。固件、启动文件和公共 CA 证书保留。重启后 Wi-Fi 配置也会清除，需要连接设备 AP 重新设置。</p>
        </div>
        <label class="field">当前管理密码<input id="factoryPassword" type="password" autocomplete="current-password"></label>
        <label class="check"><input id="factoryConfirm" type="checkbox">我确认删除所有用户数据并重启设备</label>
        <div class="button-row"><button id="factoryResetBtn" type="button" class="danger">恢复出厂设置</button></div>
      </section>
    </div>
  </section>
</div>
<script src="/app.js"></script>
</body>
</html>
"""

APP_CSS = r"""*{box-sizing:border-box}
[hidden]{display:none !important}
:root{
  color-scheme:light dark;
  --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans SC",sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --bg:#f3f5f9;
  --panel:#ffffff;
  --panel-soft:#f8fafc;
  --panel-strong:#eef2f7;
  --text:#0f172a;
  --text-soft:#334155;
  --muted:#64748b;
  --line:#e2e8f0;
  --line-strong:#cbd5e1;
  --accent:#2563eb;
  --accent-strong:#1d4ed8;
  --accent-soft:#e8efff;
  --on-accent:#ffffff;
  --danger:#dc2626;
  --danger-strong:#b91c1c;
  --danger-soft:#fee2e2;
  --on-danger:#ffffff;
  --success:#16a34a;
  --warning:#d97706;
  --user-bubble:#e8f0ff;
  --user-border:#cfe0ff;
  --bot-bubble:#ffffff;
  --code-bg:#f1f5f9;
  --code-text:#0f172a;
  --shadow-sm:0 1px 2px rgba(15,23,42,.06);
  --shadow-lg:0 18px 48px rgba(15,23,42,.14);
  --radius-sm:8px;
  --radius:12px;
  --radius-lg:18px;
  --radius-pill:999px;
  --tabs-h:61px;
  --composer-h:140px;
  --ring:0 0 0 3px rgba(37,99,235,.28);
  --transition:140ms ease;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0b111a;
  --panel:#141d2b;
  --panel-soft:#111a27;
  --panel-strong:#1b2637;
  --text:#e6edf6;
  --text-soft:#c6d2e1;
  --muted:#94a3b8;
  --line:#263345;
  --line-strong:#3a4a60;
  --accent:#7aa2ff;
  --accent-strong:#9bbaff;
  --accent-soft:#1b2b48;
  --on-accent:#08111f;
  --danger:#ff7b7b;
  --danger-strong:#ff9a9a;
  --danger-soft:#3a1d24;
  --on-danger:#2a0b0b;
  --success:#4ade80;
  --warning:#fbbf24;
  --user-bubble:#172b47;
  --user-border:#28456d;
  --bot-bubble:#141d2b;
  --code-bg:#0a121d;
  --code-text:#dbe7f5;
  --shadow-sm:0 1px 2px rgba(0,0,0,.4);
  --shadow-lg:0 18px 48px rgba(0,0,0,.5);
  --ring:0 0 0 3px rgba(122,162,255,.35);
}}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);font-family:var(--font-sans);font-size:16px;line-height:1.6;-webkit-text-size-adjust:100%}
h1,h2,h3,h4{margin:0;line-height:1.3;letter-spacing:-.01em}
p{margin:0 0 10px}
p:last-child{margin-bottom:0}
button{font:inherit;font-weight:600;border:1px solid var(--accent);background:var(--accent);color:var(--on-accent);border-radius:var(--radius-sm);padding:10px 16px;min-height:44px;cursor:pointer;transition:background var(--transition),border-color var(--transition),color var(--transition),box-shadow var(--transition),transform var(--transition);-webkit-tap-highlight-color:transparent}
button:hover:not(:disabled){background:var(--accent-strong);border-color:var(--accent-strong)}
button:active:not(:disabled){transform:translateY(1px)}
button.ghost{background:var(--panel);color:var(--text-soft);border-color:var(--line-strong)}
button.ghost:hover:not(:disabled){background:var(--panel-strong);color:var(--text);border-color:var(--line-strong)}
button.danger{background:var(--danger);border-color:var(--danger);color:var(--on-danger)}
button.danger:hover:not(:disabled){background:var(--danger-strong);border-color:var(--danger-strong)}
button:disabled{opacity:.5;cursor:not-allowed;box-shadow:none;transform:none}
button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:none;box-shadow:var(--ring);border-color:var(--accent)}
input:disabled,textarea:disabled,select:disabled{opacity:.6;cursor:not-allowed;background:var(--panel-strong)}
input,textarea,select{font:inherit;font-size:16px;color:var(--text);background:var(--panel);border:1px solid var(--line-strong);border-radius:var(--radius-sm);padding:10px 12px;width:100%;min-height:44px;transition:border-color var(--transition),box-shadow var(--transition),background var(--transition)}
textarea{resize:vertical;line-height:1.55}
select{background-color:var(--panel)}
input::placeholder,textarea::placeholder{color:var(--muted);opacity:.85}
label{display:block;margin:12px 0;color:var(--text-soft);font-size:14px;font-weight:500}
label input,label textarea,label select{margin-top:6px;font-size:16px;color:var(--text)}
.view{max-width:900px;margin:0 auto;min-height:100vh}
.muted{color:var(--muted)}
.hint{color:var(--muted);font-size:13px;line-height:1.55;margin:6px 0 10px}
.error{color:var(--danger-strong);background:var(--danger-soft);border:1px solid var(--danger);border-radius:var(--radius-sm);padding:10px 12px;margin:10px 0;white-space:pre-wrap;font-size:14px}
.danger-text{color:var(--danger)}
.check{display:flex;gap:10px;align-items:center;color:var(--text);font-size:14px;font-weight:500;min-height:44px;padding:2px 0;cursor:pointer}
.check input[type=checkbox],.auto-toggle input[type=checkbox]{width:22px;height:22px;min-height:0;margin:0;padding:0;flex:0 0 auto;accent-color:var(--accent);cursor:pointer}
.btn-block{width:100%}
.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;line-height:1.4;padding:2px 8px;border-radius:var(--radius-pill);background:var(--accent-soft);color:var(--accent-strong);letter-spacing:.02em}
.badge.danger{background:var(--danger);color:var(--on-danger)}
.badge.soft{background:var(--panel-strong);color:var(--text-soft);font-weight:600}
.brand-mark{display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-strong));color:var(--on-accent);font-weight:800;font-size:20px;letter-spacing:-.02em;box-shadow:var(--shadow-sm);flex:0 0 auto}
.brand-mark.small{width:28px;height:28px;border-radius:8px;font-size:14px}
.auth-view{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px 16px;max-width:560px}
.auth-card{width:100%;background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);box-shadow:var(--shadow-lg);padding:28px 22px 20px}
.auth-card-wide{max-width:640px}
.auth-brand{display:flex;align-items:center;gap:14px;margin-bottom:14px}
.auth-brand-text h1{font-size:24px}
.auth-tagline{color:var(--muted);font-size:13px;margin:2px 0 0}
.auth-copy{margin:0 0 14px;font-size:14px}
.auth-form .field:first-of-type{margin-top:0}
.auth-foot{color:var(--muted);font-size:12px;text-align:center;margin:16px 0 0;padding-top:14px;border-top:1px solid var(--line)}
.form-section{border-top:1px solid var(--line);padding-top:16px;margin-top:16px}
.form-section:first-of-type{border-top:0;padding-top:0;margin-top:0}
.section-title{font-size:15px;font-weight:700;color:var(--text);margin-bottom:4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.topbar{position:sticky;top:0;z-index:10;background:var(--panel);border-bottom:1px solid var(--line);box-shadow:var(--shadow-sm);padding:10px 12px 8px}
.topbar-main{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;max-width:900px;margin:0 auto}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;min-width:0}
.brand-name{white-space:nowrap}
.topmeta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.topmeta button{min-height:44px;padding:8px 10px;font-size:13px}
.statusline{display:flex;align-items:center;gap:8px;max-width:900px;margin:6px auto 0;min-width:0}
.status-text{color:var(--muted);font-size:12.5px;line-height:1.4;overflow-wrap:anywhere;min-width:0}
.conn-dot{width:9px;height:9px;border-radius:50%;background:var(--muted);flex:0 0 auto;box-shadow:0 0 0 3px var(--panel-strong);transition:background var(--transition)}
.conn-dot.online{background:var(--success)}
.conn-dot.ap{background:var(--warning)}
.conn-dot.warn{background:var(--danger)}
.tabs{position:fixed;left:0;right:0;bottom:0;z-index:15;display:flex;justify-content:center;gap:8px;max-width:900px;margin:0 auto;padding:8px 12px calc(8px + env(safe-area-inset-bottom));background:var(--panel);border-top:1px solid var(--line);box-shadow:0 -4px 16px rgba(15,23,42,.06)}
.tab{flex:1;max-width:220px;background:transparent;color:var(--muted);border:1px solid transparent;border-radius:var(--radius-sm);min-height:44px;padding:8px 10px;font-weight:600;display:flex;align-items:center;justify-content:center;gap:8px}
.tab::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.45;flex:0 0 auto}
.tab:hover:not(.active){background:var(--panel-strong);color:var(--text)}
.tab.active{color:var(--on-accent);background:var(--accent);border-color:var(--accent);box-shadow:var(--shadow-sm)}
.tab.active::before{opacity:1}
.pane{padding:0 12px calc(var(--composer-h) + var(--tabs-h) + env(safe-area-inset-bottom) + 12px)}
.chat-log{min-height:40vh;padding:16px 0 8px;display:flex;flex-direction:column;gap:12px}
.chat-log:empty{min-height:0;padding:0}
.msg{margin:0;padding:12px 14px;border-radius:var(--radius);background:var(--bot-bubble);border:1px solid var(--line);white-space:pre-wrap;word-break:break-word;box-shadow:var(--shadow-sm);max-width:min(100%,760px);line-height:1.65}
.msg.user{background:var(--user-bubble);border-color:var(--user-border);margin-left:auto;border-bottom-right-radius:4px}
.msg.assistant{margin-right:auto;border-bottom-left-radius:4px}
.msg.system{background:transparent;border-style:dashed;color:var(--muted);font-family:var(--font-mono);font-size:13px;box-shadow:none;padding:8px 12px;max-width:100%}
.msg.user::before,.msg.assistant::before{display:block;font-size:11px;font-weight:700;color:var(--text-soft);margin-bottom:4px;letter-spacing:.04em}
.msg.user::before{content:"你"}
.msg.assistant::before{content:"助手"}
.msg .meta{display:block;color:var(--muted);font-size:11px;margin-top:8px;font-family:var(--font-mono);line-height:1.4}
.msg pre,.msg code{font-family:var(--font-mono);background:var(--code-bg);border-radius:6px}
.msg pre{padding:10px 12px;overflow:auto;white-space:pre-wrap;border:1px solid var(--line);color:var(--code-text)}
.msg code{padding:1px 5px;font-size:.9em}
details.reasoning,details.collapsible{margin:10px 0;color:var(--muted);font-size:13px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--panel-soft);overflow:hidden}
details summary{cursor:pointer;padding:10px 12px;font-weight:600;color:var(--text-soft);list-style:none;display:flex;align-items:center;gap:8px;min-height:44px;user-select:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"";width:0;height:0;border-left:5px solid currentColor;border-top:4px solid transparent;border-bottom:4px solid transparent;transition:transform var(--transition);flex:0 0 auto;opacity:.7}
details[open] summary::before{transform:rotate(90deg)}
details summary:hover{background:var(--panel-strong)}
details.reasoning pre,details.collapsible pre{max-height:240px;overflow:auto;white-space:pre-wrap;margin:0;padding:10px 12px;border:0;border-top:1px solid var(--line);border-radius:0;background:var(--code-bg);color:var(--code-text);font-family:var(--font-mono)}
.card{border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;margin:0;background:var(--panel);box-shadow:var(--shadow-sm)}
.card h4{margin:0 0 8px;font-size:14px;font-weight:700;color:var(--text);display:flex;align-items:flex-start;gap:8px;line-height:1.4}
.card h4::before{content:"";width:8px;height:8px;border-radius:2px;background:var(--accent);flex:0 0 auto;margin-top:6px}
.card .actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.card .actions button{flex:1 1 120px;min-height:44px}
.card pre{max-height:300px;overflow:auto;background:var(--code-bg);padding:10px 12px;border-radius:var(--radius-sm);white-space:pre-wrap;border:1px solid var(--line);font-family:var(--font-mono);font-size:13px;color:var(--code-text)}
.question-card{border-color:var(--accent);background:var(--accent-soft)}
.question-card .question-text{font-size:15px;line-height:1.6;margin:0 0 4px;white-space:pre-wrap}
.question-card .question-options button{flex:1 1 180px}
.question-card.answered{opacity:.7}
.question-card.answered .question-options button{cursor:default}
.background-card{border-color:var(--accent);background:var(--panel-soft)}
.background-job{border-top:1px solid var(--line);margin-top:8px;padding-top:8px}
.background-job:first-of-type{border-top:0;margin-top:0;padding-top:0}
.msg-actions{display:flex;justify-content:flex-end;gap:6px;margin-top:8px}
.msg-actions .rewind-btn{font-size:12px;padding:4px 10px;min-height:28px;white-space:normal}
.msg .rewind-btn{font-family:var(--font-sans)}
.empty-state{display:flex;flex-direction:column;align-items:center;text-align:center;padding:44px 20px 24px;color:var(--muted)}
.empty-icon{width:56px;height:56px;border-radius:16px;display:flex;align-items:center;justify-content:center;background:var(--accent-soft);color:var(--accent);font-family:var(--font-mono);font-weight:800;font-size:22px;margin-bottom:14px;border:1px solid var(--line)}
.empty-state h2{font-size:17px;color:var(--text);margin-bottom:6px}
.empty-state p{font-size:14px;max-width:380px;margin:0;line-height:1.6}
.composer{position:fixed;left:0;right:0;bottom:calc(var(--tabs-h) + env(safe-area-inset-bottom));z-index:14;max-width:900px;margin:0 auto;padding:10px 12px 12px;display:flex;flex-wrap:wrap;align-items:center;gap:8px;background:var(--panel);border-top:1px solid var(--line);box-shadow:0 -6px 20px rgba(15,23,42,.06)}
.composer-options{order:0;display:flex;align-items:center;justify-content:space-between;gap:10px;flex:1 0 100%;min-width:0}
.composer-options label{margin:0;font-size:13px;white-space:nowrap}
.composer-thinking{display:flex;align-items:center;gap:6px;color:var(--muted);font-weight:600}
.composer-thinking select{width:auto;min-width:96px;min-height:44px;padding:6px 10px;font-size:16px;margin-top:0}
.auto-toggle{display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:700;letter-spacing:.02em;min-height:44px;padding:0 4px}
.auto-toggle input{width:22px;height:22px;margin:0;accent-color:var(--danger)}
.composer textarea{flex:1 1 140px;min-width:120px;min-height:46px;max-height:180px;font-size:16px}
.composer input{flex:1 1 160px;width:auto;min-width:0}
.composer button{min-width:74px;min-height:46px}
.command-log{font-family:var(--font-mono);font-size:13px}
.command-log .msg{max-width:100%;background:var(--panel-soft);border-style:solid}
.settings-view .settings-scroll{padding:16px 12px 48px;max-width:820px;margin:0 auto}
.settings-view .topbar-main{max-width:820px}
.settings-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);padding:18px 16px;margin:0 0 16px;box-shadow:var(--shadow-sm)}
.settings-card-danger{border-color:var(--danger)}
.card-head{display:flex;flex-direction:column;gap:4px;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.card-head h2{font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.card-head h2::before{content:"";width:4px;height:16px;border-radius:2px;background:var(--accent);flex:0 0 auto}
.settings-card-danger .card-head h2::before{background:var(--danger)}
.card-head .muted{font-size:13px;margin:0}
.card-head .hint{margin:0}
.button-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.button-row button{flex:1 1 160px}
.ca-advanced{margin:12px 0;color:var(--muted);border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--panel-soft);padding:0 12px 12px}
.ca-advanced summary{margin:0 -12px;border-radius:var(--radius-sm)}
.ca-advanced[open] summary{margin-bottom:8px;border-bottom:1px solid var(--line);border-radius:var(--radius-sm) var(--radius-sm) 0 0}
.ca-advanced .field{margin-top:10px}
.toast{position:fixed;left:50%;bottom:calc(var(--composer-h) + var(--tabs-h) + env(safe-area-inset-bottom) + 12px);transform:translateX(-50%);background:var(--text);color:var(--bg);padding:10px 16px;border-radius:var(--radius-pill);z-index:30;font-size:14px;font-weight:600;box-shadow:var(--shadow-lg);max-width:calc(100vw - 32px);text-align:center;animation:toast-in 160ms ease-out}
@keyframes toast-in{from{opacity:0;transform:translate(-50%,6px)}to{opacity:1;transform:translate(-50%,0)}}
@media (max-width:640px){
  :root{--composer-h:180px}
  .topbar{padding:8px 10px 6px}
  .topmeta button{padding:8px 9px}
  .composer{flex-wrap:wrap}
  .composer-options{order:3;flex:1 0 100%;justify-content:space-between}
  .composer textarea{min-width:140px}
  .composer-options label{font-size:12px}
  .auth-card{padding:22px 16px 18px}
  .settings-card{padding:16px 14px;border-radius:var(--radius)}
  .msg{max-width:100%}
}
@media (min-width:720px){.auth-view{min-height:100vh}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important;scroll-behavior:auto !important}}
"""

APP_JS = r"""'use strict';
var state = {status:null, config:null, mcp:null, streaming:false, current:null, reasoning:null, reasoningChars:0, setup:false, setupInfo:null, authed:false, pendingKey:null, pendingEl:null, lastWifiError:'', activeStream:null, behaviorSaving:false, historyLoaded:false, backgroundKey:null, backgroundEl:null, backgroundPhase:null, backgroundTimer:null, backgroundResuming:false, autoContinueSeq:null, autoBusyNotice:false, liveUserEl:null, liveSeq:null, liveContent:'', liveReasoning:'', liveEventIndex:0, liveTimer:null, livePolling:false, liveGen:0};
function $(id){return document.getElementById(id);}
function setText(el,text){el.textContent = text == null ? '' : String(text);}
function show(el, yes){el.hidden = !yes;}
function scrollPageBottom(){
  var root=document.scrollingElement||document.documentElement;
  window.scrollTo(0,root.scrollHeight);
}
function scrollPageBottomSoon(){
  scrollPageBottom();
  if(window.requestAnimationFrame) window.requestAnimationFrame(scrollPageBottom);
}
function pageNearBottom(){
  var root=document.scrollingElement||document.documentElement;
  var offset=window.pageYOffset||root.scrollTop||0;
  return root.scrollHeight-window.innerHeight-offset<160;
}
function scrollIfStick(stick){
  if(stick&&chatVisible()) scrollPageBottomSoon();
}
function updateEmptyState(){
  var chat=$('chatEmpty'); if(chat) chat.hidden=$('chatLog').childElementCount>0;
  var commands=$('commandEmpty'); if(commands) commands.hidden=$('commandLog').childElementCount>0;
}
function chatVisible(){return !$('mainView').hidden&&!$('chatPane').hidden;}
function autoScrollChat(force){
  if(chatVisible()&&(force||pageNearBottom())) scrollPageBottom();
}
function toast(text){var el=document.createElement('div');el.className='toast';el.setAttribute('role','status');el.textContent=text;document.body.appendChild(el);setTimeout(function(){el.remove();},2500);}
function api(path, options){
  options = options || {};
  var headers = options.headers || {};
  headers['X-Harness'] = '1';
  if (options.body != null) headers['Content-Type'] = 'application/json';
  options.headers = headers;
  options.credentials = 'same-origin';
  return fetch(path, options).then(function(response){
    return response.text().then(function(text){
      var data = null;
      try { data = text ? JSON.parse(text) : {}; } catch (e) { data = {error:text}; }
      if (!response.ok) {
        var err = new Error(data.error || ('HTTP ' + response.status));
        err.status = response.status; err.data = data; throw err;
      }
      return data;
    });
  });
}
function post(path, value){return api(path,{method:'POST',body:JSON.stringify(value || {})});}
function logMessage(role, text, cls){
  var stick=pageNearBottom();
  var log=$('chatLog'); var el=document.createElement('div');
  el.className='msg ' + (cls || role); el.textContent=text || '';
  log.appendChild(el); updateEmptyState(); scrollIfStick(stick); return el;
}
function commandLog(text){
  var el=document.createElement('div');el.className='msg system';el.textContent=text || '';
  $('commandLog').appendChild(el); updateEmptyState();
  if(!$('mainView').hidden&&!$('commandsPane').hidden) scrollPageBottom();
}
function collapsibleBlock(summaryText, text, open){
  var details=document.createElement('details'); details.className='reasoning collapsible';
  if(open===true) details.open=true;
  var summary=document.createElement('summary'); summary.textContent=summaryText; details.appendChild(summary);
  var pre=document.createElement('pre'); pre.textContent=text || ''; details.appendChild(pre);
  return details;
}
function collapsedMessage(summaryText, text){
  var stick=pageNearBottom();
  var el=document.createElement('div'); el.className='msg system';
  el.appendChild(collapsibleBlock(summaryText,text,false));
  $('chatLog').appendChild(el); updateEmptyState(); scrollIfStick(stick);
  return el;
}
function renderAssistantBody(b){
  var text=b.text || '';
  b.fenceCount=text.split('```').length-1;
  var openStates=[];
  b.body.querySelectorAll('details').forEach(function(details){openStates.push(details.open===true);});
  b.body.textContent='';
  var cursor=0, detailIndex=0;
  function appendText(value){
    if(!value) return;
    var span=document.createElement('span'); span.textContent=value; b.body.appendChild(span);
  }
  function appendDetails(summary, content){
    var details=collapsibleBlock(summary,content,openStates[detailIndex]===true);
    detailIndex+=1; b.body.appendChild(details);
  }
  function blockLabel(language){
    var labels={python:'Python 代码',py:'Python 代码',micropython:'MicroPython 代码',
      tool:'工具调用',tool_call:'工具调用',toolcall:'工具调用','tool-call':'工具调用',
      skill:'Skill 提案'};
    return labels[language.toLowerCase()] || language || '代码';
  }
  while(cursor<text.length){
    var start=text.indexOf('```',cursor);
    if(start<0){appendText(text.slice(cursor));break;}
    appendText(text.slice(cursor,start));
    var lineEnd=text.indexOf('\n',start+3);
    if(lineEnd<0){appendDetails('代码块（接收中）',text.slice(start+3));cursor=text.length;break;}
    var language=text.slice(start+3,lineEnd).trim().split(/\s+/)[0];
    var label=blockLabel(language);
    var close=text.indexOf('```',lineEnd+1);
    if(close<0){
      appendDetails(label+'（接收中）',text.slice(lineEnd+1));
      cursor=text.length;break;
    }
    var content=text.slice(lineEnd+1,close);
    appendDetails('查看'+label+'（'+content.length+' 字符）',content);
    cursor=close+3;
  }
}
function appendAssistantText(text){
  var chunk=text || '';
  if(!chunk) return;
  var stick=pageNearBottom();
  var b=ensureAssistant();
  var fences=chunk.split('```').length-1;
  b.text+=chunk;
  if(fences===0 && b.fenceCount%2===0){
    b.body.appendChild(document.createTextNode(chunk));
    scrollIfStick(stick);
    return;
  }
  renderAssistantBody(b);
  scrollIfStick(stick);
}
function setAssistantText(text){
  var stick=pageNearBottom();
  var b=ensureAssistant(); b.text=text || ''; renderAssistantBody(b);
  scrollIfStick(stick);
}
function assistantBubble(){
  var stick=pageNearBottom();
  var el=document.createElement('div'); el.className='msg assistant';
  var body=document.createElement('div'); el.appendChild(body);
  var meta=document.createElement('span'); meta.className='meta'; el.appendChild(meta);
  $('chatLog').appendChild(el); updateEmptyState(); scrollIfStick(stick);
  return {el:el, body:body, meta:meta, text:'', fenceCount:0};
}
function ensureAssistant(){if(!state.current){state.current=assistantBubble();}return state.current;}
function syncBehaviorControls(run, level){
  if(state.behaviorSaving) return;
  if(level==='default'||level==='medium'||level==='middle') level='high';
  if(level!=='disabled'&&level!=='low'&&level!=='high'&&level!=='max') level='high';
  $('chatThinking').value=level;
  $('chatAuto').checked=!!run;
  $('chatAuto').indeterminate=false;
}
function restoreChatBehavior(){
  var cfg=state.config || {};
  var status=state.status || {};
  var auto=status.auto || {};
  var thinking=status.thinking || {};
  var run=auto.run===undefined?!!cfg.auto_run:!!auto.run;
  syncBehaviorControls(run,thinking.level || cfg.thinking_level || 'high');
}
function syncBehaviorDisabled(){
  var disabled=state.streaming || state.behaviorSaving;
  $('chatThinking').disabled=disabled;
  $('chatAuto').disabled=disabled;
}
function setBehaviorSaving(saving){
  state.behaviorSaving=saving;
  syncBehaviorDisabled();
}
function changeThinking(level){
  setBehaviorSaving(true);
  return post('/api/config',{thinking_level:level}).then(function(){
    if(state.config) state.config.thinking_level=level;
    setBehaviorSaving(false);
    toast('Thinking 已设为 '+level);
    refreshStatus().catch(function(){});
  }).catch(function(err){
    setBehaviorSaving(false); restoreChatBehavior(); toast(err.message);
    refreshStatus().catch(function(){});
  });
}
function changeAuto(enable){
  if(enable && !confirm('AUTO 会自动运行模型生成的代码，绕过逐次确认且拥有设备完整权限。确定开启？')){
    restoreChatBehavior();
    return Promise.resolve(false);
  }
  var feedback=state.config && typeof state.config.auto_feedback==='boolean' ? state.config.auto_feedback :
    (state.status && state.status.auto && typeof state.status.auto.feedback==='boolean' ? state.status.auto.feedback : true);
  setBehaviorSaving(true);
  return post('/api/auto',{auto_run:enable,auto_feedback:feedback,confirm:true}).then(function(){
    if(state.config){state.config.auto_run=enable;}
    setBehaviorSaving(false);
    toast(enable?'AUTO 已开启':'AUTO 已关闭');
    refreshStatus().catch(function(){});
    return true;
  }).catch(function(err){
    setBehaviorSaving(false); restoreChatBehavior(); toast(err.message);
    refreshStatus().catch(function(){});
    return false;
  });
}
function setBusy(busy){
  state.streaming=busy; $('sendBtn').disabled=busy; $('chatInput').disabled=busy;
  show($('stopBtn'), busy);
  syncBehaviorDisabled();
  ['undoBtn','refreshBtn','historyBtn','clearBtn','compactBtn','testWifiBtn','testApiBtn'].forEach(function(id){
    var el=$(id); if(el) el.disabled=busy;
  });
}
function renderStatus(status){
  state.status=status;
  var mode=status.mode || 'off';
  var ip=status.sta_ip || status.ap_ip || '-';
  var apNote='';
  if(status.ap_active && status.ap_off_in_s > 0){
    apNote = ' · AP ' + (status.ap_off_in_s >= 120 ? Math.ceil(status.ap_off_in_s / 60) + ' 分钟后关闭' : status.ap_off_in_s + ' 秒后关闭');
  }
  setText($('modeText'), mode + ' · ' + ip + (status.model && status.model.model ? ' · ' + status.model.model : '') + apNote);
  var dot=$('connDot');
  if(dot){
    var health=status.wifi_error?'warn':(status.sta_ip?'online':(status.ap_active?'ap':'warn'));
    dot.className='conn-dot '+health;
    dot.title=status.wifi_error?'Wi-Fi: '+status.wifi_error:(status.sta_ip?'STA 已连接':(status.ap_active?'AP 模式':'网络未连接'));
  }
  var autoState=status.auto||{};
  var auto=!!autoState.run;
  show($('autoBadge'), auto);
  syncBehaviorControls(!!autoState.run,(status.thinking||{}).level);
  if(status.wifi_error && state.lastWifiError!==status.wifi_error){toast('Wi-Fi: '+status.wifi_error);state.lastWifiError=status.wifi_error;}
  else if(!status.wifi_error){state.lastWifiError='';}
  maybeShowPending(status);
  syncBackground(status);
  var liveActive=!!(status.live&&status.live.active);
  if(!state.streaming&&((status.turn_busy&&status.busy_source==='auto-continue')||liveActive)){
    state.autoBusyNotice=false;
    startLivePoll();
  } else {
    state.autoBusyNotice=false;
    stopLivePoll();
  }
  var continueSeq=status.auto&&status.auto.continue_seq;
  if(continueSeq!=null){
    if(state.autoContinueSeq==null){
      state.autoContinueSeq=continueSeq;
    } else if(continueSeq!==state.autoContinueSeq){
      // The device auto-continued a background result on its main thread.
      // Pull the new history so serial-side AUTO turns are visible here.
      state.autoContinueSeq=continueSeq;
      if(state.historyLoaded&&!state.streaming){
        refreshHistory().catch(function(){});
      }
    }
  }
}
function maybeShowPending(status){
  var q=status&&status.question;
  if(q&&q.question){
    var attached=state.pendingEl && document.body.contains(state.pendingEl);
    if(state.pendingKey!=='question' || !attached) renderQuestion(q);
    return;
  }
  var p=status&&status.pending;
  if(!p||!p.kind||p.kind==='none') return;
  if(p.kind==='code'&&p.code){
    if(state.pendingKey!=='code'){state.pendingKey='code';renderCode({source:p.code,safe:p.safe,auto:false,timeout_s:p.timeout_s});}
    return;
  }
  if(p.kind==='skill'&&p.skill){
    var skillKey='skill:'+(p.skill.name||'');
    if(state.pendingKey!==skillKey){state.pendingKey=skillKey;renderProposal(p.skill);}
  }
}
function refreshStatus(){
  return api('/api/status').then(function(status){renderStatus(status);return status;}).catch(function(err){if(err.status===401)showLogin();throw err;});
}
function stopLivePoll(){
  if(state.liveTimer){clearTimeout(state.liveTimer);state.liveTimer=null;}
  state.livePolling=false;
  state.liveGen+=1;
  state.liveSeq=null; state.liveContent=''; state.liveReasoning=''; state.liveEventIndex=0;
}
function startLivePoll(){
  if(state.liveTimer||state.livePolling||state.streaming) return;
  state.liveGen+=1;
  state.livePolling=true;
  pollLive();
}
function pollLive(){
  var gen=state.liveGen;
  state.liveTimer=null;
  if(!state.authed||state.streaming){state.livePolling=false;return;}
  if(document.hidden){
    state.livePolling=false;
    state.liveTimer=setTimeout(pollLive,800);
    return;
  }
  state.livePolling=true;
  api('/api/live').then(function(data){
    if(gen!==state.liveGen||state.streaming) return;
    data=data||{};
    if(!data.id){
      state.livePolling=false;
      state.liveTimer=setTimeout(pollLive,400);
      return;
    }
    if(state.liveSeq!==data.id){
      state.liveSeq=data.id; state.liveContent=''; state.liveReasoning='';
      state.liveEventIndex=0;
      state.current=null; state.reasoning=null; state.reasoningChars=0;
    }
    var content=data.content||'';
    var reasoning=data.reasoning||'';
    var replace=content!==state.liveContent&&content.indexOf(state.liveContent)!==0;
    if(replace){
      state.current=null; state.reasoning=null; state.reasoningChars=0;
      state.liveContent=''; state.liveReasoning='';
    }
    var events=data.events||[];
    var base=data.event_base||0;
    var pos=state.liveContent.length;
    // Handle turn/tool events at or before the current content boundary
    // first, so a model_start (c=0 or c=pos) opens the assistant bubble
    // before the reasoning drawer is attached to it.
    for(var i=0;i<events.length;i++){
      var absolute=base+i;
      if(absolute<state.liveEventIndex) continue;
      var ev=events[i];
      var cut=typeof ev.c==='number'?ev.c:content.length;
      if(cut<pos) cut=pos;
      if(cut>content.length) cut=content.length;
      if(cut<=pos){
        handleEvent(ev);
        state.liveEventIndex=absolute+1;
      }
    }
    if(reasoning.length>state.liveReasoning.length&&
       reasoning.slice(0,state.liveReasoning.length)===state.liveReasoning){
      handleEvent({t:'reasoning',d:reasoning.slice(state.liveReasoning.length)});
    }
    state.liveReasoning=reasoning;
    for(var i=0;i<events.length;i++){
      var absolute=base+i;
      if(absolute<state.liveEventIndex) continue;
      var ev=events[i];
      var cut=typeof ev.c==='number'?ev.c:content.length;
      if(cut<pos) cut=pos;
      if(cut>content.length) cut=content.length;
      if(cut>pos){
        handleEvent({t:'content',d:content.slice(pos,cut)});
        pos=cut;
      }
      handleEvent(ev);
      state.liveEventIndex=absolute+1;
    }
    if(pos<content.length) handleEvent({t:'content',d:content.slice(pos)});
    state.liveContent=content;
    if(data.active){
      state.livePolling=false;
      state.liveTimer=setTimeout(pollLive,250);
    } else {
      state.livePolling=false;
      // Keep the sequence/content/index until refreshStatus sees the live
      // turn inactive. A status poll may otherwise restart the same id and
      // replay the last snapshot, duplicating a reply or tool card.
      setTimeout(function(){refreshStatus().catch(function(){});},350);
    }
  }).catch(function(){
    if(gen!==state.liveGen||state.streaming) return;
    state.livePolling=false;
    state.liveTimer=setTimeout(pollLive,1200);
  });
}
function attachRewindButton(el,index){
  if(!el||index==null||el.querySelector('.rewind-btn')) return;
  var actions=document.createElement('div'); actions.className='msg-actions';
  var button=document.createElement('button'); button.type='button'; button.className='ghost rewind-btn';
  button.textContent='回退到这里';
  button.onclick=function(){rewindContext({index:index});};
  actions.appendChild(button); el.appendChild(actions);
}
function rewindContext(payload){
  if(state.streaming) return;
  if(!confirm('回退会删除这条用户消息及其后的所有回复（包括工具结果和执行结果）。确定继续？')) return;
  post('/api/rewind', payload || {count:1}).then(function(data){
    state.pendingKey=null; state.pendingEl=null; state.liveUserEl=null;
    toast('已回退 '+(data.removed||0)+' 条消息');
    return refreshHistory();
  }).then(function(){return refreshStatus();}).catch(function(err){toast(err.message);});
}
function backgroundJobs(status){
  var jobs=status&&status.backgrounds;
  if(jobs&&jobs.length) return jobs;
  var one=status&&status.background;
  if(one&&one.phase&&one.phase!=='none') return [one];
  return [];
}
function backgroundRunning(bg){return !!bg&&(bg.phase==='queued'||bg.phase==='running');}
function backgroundBlocked(status){
  if(!status) return false;
  if(status.question&&status.question.question) return true;
  var pending=status.pending;
  return !!(pending&&pending.kind&&pending.kind!=='none');
}
function stopBackgroundPoll(){if(state.backgroundTimer){clearTimeout(state.backgroundTimer);state.backgroundTimer=null;}}
function pollBackground(){state.backgroundTimer=null;if(!state.authed||document.hidden)return;refreshStatus().catch(function(){});}
function backgroundStateLabel(bg){
  if(bg.phase==='queued') return '排队中';
  if(bg.phase==='running') return '运行中';
  if(bg.phase==='ready'){
    var r=bg.result||{};
    return r.cancelled?'已停止':(r.timed_out?'已超时':(r.ok?'成功':'失败'));
  }
  return '失败';
}
function stopBackground(id){
  if(!confirm('请求停止后台程序？只有执行到协作检查点的代码会停止；阻塞中的 C/SPI 调用可能只能等待看门狗。'))return;
  post('/api/background/stop',id==null?{}:{id:id}).then(function(data){toast(data.message||'已请求停止');refreshStatus().catch(function(){});}).catch(function(err){toast(err.message);});
}
function discardBackground(id){
  if(state.streaming)return;
  if(!confirm('丢弃这个后台执行结果？'))return;
  post('/api/background/discard',id==null?{}:{id:id}).then(function(){
    toast('已丢弃后台结果');
    refreshStatus().catch(function(){});
  }).catch(function(err){toast(err.message);});
}
function autoContinueBackground(jobId){
  if(state.streaming||state.backgroundResuming||!state.historyLoaded) return;
  if(state.status&&state.status.turn_busy) return;
  if(backgroundBlocked(state.status)) return;
  state.backgroundResuming=true;
  stopBackgroundPoll();
  stream('/api/continue',{id:jobId}).then(function(){
    state.backgroundResuming=false;
    setTimeout(function(){refreshStatus().catch(function(){});},300);
  }).catch(function(){
    state.backgroundResuming=false;
  });
}
function backgroundJobEl(bg){
  var wrap=document.createElement('div');
  wrap.className='background-job';
  wrap.setAttribute('data-job-id',String(bg.id));
  var head=document.createElement('p'); head.className='hint';
  head.textContent='#'+bg.id+' · '+backgroundStateLabel(bg)+' · 已用 ';
  var elapsed=document.createElement('span'); elapsed.className='bg-elapsed';
  elapsed.textContent=Math.round((bg.elapsed_ms||0)/1000);
  head.appendChild(elapsed);
  head.appendChild(document.createTextNode(' 秒 · 超时 '+
    (bg.timeout_s===0?'∞':(bg.timeout_s==null?'?':bg.timeout_s+'s'))));
  wrap.appendChild(head);
  var note=document.createElement('p'); note.className='hint';
  note.textContent='备注: '+((bg.note||bg.task||'').slice(0,160)||'<无>');
  wrap.appendChild(note);
  if(bg.phase==='running'&&bg.stop_requested){
    var stopping=document.createElement('p'); stopping.className='hint';
    stopping.textContent='停止请求已发送，等待协作检查点。'; wrap.appendChild(stopping);
  }
  if(bg.phase==='ready'&&bg.result){
    var output=(bg.result.output||'')+(bg.result.error?'\n'+bg.result.error:'');
    wrap.appendChild(collapsibleBlock('查看执行输出 #'+bg.id+'（'+output.length+' 字符）',output||'<无输出>',false));
  }
  if(bg.phase==='error'&&bg.error){
    var err=document.createElement('p'); err.className='hint';
    err.textContent='错误: '+bg.error.slice(0,240); wrap.appendChild(err);
  }
  var actions=document.createElement('div'); actions.className='actions';
  if(backgroundRunning(bg)&&!bg.stop_requested){
    var stop=document.createElement('button'); stop.type='button'; stop.className='ghost';
    stop.textContent='停止 #'+bg.id;
    stop.onclick=function(){stopBackground(bg.id);};
    actions.appendChild(stop);
  }
  if(bg.phase==='ready'&&bg.result){
    var autoState=state.status&&state.status.auto||{};
    var deviceAuto=!!(autoState.run&&autoState.feedback!==false)&&!bg.recovered;
    if(deviceAuto){
      var waiting=document.createElement('p'); waiting.className='hint';
      waiting.textContent='AUTO 模式：设备会自动继续此结果，无需手动选择。';
      wrap.appendChild(waiting);
    } else {
      var blocked=backgroundBlocked(state.status);
      if(!blocked&&!state.backgroundResuming){
        var cont=document.createElement('button'); cont.type='button';
        cont.textContent='继续 #'+bg.id;
        cont.onclick=function(){autoContinueBackground(bg.id);};
        actions.appendChild(cont);
      }
      var disc=document.createElement('button'); disc.type='button'; disc.className='ghost';
      disc.textContent='丢弃结果';
      disc.onclick=function(){discardBackground(bg.id);};
      actions.appendChild(disc);
    }
  } else if(bg.phase==='error'){
    var disc2=document.createElement('button'); disc2.type='button'; disc2.className='ghost';
    disc2.textContent='丢弃记录';
    disc2.onclick=function(){discardBackground(bg.id);};
    actions.appendChild(disc2);
  }
  if(actions.childNodes.length) wrap.appendChild(actions);
  return wrap;
}
function rebuildBackgroundBody(el,jobs){
  while(el.childNodes.length>1) el.removeChild(el.lastChild);
  var blocked=backgroundBlocked(state.status);
  var active=jobs.filter(backgroundRunning).length;
  var hint=document.createElement('p'); hint.className='hint';
  hint.textContent=(active?active+' 个后台任务正在运行或排队。':'后台任务已结束。')+
    '你可以同时发送新消息或在前台运行另一段代码；结果会分别保留。';
  el.appendChild(hint);
  if(blocked){
    var wait=document.createElement('p'); wait.className='hint';
    wait.textContent='有已完成后台结果，但请先处理待执行代码/Skill 或回答问题，之后才能继续结果。';
    el.appendChild(wait);
  }
  jobs.forEach(function(bg){el.appendChild(backgroundJobEl(bg));});
  if(active>1){
    var all=document.createElement('div'); all.className='actions';
    var stopAll=document.createElement('button'); stopAll.type='button'; stopAll.className='ghost';
    stopAll.textContent='停止全部后台程序';
    stopAll.onclick=function(){stopBackground(null);};
    all.appendChild(stopAll); el.appendChild(all);
  }
}
function renderBackgroundCard(jobs){
  var key=jobs.map(function(bg){return bg.id+':'+bg.phase+':'+(bg.stop_requested?'s':'')+':'+(bg.result?bg.result.elapsed_ms:0)+':'+(bg.note||'')+':'+(bg.error||'');}).join('|');
  if(state.backgroundKey!==key || !state.backgroundEl || !document.body.contains(state.backgroundEl)){
    if(state.backgroundEl) state.backgroundEl.remove();
    var el=card('后台任务 ('+jobs.length+')');
    el.classList.add('background-card');
    state.backgroundKey=key; state.backgroundEl=el;
    rebuildBackgroundBody(el,jobs);
    return;
  }
  // Update only the elapsed counter between structural rebuilds, so open
  // output drawers and chat scroll position survive status polling.
  jobs.forEach(function(bg){
    var row=state.backgroundEl.querySelector('[data-job-id="'+bg.id+'"]');
    if(!row) return;
    var span=row.querySelector('.bg-elapsed');
    if(span) span.textContent=Math.round((bg.elapsed_ms||0)/1000);
  });
}
function syncBackground(status){
  var jobs=backgroundJobs(status);
  if(!jobs.length){
    stopBackgroundPoll();
    if(state.backgroundEl){state.backgroundEl.remove();state.backgroundEl=null;state.backgroundKey=null;}
    return;
  }
  renderBackgroundCard(jobs);
  var active=jobs.filter(backgroundRunning).length;
  if(active){
    if(!state.backgroundTimer) state.backgroundTimer=setTimeout(pollBackground,1200);
  }
  var blocked=backgroundBlocked(status);
  if(blocked){
    if(!state.backgroundTimer) state.backgroundTimer=setTimeout(pollBackground,2000);
    return;
  }
  var turnBusy=status.turn_busy===undefined?!!status.busy:!!status.turn_busy;
  if(turnBusy){
    if(!state.backgroundTimer) state.backgroundTimer=setTimeout(pollBackground,1500);
    return;
  }
  if(!active) stopBackgroundPoll();
  if(state.historyLoaded&&!state.streaming&&!state.backgroundResuming){
    var autoState=status.auto||{};
    // In AUTO mode the device main thread continues results itself, so it
    // still works when this tab is closed or hidden. Only the browser
    // auto-continues in manual mode.
    var deviceAuto=!!(autoState.run&&autoState.feedback!==false);
    var feedback=!(autoState.feedback===false);
    var ready=jobs.filter(function(bg){return bg.phase==='ready'&&bg.result;});
    var autoJob=null;
    for(var i=0;i<ready.length;i++){
      if(!ready[i].recovered){autoJob=ready[i];break;}
    }
    if(autoJob&&feedback&&!deviceAuto) autoContinueBackground(autoJob.id);
  }
}
function refreshHistory(){
  if(state.streaming) return Promise.resolve();
  stopLivePoll(); state.liveSeq=null; state.liveContent=''; state.liveReasoning=''; state.liveEventIndex=0;
  return api('/api/history?limit=200').then(function(data){
    $('chatLog').innerHTML=''; state.current=null; state.reasoning=null; state.reasoningChars=0; state.pendingKey=null; state.pendingEl=null; state.liveUserEl=null;
    state.backgroundEl=null; state.backgroundKey=null; state.backgroundPhase=null;
    var observations=[];
    function flushObservations(){
      if(!observations.length) return;
      var text=observations.join('\n\n---\n\n');
      collapsedMessage('本地执行/工具记录（'+observations.length+' 条，'+text.length+' 字符）',text);
      observations=[];
    }
    (data.messages||[]).forEach(function(message){
      var role=message.role==='assistant'?'assistant':'user';
      if(message.role==='user' && message.kind==='observation'){
        observations.push(message.content || '');
        return;
      }
      flushObservations();
      if(role==='assistant'){
        var bubble=assistantBubble(); bubble.text=message.content || ''; renderAssistantBody(bubble);
      } else {
        var el=logMessage(role, message.content, role);
        if(message.kind==='user') attachRewindButton(el, message.index);
      }
    });
    flushObservations();
    updateEmptyState();
    if(data.truncated){
      var shown=data.shown_messages||(data.messages||[]).length;
      var total=data.total_messages||shown;
      logMessage('system','网页只显示最近 '+shown+'/'+total+' 条消息（受 web_history_limit / web_history_chars 限制）；更早的上下文可用串口 /history 查看。','system');
    }
    if((data.messages||[]).some(function(message){return message.truncated;})){
      logMessage('system','部分消息在网页视图中被截断（每条最多 8192 字符）；完整内容请用串口 /history。','system');
    }
    state.historyLoaded=true;
    if(state.status){maybeShowPending(state.status);syncBackground(state.status);}
    autoScrollChat(true);
  }).catch(function(err){if(err.status===401)showLogin();throw err;});
}
function handleEvent(ev){
  if(!ev || !ev.t) return;
  if(ev.t==='model_start'){
    // Keep the turn's thinking drawer across retries/tool-call model requests;
    // only the assistant content bubble starts fresh.
    state.current=null;
    return;
  }
  if(ev.t==='reasoning'){
    var stick=pageNearBottom();
    var b=ensureAssistant();
    if(!state.reasoning){
      state.reasoning=collapsibleBlock('思考过程 (0 字)','',false);
      state.reasoningChars=0;
      b.el.insertBefore(state.reasoning,b.body);
    }
    var chunk=ev.d||'';
    var pre=state.reasoning.querySelector('pre');
    var nearBottom=pre.scrollHeight-pre.scrollTop-pre.clientHeight<24;
    var previousTop=pre.scrollTop;
    pre.appendChild(document.createTextNode(chunk));
    state.reasoningChars += chunk.length;
    state.reasoning.querySelector('summary').textContent='思考过程 ('+state.reasoningChars+' 字)';
    pre.scrollTop=nearBottom?pre.scrollHeight:previousTop;
    scrollIfStick(stick);
    return;
  }
  if(ev.t==='content'){
    appendAssistantText(ev.d||''); return;
  }
  if(ev.t==='notice'){ logMessage('system', ev.d||'', 'system'); return; }
  if(ev.t==='model'){
    var meta=ensureAssistant().meta;
    var text='模型: ' + (ev.ok?'OK':'ERROR') + (ev.finish?' finish='+ev.finish:'') + (ev.elapsed_ms!=null?' '+ev.elapsed_ms+'ms':'');
    if(ev.first_token_ms) text += ' 首字='+ev.first_token_ms+'ms';
    if(ev.token_count) text += ' chunks='+ev.token_count;
    if(ev.max_gap_ms) text += ' 最大间隔='+ev.max_gap_ms+'ms';
    if(ev.tail_gap_ms) text += ' 尾间隔='+ev.tail_gap_ms+'ms';
    if(ev.error) text += ' ' + ev.error;
    meta.textContent=text; return;
  }
  if(ev.t==='tool'){
    if(ev.phase==='start'){
      collapsedMessage('工具调用: '+(ev.name||''),ev.args?JSON.stringify(ev.args):'{}');
    } else {
      collapsedMessage('工具结果: '+(ev.name||'')+' '+(ev.ok?'OK':'ERROR'),
        (ev.output||'')+(ev.error?'\n'+ev.error:'') || '<无输出>');
    }
    return;
  }
  if(ev.t==='response'){
    var b=ensureAssistant();
    if(ev.text && !b.text) setAssistantText(ev.text);
    return;
  }
  if(ev.t==='code'){ renderCode(ev); return; }
  if(ev.t==='execution'){ renderExecution(ev); return; }
  if(ev.t==='skill'){ renderProposal(ev); return; }
  if(ev.t==='question'){ renderQuestion(ev); return; }
  if(ev.t==='feedback'){ logMessage('system','自动反馈第 '+(ev.turn||'?')+' 轮...','system'); return; }
  if(ev.t==='background'){
    if(!state.backgroundTimer) state.backgroundTimer=setTimeout(pollBackground,500);
    return;
  }
  if(ev.t==='done'){
    state.current=null;
    if(state.liveUserEl){attachRewindButton(state.liveUserEl,ev.user_index);state.liveUserEl=null;}
    if(ev.pending && ev.pending!=='none' && ev.pending!=='question') logMessage('system','有待处理项: '+ev.pending,'system');
    return;
  }
  if(ev.t==='error'){ logMessage('system','错误: '+(ev.message||ev.code||''),'system'); return; }
}
function card(title){
  var stick=pageNearBottom();
  var el=document.createElement('div'); el.className='card';
  var h=document.createElement('h4'); h.textContent=title; el.appendChild(h);
  $('chatLog').appendChild(el); updateEmptyState(); scrollIfStick(stick); return el;
}
function renderCode(ev){
  var runnable=ev.runnable!==false;
  var title=runnable?(ev.auto?'代码（自动模式）':('待执行代码'+(ev.safe===false?'（检测到明显无限循环，需手动运行）':''))):'代码未调度';
  if(ev.timeout_s!=null) title+=' · 超时 '+(ev.timeout_s===0?'∞':ev.timeout_s+'s');
  var el=card(title);
  var source=ev.source||'';
  el.appendChild(collapsibleBlock('查看代码（'+source.length+' 字符）',source,false));
  if(!runnable){var blocked=document.createElement('p');blocked.className='hint';blocked.textContent='这段代码没有排入执行；可以让助手重新生成，或直接在下方输入新消息。';el.appendChild(blocked);return;}
  if(ev.auto){var note=document.createElement('p');note.className='hint';note.textContent='自动模式已开启，代码已提交执行。';el.appendChild(note);return;}
  state.pendingKey='code'; state.pendingEl=el;
  var actions=document.createElement('div'); actions.className='actions';
  var backgroundEnabled=!(state.status&&state.status.auto&&state.status.auto.background===false);
  if(ev.timeout_s!==0){
    var run=document.createElement('button'); run.textContent='前台运行'; run.onclick=function(){runPending('foreground');};
    actions.appendChild(run);
  }
  if(backgroundEnabled){
    var bgRun=document.createElement('button'); bgRun.type='button'; bgRun.textContent='后台运行';
    bgRun.onclick=function(){runPending('background');};
    actions.appendChild(bgRun);
  }
  var discard=document.createElement('button'); discard.className='ghost'; discard.textContent='丢弃'; discard.onclick=function(){post('/api/discard',{}).then(function(){state.pendingKey=null;state.pendingEl=null;el.remove();toast('已丢弃');refreshStatus().catch(function(){});}).catch(function(e){toast(e.message);});};
  actions.appendChild(discard); el.appendChild(actions);
}
function renderQuestion(ev){
  var el=card('模型提问: '+(ev.header||'问题'));
  el.classList.add('question-card');
  var text=document.createElement('p'); text.className='question-text'; text.textContent=ev.question||''; el.appendChild(text);
  var options=ev.options||[];
  if(options.length){
    var actions=document.createElement('div'); actions.className='actions question-options';
    options.forEach(function(option,index){
      var label=typeof option==='string'?option:(option.label||'');
      var description=typeof option==='string'?'':(option.description||'');
      var button=document.createElement('button');
      button.type='button';
      button.textContent=(index+1)+'. '+label;
      if(description) button.title=description;
      button.onclick=function(){answerQuestion(label,el);};
      actions.appendChild(button);
    });
    el.appendChild(actions);
    if(options.some(function(option){return typeof option!=='string' && option.description;})){
      var details=document.createElement('p'); details.className='hint';
      details.textContent=options.map(function(option,index){
        var label=typeof option==='string'?option:(option.label||'');
        var description=typeof option==='string'?'':(option.description||'');
        return description?((index+1)+'. '+label+'：'+description):'';
      }).filter(function(value){return value;}).join('  ');
      el.appendChild(details);
    }
  }
  if(ev.allow_custom!==false){
    var hint=document.createElement('p'); hint.className='hint';
    hint.textContent='也可以直接在下方输入自定义回答。'; el.appendChild(hint);
  }
  state.pendingKey='question'; state.pendingEl=el;
  return el;
}
function answerQuestion(label,el){
  if(state.streaming) return;
  var input=$('chatInput'); if(!input) return;
  input.value=label;
  sendChat();
}
function disableQuestions(){
  var cards=document.querySelectorAll('.question-card');
  for(var i=0;i<cards.length;i++){
    cards[i].classList.add('answered');
    var buttons=cards[i].querySelectorAll('button');
    for(var j=0;j<buttons.length;j++) buttons[j].disabled=true;
  }
  if(state.pendingKey==='question'){state.pendingKey=null;state.pendingEl=null;}
}
function renderExecution(ev){
  var resultText=ev.cancelled?'已停止':(ev.timed_out?'超时停止':(ev.ok?'成功':'失败'));
  var el=card('执行结果: '+resultText+' ('+(ev.elapsed_ms||0)+' ms)');
  var output=(ev.output||'')+(ev.error?'\n'+ev.error:'') || '<无输出>';
  el.appendChild(collapsibleBlock('查看执行输出（'+output.length+' 字符）',output,false));
}
function renderProposal(ev){
  var el=card('Skill 提案: '+(ev.name||''));
  var content=ev.body||'';
  el.appendChild(collapsibleBlock('查看完整提案（'+content.length+' 字符）',content,false));
  state.pendingKey='skill:'+(ev.name||''); state.pendingEl=el;
  var actions=document.createElement('div'); actions.className='actions';
  var save=document.createElement('button'); save.textContent='保存';
  save.onclick=function(){post('/api/save-skill',{}).then(function(){state.pendingKey=null;state.pendingEl=null;el.remove();toast('已保存');refreshStatus();}).catch(function(e){toast(e.message);});};
  var discard=document.createElement('button'); discard.className='ghost'; discard.textContent='丢弃';
  discard.onclick=function(){post('/api/discard',{}).then(function(){state.pendingKey=null;state.pendingEl=null;el.remove();toast('已丢弃');refreshStatus().catch(function(){});}).catch(function(e){toast(e.message);});};
  actions.appendChild(save); actions.appendChild(discard); el.appendChild(actions);
}
function stream(path, value){
  setBusy(true); state.current=null; state.reasoning=null; state.reasoningChars=0;
  var controller=null;
  try{controller=new AbortController();}catch(e){}
  var active={controller:controller, stopped:false};
  state.activeStream=active;
  var options={method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Harness':'1'},body:JSON.stringify(value||{})};
  if(controller) options.signal=controller.signal;
  return fetch(path,options)
  .then(function(response){
    if(!response.ok){return response.text().then(function(text){var data={};try{data=JSON.parse(text);}catch(e){};var err=new Error(data.error||('HTTP '+response.status));err.status=response.status;throw err;});}
    var reader=response.body.getReader(); var decoder=new TextDecoder(); var buffer='';
    function pump(){
      return reader.read().then(function(result){
        if(state.activeStream!==active) return;
        if(result.done){
          if(active.stopped){waitForStoppedTurn(active);}
          else {state.activeStream=null;setBusy(false);refreshStatus().catch(function(){});}
          return;
        }
        if(active.stopped){
          try{reader.cancel().catch(function(){});}catch(e){}
          waitForStoppedTurn(active);
          return;
        }
        buffer += decoder.decode(result.value,{stream:true});
        var index;
        while((index=buffer.indexOf('\n'))>=0){
          var line=buffer.slice(0,index); buffer=buffer.slice(index+1);
          if(line.trim()){try{handleEvent(JSON.parse(line));}catch(e){console.error(e);}}
        }
        return pump();
      });
    }
    return pump();
  }).catch(function(err){
    if(state.activeStream!==active) return;
    state.activeStream=null;
    setBusy(false);
    if(active.stopped){waitForStoppedTurn(active);return;}
    logMessage('system','请求失败: '+err.message,'system');
    if(err.status===401){showLogin();}
  });
}
function waitForStoppedTurn(active){
  if(state.activeStream!==active || active.waiting) return;
  active.waiting=true;
  pollStoppedTurn(active);
}
function pollStoppedTurn(active){
  if(state.activeStream!==active) return;
  api('/api/status').then(function(status){
    if(state.activeStream!==active) return;
    renderStatus(status);
    var turnBusy=status.turn_busy===undefined?status.busy:status.turn_busy;
    if(turnBusy){setTimeout(function(){pollStoppedTurn(active);},1000);return;}
    state.activeStream=null;
    setBusy(false);
    logMessage('system','设备已结束当前回合。','system');
    refreshHistory().catch(function(){});
  }).catch(function(err){
    if(state.activeStream!==active) return;
    if(err.status===401){state.activeStream=null;setBusy(false);showLogin();return;}
    setTimeout(function(){pollStoppedTurn(active);},1500);
  });
}
function stopStream(){
  var active=state.activeStream;
  if(!active || active.stopped) return;
  active.stopped=true;
  show($('stopBtn'),false);
  logMessage('system','已停止接收响应；设备上已开始的代码不会中断，正在等待设备结束。','system');
  if(active.controller){try{active.controller.abort();}catch(e){}}
  waitForStoppedTurn(active);
}
function runPending(mode){
  if(state.streaming)return;
  if(state.pendingEl){var actions=state.pendingEl.querySelector('.actions');if(actions)actions.remove();state.pendingKey=null;state.pendingEl=null;}
  stream('/api/run',{mode:mode||'background'});
}
function sendChat(event){
  if(event) event.preventDefault();
  if(state.streaming) return;
  var input=$('chatInput'); var text=input.value.trim(); if(!text) return;
  input.value='';
  disableQuestions();
  state.liveUserEl=logMessage('user',text,'user');
  scrollPageBottomSoon();
  stream('/api/chat',{text:text});
}
function runCommand(line,confirmed){
  post('/api/command',{line:line,confirm:!!confirmed}).then(function(data){
    if(data.output) commandLog(data.output);
    if(data.ok===false) commandLog('错误: '+(data.error||'命令失败'));
    refreshStatus().catch(function(){});
  }).catch(function(err){
    var data=err.data||{};
    if(data.code==='confirm'&&!confirmed){
      if(window.confirm((data.error||'确认执行该命令？')+'\n\n'+line)){
        runCommand(line,true); return;
      }
      commandLog('已取消: '+line); return;
    }
    commandLog('错误: '+err.message);
  });
}
function sendCommand(event){
  if(event) event.preventDefault();
  var input=$('commandInput'); var line=input.value.trim(); if(!line) return;
  input.value=''; commandLog('esp32> '+line);
  runCommand(line,false);
}
function showLogin(){state.authed=false;state.historyLoaded=false;state.liveUserEl=null;stopBackgroundPoll();show($('loginView'),true);show($('setupView'),false);show($('mainView'),false);show($('settingsView'),false);
  var note=state.setupInfo&&state.setupInfo.reset_note;
  if(note){setText($('loginNotice'),note);show($('loginNotice'),true);}else{show($('loginNotice'),false);}
}
function showSetup(setup){state.setup=setup;state.authed=false;state.historyLoaded=false;stopBackgroundPoll();show($('setupView'),true);show($('loginView'),false);show($('mainView'),false);show($('settingsView'),false);
  if(setup){var port=setup.port||80;setText($('setupHint'),'请连接设备 AP'+(setup.ap_ssid?' '+setup.ap_ssid:'')+'，然后打开 http://'+(setup.ap_ip||'192.168.4.1')+(port===80?'':':'+port)+'/ 完成设置。');}
}
function showApp(){state.authed=true;show($('mainView'),true);show($('settingsView'),false);show($('loginView'),false);show($('setupView'),false);show($('loginNotice'),false);}
function boot(){
  return api('/api/setup').then(function(setup){
    state.setupInfo=setup;
    if(setup.setup_required){showSetup(setup);return;}
    return refreshStatus().then(function(){showApp();return refreshHistory();}).catch(function(err){
      if(err.status===401){showLogin();} else {showLogin();}
    });
  }).catch(function(){showLogin();});
}
function searchProviderLabel(name){
  var labels={deepseek:'DeepSeek 模型内置搜索',anthropic:'Anthropic 内置 web_search',openai:'OpenAI Responses web_search',bing:'Bing 免 Key'};
  return labels[name]||name||'-';
}
function loadConfig(){
  return api('/api/config').then(function(cfg){
    state.config=cfg;
    $('cfgSsid').value=cfg.wifi_ssid||'';
    $('cfgWifiPassword').value=''; $('cfgWifiClear').checked=false;
    $('cfgWifiPassword').placeholder=cfg.wifi_password_set?'已设置；留空保留；勾选清除':'未设置';
    $('cfgProvider').value=cfg.api_provider||'openai';
    $('cfgBaseUrl').value=cfg.base_url||'';
    $('cfgModel').value=cfg.model||'';
    $('cfgApiKey').value=''; $('cfgApiClear').checked=false;
    $('cfgApiKey').placeholder=cfg.api_key_set?'已设置；留空保留；勾选清除':'未设置';
    $('cfgTls').checked=!!cfg.tls_verify;
    $('cfgCaFile').value=cfg.ca_file||'/ca.pem';
    if(cfg.ca_present){
      setText($('caStatus'),'已安装 CA：'+(cfg.ca_file||'/ca.pem')+'。不想自定义时保持原样即可；全量公共 CA 已部署时无需替换。');
      $('caAdvanced').open=false;
    } else {
      setText($('caStatus'),'未检测到 CA 文件：'+(cfg.ca_file||'/ca.pem')+'。使用公共 HTTPS 前请部署全量 bundle 或在下方上传对应根证书。');
      $('caAdvanced').open=true;
    }
    syncBehaviorControls(!!cfg.auto_run,cfg.thinking_level||'high');
    $('cfgShowThinking').checked=!!cfg.show_thinking;
    $('cfgAutoFeedback').checked=!!cfg.auto_feedback;
    $('cfgBackground').checked=cfg.background_execution!==false;
    $('cfgBackgroundMax').value=cfg.background_max_jobs||4;
    $('cfgMaxTurns').value=cfg.max_turns||32;
    $('cfgPersistConversation').checked=cfg.persist_conversation!==false;
    $('cfgSearchEnabled').checked=cfg.search_enabled!==false;
    $('cfgSearchProvider').value=cfg.search_provider||'auto';
    setText($('searchEffective'),'当前实际使用：'+searchProviderLabel(cfg.search_provider_effective||cfg.search_provider||'auto')+(cfg.search_provider_fallback?'（失败时自动回退到 Bing）':''));
    $('cfgHttpToolEnabled').checked=cfg.http_tool_enabled!==false;
    $('cfgHttpAllowPlain').checked=!!cfg.http_tool_allow_http;
    setText($('netStatus'),'模式 '+(cfg.mode||'')+' · STA '+(cfg.sta_ip||'-')+' · AP '+(cfg.ap_active?(cfg.ap_ssid||'on'):'off'));
    setText($('securityStatus'),'管理密码: '+(cfg.web_password_set?'已设置':'未设置')+' · AP 密码: '+(cfg.ap_password_set?'已设置':'未设置'));
    setText($('sysStatus'),'IP '+(cfg.sta_ip||cfg.ap_ip||'-')+' · 历史 '+(cfg.history?cfg.history.messages:0)+' 条');
    return cfg;
  }).catch(function(err){if(err.status===401)showLogin();throw err;});
}
function loadMcp(){
  return api('/api/mcp').then(function(mcp){
    state.mcp=mcp;
    $('cfgMcpEnabled').checked=!!mcp.enabled;
    var servers=(mcp.servers||[]).map(function(s){
      return {name:s.name,url:s.url,enabled:s.enabled,auto:s.auto,timeout_s:s.timeout_s,protocol:s.protocol};
    });
    $('cfgMcpServers').value=JSON.stringify(servers,null,2);
    var errors=mcp.errors||{}; var names=Object.keys(errors);
    var text='已加载工具: '+(mcp.tools||0)+' 个';
    if(names.length){text+='；错误: '+names.map(function(n){return n+': '+errors[n];}).join('; ');}
    if(!mcp.enabled){text='MCP 已关闭';}
    setText($('mcpStatus'),text);
    return mcp;
  }).catch(function(err){setText($('mcpStatus'),'MCP 状态不可用: '+err.message);throw err;});
}
function saveConfig(fields){
  return post('/api/config',fields).then(function(result){
    toast(result.relogin_required?'密码已修改，请重新登录':'已保存');
    if(result.relogin_required){showLogin();return result;}
    return refreshStatus().then(loadConfig);
  }).catch(function(err){toast(err.message);throw err;});
}
function toggleSettings(showIt){
  if(showIt){showApp();show($('settingsView'),true);show($('mainView'),false);loadConfig().catch(function(e){toast(e.message);});loadMcp().catch(function(){});}
  else {show($('settingsView'),false);showApp();scrollPageBottomSoon();}
}
document.addEventListener('DOMContentLoaded',function(){
  $('loginForm').onsubmit=function(e){e.preventDefault();setText($('loginError'),'');post('/api/login',{password:$('loginPassword').value}).then(function(){showApp();return refreshStatus();}).then(function(){return refreshHistory();}).catch(function(err){setText($('loginError'),err.message);show($('loginError'),true);});};
  $('setupForm').onsubmit=function(e){
    e.preventDefault();setText($('setupError'),'');
    var password=$('setupPassword').value, confirm=$('setupConfirm').value;
    if(password!==confirm){setText($('setupError'),'两次密码不一致');show($('setupError'),true);return;}
    var body={password:password,confirm:confirm,wifi_ssid:$('setupSsid').value,wifi_password:$('setupWifiPassword').value,api_provider:$('setupProvider').value,base_url:$('setupBaseUrl').value,model:$('setupModel').value,api_key:$('setupApiKey').value};
    post('/api/setup',body).then(function(){showApp();return refreshStatus();}).then(function(){return refreshHistory();}).catch(function(err){setText($('setupError'),err.message);show($('setupError'),true);});
  };
  $('chatForm').onsubmit=sendChat;
  $('commandForm').onsubmit=sendCommand;
  $('chatInput').addEventListener('keydown',function(e){
    if(e.key==='Enter' && !e.shiftKey && !e.isComposing){
      e.preventDefault(); sendChat();
    }
  });
  $('sendBtn').onclick=sendChat;
  $('stopBtn').onclick=stopStream;
  $('chatThinking').onchange=function(){changeThinking(this.value);};
  $('chatAuto').onchange=function(){changeAuto(this.checked);};
  function switchProviderBase(selectId,baseId){
    var base=$(baseId).value.replace(/\/+$/,'');
    var known=['https://api.openai.com/v1','https://api.openai.com','https://api.anthropic.com','https://api.deepseek.com','https://api.deepseek.com/v1'];
    if(known.indexOf(base)>=0){
      $(baseId).value=$(selectId).value==='anthropic'?'https://api.anthropic.com':'https://api.openai.com/v1';
    }
  }
  $('setupProvider').onchange=function(){switchProviderBase('setupProvider','setupBaseUrl');};
  $('cfgProvider').onchange=function(){switchProviderBase('cfgProvider','cfgBaseUrl');};
  $('undoBtn').onclick=function(){rewindContext({count:1});};
  $('refreshBtn').onclick=function(){refreshStatus().then(refreshHistory).catch(function(e){toast(e.message);});};
  $('logoutBtn').onclick=function(){post('/api/logout',{}).then(function(){showLogin();});};
  $('settingsBtn').onclick=function(){toggleSettings(true);};
  $('closeSettingsBtn').onclick=function(){toggleSettings(false);};
  $('wifiSaveBtn').onclick=function(){
    var body={ssid:$('cfgSsid').value,password:$('cfgWifiClear').checked?'-':$('cfgWifiPassword').value};
    post('/api/wifi/connect',body).then(function(result){toast(result.connecting?'正在连接，若断线请连接设备 AP':'已提交');refreshStatus().catch(function(){});}).catch(function(e){toast(e.message);});
  };
  $('modelSaveBtn').onclick=function(){
    var body={api_provider:$('cfgProvider').value,base_url:$('cfgBaseUrl').value,model:$('cfgModel').value,tls_verify:$('cfgTls').checked,ca_file:$('cfgCaFile').value};
    if($('cfgApiClear').checked) body.api_key_clear=true; else if($('cfgApiKey').value) body.api_key=$('cfgApiKey').value;
    saveConfig(body);
  };
  $('caSaveBtn').onclick=function(){post('/api/config/ca',{pem:$('cfgCaPem').value}).then(function(){toast('CA 已保存');}).catch(function(e){toast(e.message);});};
  $('mcpSaveBtn').onclick=function(){
    var raw=$('cfgMcpServers').value.trim(); var servers=[];
    if(raw){try{servers=JSON.parse(raw);}catch(e){toast('MCP JSON 无效');return;}}
    if(!Array.isArray(servers)){toast('MCP 配置必须是数组');return;}
    var auto=servers.some(function(s){return s&&s.auto;});
    if(auto && !confirm('MCP auto 模式会无确认执行远程工具，继续？')) return;
    post('/api/config',{mcp_enabled:$('cfgMcpEnabled').checked}).then(function(){
      return post('/api/mcp',{servers:servers,confirm:auto});
    }).then(function(){toast('MCP 已保存，正在刷新工具');return post('/api/mcp/refresh',{});})
      .then(function(){toast('MCP 工具已刷新');return loadMcp();})
      .catch(function(e){toast(e.message);});
  };
  $('mcpRefreshBtn').onclick=function(){
    post('/api/mcp/refresh',{}).then(function(result){toast('MCP 工具已刷新: '+(result.tools||0));return loadMcp();}).catch(function(e){toast(e.message);});
  };
  $('searchSaveBtn').onclick=function(){
    var body={
      search_enabled:$('cfgSearchEnabled').checked,
      search_provider:$('cfgSearchProvider').value,
      http_tool_enabled:$('cfgHttpToolEnabled').checked,
      http_tool_allow_http:$('cfgHttpAllowPlain').checked
    };
    saveConfig(body).then(function(){
      toast('搜索设置已保存');
    }).catch(function(){});
  };
  $('behaviorSaveBtn').onclick=function(){
    var cfg=state.config||{};
    var currentAuto=(state.status&&state.status.auto)||{};
    var run=typeof cfg.auto_run==='boolean'?cfg.auto_run:!!currentAuto.run;
    post('/api/config',{show_thinking:$('cfgShowThinking').checked,background_execution:$('cfgBackground').checked,background_max_jobs:parseInt($('cfgBackgroundMax').value,10)||4,max_turns:parseInt($('cfgMaxTurns').value,10)||32,persist_conversation:$('cfgPersistConversation').checked}).then(function(){
      return post('/api/auto',{
        auto_run:run,
        auto_feedback:$('cfgAutoFeedback').checked,
        confirm:true
      });
    }).then(function(){toast('显示与反馈配置已保存');return refreshStatus();}).catch(function(e){toast(e.message);});
  };
  $('passwordSaveBtn').onclick=function(){
    if(!$('cfgNewPassword').value){toast('请输入新管理密码');return;}
    var body={web_password:$('cfgNewPassword').value,current_password:$('cfgCurrentPassword').value,confirm_password:$('cfgNewPassword2').value};
    saveConfig(body).then(function(){$('cfgCurrentPassword').value='';$('cfgNewPassword').value='';$('cfgNewPassword2').value='';}).catch(function(){});
  };
  $('apPasswordBtn').onclick=function(){
    var value=$('cfgApPassword').value;
    if(value.length<8||value.length>63){toast('AP 密码需为 8-63 位');return;}
    saveConfig({ap_password:value}).then(function(){$('cfgApPassword').value='';}).catch(function(){});
  };
  $('clearBtn').onclick=function(){if(confirm('清空当前对话历史？')) post('/api/command',{line:'/clear'}).then(function(){refreshHistory();refreshStatus();}).catch(function(e){toast(e.message);});};
  $('compactBtn').onclick=function(){post('/api/command',{line:'/compact'}).then(function(data){commandLog(data.output||'');refreshStatus();}).catch(function(e){toast(e.message);});};
  $('historyBtn').onclick=function(){refreshHistory().catch(function(e){toast(e.message);});};
  $('factoryResetBtn').onclick=function(){
    if(!$('factoryConfirm').checked){toast('请先勾选确认删除所有用户数据');return;}
    var password=$('factoryPassword').value;
    if(!password){toast('请输入当前管理密码');return;}
    if(!confirm('恢复出厂设置会删除所有用户数据并重启设备，且不可撤销。确定继续？')) return;
    post('/api/factory-reset',{confirm:true,password:password}).then(function(data){
      $('factoryPassword').value='';
      $('factoryConfirm').checked=false;
      toast('已删除 '+(data.deleted||[]).length+' 项用户数据，设备正在重启');
      setTimeout(function(){
        showLogin();
        setText($('loginError'),'恢复出厂设置已完成。请连接设备 AP（ESP32-Harness-XXXX）后打开 http://192.168.4.1/ 重新设置。');
        show($('loginError'),true);
      },1500);
    }).catch(function(e){toast(e.message);});
  };
  $('testWifiBtn').onclick=function(){post('/api/test',{kind:'wifi'}).then(function(data){toast(data.ok?'Wi-Fi 正常':'Wi-Fi 异常');}).catch(function(e){toast(e.message);});};
  $('testApiBtn').onclick=function(){post('/api/test',{kind:'api'}).then(function(data){toast(data.ok?'API 正常':'API 异常');}).catch(function(e){toast(e.message);});};
  document.querySelectorAll('.tab').forEach(function(tab){tab.onclick=function(){document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});tab.classList.add('active');var name=tab.getAttribute('data-tab');show($('chatPane'),name==='chat');show($('commandsPane'),name==='commands');scrollPageBottomSoon();};});
  setInterval(function(){if(state.authed && !state.streaming && !document.hidden){refreshStatus().catch(function(){});}},5000);
  document.addEventListener('visibilitychange',function(){
    if(!document.hidden && state.authed && !state.streaming){refreshStatus().catch(function(){});}
  });
  boot();
});
"""
