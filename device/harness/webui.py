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
      <p class="muted auth-copy">登录后即可对话、运行 / 命令和管理设备。</p>
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
    <div id="sidebarBackdrop" class="sidebar-backdrop" hidden></div>
    <aside id="sidebar" class="sidebar" aria-label="主菜单">
      <div class="sidebar-head">
        <div class="brand">
          <span class="brand-mark small" aria-hidden="true">H</span>
          <span class="brand-name">ESP32 Harness</span>
        </div>
        <button id="sidebarCloseBtn" class="icon-btn" type="button" aria-label="收起侧边栏">«</button>
      </div>
      <nav class="side-nav">
        <button class="nav-item" data-nav="status" type="button"><span class="nav-dot"></span>状态</button>
        <button class="nav-item active" data-nav="chat" type="button"><span class="nav-dot"></span>对话</button>
        <div class="nav-group open" data-group="tasks">
          <button class="nav-item nav-parent" data-group-toggle="tasks" type="button"><span class="nav-dot"></span>任务<span class="chevron">▸</span></button>
          <div class="nav-sub" id="navTasksSub">
            <button class="nav-item sub" data-nav="background" type="button">后台任务</button>
            <button class="nav-item sub" data-nav="schedules" type="button">自动化任务</button>
          </div>
        </div>
        <button class="nav-item" data-nav="skills" type="button"><span class="nav-dot"></span>Skill</button>
        <button class="nav-item" data-nav="files" type="button"><span class="nav-dot"></span>文件</button>
        <div class="nav-group" data-group="settings">
          <button class="nav-item nav-parent" data-group-toggle="settings" type="button"><span class="nav-dot"></span>设置<span class="chevron">▸</span></button>
          <div class="nav-sub" id="navSettingsSub" hidden>
            <button class="nav-item sub" data-nav="settings-network" type="button">网络</button>
            <button class="nav-item sub" data-nav="settings-model" type="button">模型</button>
            <button class="nav-item sub" data-nav="settings-mcp" type="button">MCP</button>
            <button class="nav-item sub" data-nav="settings-search" type="button">网页搜索</button>
            <button class="nav-item sub" data-nav="settings-behavior" type="button">Harness 行为</button>
            <button class="nav-item sub" data-nav="settings-security" type="button">安全</button>
            <button class="nav-item sub" data-nav="settings-factory" type="button">恢复出厂设置</button>
          </div>
        </div>
      </nav>
      <div class="sidebar-foot">
        <div class="side-status">
          <span id="sideConnDot" class="conn-dot" aria-hidden="true"></span>
          <span id="sideStatusText" class="status-text"></span>
        </div>
        <button id="logoutBtn" class="ghost btn-block" type="button">退出登录</button>
      </div>
    </aside>

    <div class="main-area">
      <header class="appbar">
        <button id="sidebarToggleBtn" class="icon-btn" type="button" aria-label="显示或隐藏侧边栏">☰</button>
        <div class="appbar-main">
          <h1 id="screenTitle" class="appbar-title">对话</h1>
          <span id="autoBadge" class="badge danger" hidden>AUTO</span>
        </div>
        <div class="appbar-meta">
          <span id="connDot" class="conn-dot" aria-hidden="true"></span>
          <span id="modeText" class="status-text"></span>
          <button id="refreshBtn" class="ghost appbar-btn" type="button">刷新</button>
        </div>
      </header>

      <main class="screens">
        <section id="screenStatus" class="screen" hidden>
          <div class="screen-inner">
            <section class="settings-card">
              <div class="card-head">
                <h2>状态</h2>
                <p id="statusLine" class="muted"></p>
              </div>
              <div class="stat-grid">
                <div class="stat-cell"><span class="stat-label">网络模式</span><strong id="statusMode" class="stat-value">-</strong></div>
                <div class="stat-cell"><span class="stat-label">IP</span><strong id="statusIp" class="stat-value">-</strong></div>
                <div class="stat-cell"><span class="stat-label">Wi-Fi</span><strong id="statusSsid" class="stat-value">-</strong></div>
                <div class="stat-cell"><span class="stat-label">可用堆</span><strong id="statusHeap" class="stat-value">-</strong></div>
                <div class="stat-cell"><span class="stat-label">模型</span><strong id="statusModel" class="stat-value">-</strong></div>
                <div class="stat-cell"><span class="stat-label">会话</span><strong id="statusSessions" class="stat-value">-</strong></div>
              </div>
              <p id="statusWifiError" class="error" hidden></p>
              <p id="statusVersion" class="hint"></p>
            </section>
            <section class="settings-card">
              <div class="card-head">
                <h2>上下文</h2>
                <p class="hint">清空会删除全部对话历史；压缩会调用模型生成摘要。</p>
              </div>
              <div class="context-meter"><div id="contextBar" class="context-bar"></div></div>
              <p id="contextText" class="muted"></p>
              <p id="contextSummary" class="hint"></p>
              <div class="button-row">
                <button id="clearBtn" type="button" class="danger">清空对话</button>
                <button id="compactBtn" type="button">压缩上下文</button>
                <button id="historyBtn" type="button" class="ghost">刷新历史</button>
              </div>
            </section>
            <section class="settings-card">
              <div class="card-head"><h2>运行状态</h2></div>
              <div class="stat-grid">
                <div class="stat-cell"><span class="stat-label">设备回合</span><strong id="statusTurn" class="stat-value">空闲</strong></div>
                <div class="stat-cell"><span class="stat-label">后台任务</span><strong id="statusBackground" class="stat-value">0</strong></div>
                <div class="stat-cell"><span class="stat-label">实时续答</span><strong id="statusLive" class="stat-value">-</strong></div>
                <div class="stat-cell"><span class="stat-label">版本</span><strong id="statusVersionShort" class="stat-value">-</strong></div>
              </div>
            </section>
          </div>
        </section>

        <section id="screenChat" class="screen chat-screen">
          <div id="chatEmpty" class="empty-state">
            <span class="empty-icon" aria-hidden="true">H</span>
            <h2>开始新的对话</h2>
            <p>发送消息与设备上的 Agent 交流。输入 / 可直接运行命令；思考、工具调用、代码和正文会分别显示。</p>
          </div>
          <div id="chatLog" class="chat-log"></div>
          <form id="chatForm" class="composer">
            <div class="composer-inner">
              <div class="composer-options" aria-label="对话行为">
                <label class="composer-thinking"><span class="composer-thinking-label">Thinking</span>
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
              <textarea id="chatInput" rows="1" placeholder="输入消息，或 / 命令；Enter 发送，Shift+Enter 换行"></textarea>
              <button id="stopBtn" type="button" class="ghost" hidden>停止</button>
              <button id="sendBtn" type="submit">发送</button>
            </div>
          </form>
        </section>

        <section id="screenBackground" class="screen" hidden>
          <div class="screen-inner">
            <section class="settings-card">
              <div class="card-head">
                <h2>后台任务</h2>
                <p class="hint">后台程序在独立线程运行；完成后可继续对话或丢弃结果。AUTO 模式下设备会自动继续结果。</p>
                <p id="backgroundQuota" class="hint"></p>
                <div class="button-row"><button id="backgroundClearBtn" type="button" class="ghost">清空已完成结果</button></div>
              </div>
              <div id="backgroundList" class="background-list"></div>
            </section>
          </div>
        </section>

        <section id="screenSchedules" class="screen" hidden>
          <div class="screen-inner">
            <section class="settings-card">
              <div class="card-head">
                <h2>自动化任务</h2>
                <p class="hint">按设备 RTC 的本地时间执行。一次性任务重启后仍保留；重复任务错过多次时只补跑一次。提示词任务会让 AI 在指定时间执行，代码任务在后台运行。</p>
              </div>
              <div id="scheduleList" class="schedule-list"></div>
              <details id="scheduleEditor" class="schedule-editor">
                <summary id="scheduleEditorSummary">新建定时任务</summary>
                <p class="hint">时间使用设置页中的时区。代码任务拥有完整设备权限，保存前会再次确认。</p>
                <label class="field">名称<input id="scheduleName" maxlength="48" autocomplete="off"></label>
                <label class="field">类型
                  <select id="scheduleType">
                    <option value="prompt">提示词（到点后让 AI 执行）</option>
                    <option value="code">MicroPython 代码（后台运行）</option>
                  </select>
                </label>
                <label class="field" id="schedulePromptField">提示词<textarea id="schedulePrompt" rows="3" maxlength="2000"></textarea></label>
                <label class="field" id="scheduleSourceField" hidden>代码<textarea id="scheduleSource" rows="6" maxlength="8192" spellcheck="false"></textarea></label>
                <label class="field" id="scheduleTimeoutField" hidden>代码超时（秒，0 = 不限）<input id="scheduleTimeout" type="number" min="0" step="1" inputmode="numeric"></label>
                <label class="field">执行方式
                  <select id="scheduleWhen">
                    <option value="once">一次性</option>
                    <option value="interval">按间隔重复</option>
                    <option value="daily">每天固定时间</option>
                  </select>
                </label>
                <div id="scheduleOnceFields">
                  <label class="field">延迟（秒）<input id="scheduleDelay" type="number" min="1" step="1" inputmode="numeric"></label>
                  <label class="field">或本地时间（YYYY-MM-DD HH:MM）<input id="scheduleAt" autocomplete="off" placeholder="2026-11-27 08:00"></label>
                </div>
                <label class="field" id="scheduleIntervalField" hidden>间隔（秒，至少 30）<input id="scheduleEvery" type="number" min="30" step="1" inputmode="numeric"></label>
                <label class="field" id="scheduleDailyField" hidden>每天时间（HH:MM）<input id="scheduleDailyAt" autocomplete="off" placeholder="08:00"></label>
                <label class="check"><input id="scheduleEnabled" type="checkbox" checked>启用</label>
                <div class="button-row">
                  <button id="scheduleSaveBtn" type="button">创建任务</button>
                  <button id="scheduleCancelBtn" type="button" class="ghost" hidden>取消编辑</button>
                </div>
              </details>
            </section>
          </div>
        </section>

        <section id="screenSkills" class="screen" hidden>
          <div class="screen-inner">
            <section class="settings-card">
              <div class="card-head">
                <h2>Skill</h2>
                <p id="skillsStatus" class="hint">Skill 是 Markdown 指令包，不会执行代码。</p>
              </div>
              <div id="skillsList" class="skills-list"></div>
            </section>
          </div>
        </section>

        <section id="screenFiles" class="screen" hidden>
          <div class="screen-inner">
            <section class="settings-card">
              <div class="card-head">
                <h2>文件</h2>
                <p class="hint">浏览设备 VFS。只有 /workspace 下的文件可以修改、上传、重命名或删除；系统文件只读，/secrets.json 和 /mcp.json 不会通过网页读取或下载。</p>
              </div>
              <div class="file-toolbar">
                <button id="fileUpBtn" type="button" class="ghost">上一级</button>
                <button id="fileRefreshBtn" type="button" class="ghost">刷新</button>
                <button id="fileMkdirBtn" type="button" class="ghost">新建文件夹</button>
                <button id="fileNewBtn" type="button" class="ghost">新建文本文件</button>
                <button id="fileUploadBtn" type="button">上传文本文件</button>
                <input id="fileUploadInput" type="file" hidden>
              </div>
              <div id="filePath" class="file-path">/</div>
              <p id="fileHint" class="hint"></p>
              <div id="fileList" class="file-list"></div>
              <section id="fileEditor" class="file-editor" hidden>
                <div class="card-head">
                  <h2 id="fileEditorPath">编辑文件</h2>
                  <p id="fileEditorHint" class="hint"></p>
                </div>
                <textarea id="fileEditorText" rows="12" spellcheck="false"></textarea>
                <div class="button-row">
                  <button id="fileSaveBtn" type="button">保存</button>
                  <button id="fileCancelBtn" type="button" class="ghost">取消</button>
                </div>
              </section>
            </section>
          </div>
        </section>

        <section id="screenSettings" class="screen" hidden>
          <div class="screen-inner">
            <section class="settings-card" data-settings-panel="network" id="settingsPanelNetwork">
              <div class="card-head">
                <h2>网络</h2>
                <p id="netStatus" class="muted"></p>
              </div>
              <label class="field">Wi-Fi SSID<input id="cfgSsid" autocomplete="off"></label>
              <label class="field">Wi-Fi 密码<input id="cfgWifiPassword" type="password" autocomplete="off" placeholder="留空保留；勾选清除"></label>
              <label class="check"><input id="cfgWifiClear" type="checkbox">清除 Wi-Fi 密码</label>
              <div class="button-row">
                <button id="wifiSaveBtn" type="button">保存并连接</button>
                <button id="testWifiBtn" type="button" class="ghost">测试 Wi-Fi</button>
              </div>
              <p class="hint">修改 Wi-Fi 会先开启 AP；若当前页面断线，请连接设备 AP 后重新打开。</p>
            </section>

            <section class="settings-card" data-settings-panel="model" id="settingsPanelModel" hidden>
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
                <button id="testApiBtn" type="button" class="ghost">测试 API</button>
              </div>
            </section>

            <section class="settings-card" data-settings-panel="mcp" id="settingsPanelMcp" hidden>
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

            <section class="settings-card" data-settings-panel="search" id="settingsPanelSearch" hidden>
              <div class="card-head">
                <h2>网页搜索</h2>
                <p class="hint">auto 会跟随当前模型 API：Anthropic 模型使用内置 web_search，OpenAI 官方端点使用 Responses web_search，DeepSeek 直连端点使用 deepseek_web_search 服务端搜索；OpenCode Go 和其他只提供模型推理的兼容端点使用 Bing 免 Key 搜索。原生搜索不可用时自动回退到 Bing。</p>
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

            <section class="settings-card" data-settings-panel="behavior" id="settingsPanelBehavior" hidden>
              <div class="card-head">
                <h2>Harness 行为</h2>
                <p class="hint">Thinking 强度和 AUTO 开关在对话框旁。AUTO 控制是否自动运行模型生成的代码；此处配置思考文本、执行结果自动反馈、后台执行、NTP/RTC 自动校时和显示时区。</p>
              </div>
              <label class="check"><input id="cfgShowThinking" type="checkbox">显示思考文本</label>
              <label class="check"><input id="cfgAutoFeedback" type="checkbox">自动反馈执行结果</label>
              <label class="check"><input id="cfgBackground" type="checkbox">后台执行代码（完成后自动继续对话）</label>
              <label class="field">同时运行的后台程序数（1-4）<input id="cfgBackgroundMax" type="number" min="1" max="4" step="1"></label>
              <label class="field">自动反馈最大轮数（1-50）<input id="cfgMaxTurns" type="number" min="1" max="50" step="1"></label>
              <label class="field">单回合工具调用上限（1-64）<input id="cfgMaxToolCalls" type="number" min="1" max="64" step="1"></label>
              <label class="field">上下文长度（tokens，1024-2000000）<input id="cfgContextTokens" type="number" min="1024" max="2000000" step="1024"></label>
              <label class="field">自动压缩阈值（tokens，需小于上下文长度）<input id="cfgAutoCompactTokens" type="number" min="1024" max="2000000" step="1024"></label>
              <label class="check"><input id="cfgAutoCompact" type="checkbox">启用自动压缩</label>
              <label class="field">NTP 服务器<input id="cfgNtpServer" type="text" maxlength="255" autocomplete="off"></label>
              <label class="field">时区偏移（分钟，UTC+8 = 480）<input id="cfgTimezoneOffset" type="number" min="-720" max="840" step="1"></label>
              <label class="field">自动校时间隔（小时，0 = 仅首次）<input id="cfgNtpIntervalHours" type="number" min="0" max="24" step="1"></label>
              <label class="check"><input id="cfgPersistConversation" type="checkbox">保存对话上下文（重启后恢复）</label>
              <div class="button-row"><button id="behaviorSaveBtn" type="button">保存行为与时间设置</button></div>
            </section>

            <section class="settings-card" data-settings-panel="security" id="settingsPanelSecurity" hidden>
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

            <section class="settings-card settings-card-danger" data-settings-panel="factory" id="settingsPanelFactory" hidden>
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
      </main>
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
  --sidebar-w:264px;
  --appbar-h:57px;
  --composer-h:78px;
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
.view{min-height:100vh}
.muted{color:var(--muted)}
.hint{color:var(--muted);font-size:13px;line-height:1.55;margin:6px 0 10px}
.error{color:var(--danger-strong);background:var(--danger-soft);border:1px solid var(--danger);border-radius:var(--radius-sm);padding:10px 12px;margin:10px 0;white-space:pre-wrap;font-size:14px}
.danger-text{color:var(--danger)}
.check{display:flex;gap:10px;align-items:center;color:var(--text);font-size:14px;font-weight:500;min-height:44px;padding:2px 0;cursor:pointer}
.check input[type=checkbox],.auto-toggle input[type=checkbox]{width:22px;height:22px;min-height:0;margin:0;padding:0;flex:0 0 auto;accent-color:var(--accent);cursor:pointer}
.btn-block{width:100%}
.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:700;line-height:1.4;padding:2px 8px;border-radius:var(--radius-pill);background:var(--accent-soft);color:var(--accent-strong);letter-spacing:.02em;white-space:nowrap}
.badge.danger{background:var(--danger);color:var(--on-danger)}
.badge.soft{background:var(--panel-strong);color:var(--text-soft);font-weight:600}
.badge.ok{background:var(--success);color:#fff}
.badge.warn{background:var(--warning);color:#fff}
.brand-mark{display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-strong));color:var(--on-accent);font-weight:800;font-size:20px;letter-spacing:-.02em;box-shadow:var(--shadow-sm);flex:0 0 auto}
.brand-mark.small{width:28px;height:28px;border-radius:8px;font-size:14px}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:16px;min-width:0}
.brand-name{white-space:nowrap}
.auth-view{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px 16px;max-width:560px;margin:0 auto}
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
.app-view{max-width:none}
.sidebar{position:fixed;top:0;left:0;bottom:0;width:var(--sidebar-w);z-index:40;display:flex;flex-direction:column;background:var(--panel);border-right:1px solid var(--line);box-shadow:var(--shadow-sm);transition:transform var(--transition)}
.sidebar-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 12px 10px;border-bottom:1px solid var(--line)}
.side-nav{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:2px}
.nav-item{width:100%;display:flex;align-items:center;gap:10px;background:transparent;color:var(--text-soft);border:1px solid transparent;border-radius:10px;min-height:42px;padding:8px 10px;font-weight:600;text-align:left;justify-content:flex-start}
.nav-item:hover:not(:disabled){background:var(--panel-strong);color:var(--text);border-color:transparent}
.nav-item.active{background:var(--accent-soft);color:var(--accent-strong);border-color:var(--accent-soft)}
.nav-item.sub{min-height:38px;font-size:14px;padding-left:30px;color:var(--muted)}
.nav-item.sub.active{color:var(--accent-strong);background:var(--accent-soft)}
.nav-dot{width:7px;height:7px;border-radius:50%;background:currentColor;opacity:.55;flex:0 0 auto}
.nav-item.active .nav-dot{opacity:1}
.chevron{margin-left:auto;transition:transform var(--transition);opacity:.7;font-size:12px}
.nav-group.open .chevron{transform:rotate(90deg)}
.nav-sub{display:flex;flex-direction:column;gap:2px;margin:2px 0 4px}
.sidebar-foot{border-top:1px solid var(--line);padding:10px 12px calc(10px + env(safe-area-inset-bottom));display:flex;flex-direction:column;gap:8px}
.side-status{display:flex;align-items:center;gap:8px;min-width:0}
.side-status .status-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sidebar-backdrop{position:fixed;inset:0;z-index:35;background:rgba(15,23,42,.45)}
.main-area{margin-left:var(--sidebar-w);min-height:100vh;display:flex;flex-direction:column;transition:margin var(--transition)}
body.sidebar-collapsed .sidebar{transform:translateX(-100%)}
body.sidebar-collapsed .main-area{margin-left:0}
body.sidebar-collapsed .composer{left:0}
.appbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:8px;min-height:var(--appbar-h);padding:8px 12px;background:var(--panel);border-bottom:1px solid var(--line);box-shadow:var(--shadow-sm)}
.icon-btn{background:transparent;color:var(--text-soft);border:1px solid transparent;min-height:40px;min-width:40px;padding:4px 8px;font-size:18px;line-height:1}
.icon-btn:hover:not(:disabled){background:var(--panel-strong);border-color:transparent;color:var(--text)}
.appbar-main{display:flex;align-items:center;gap:8px;flex:1;min-width:0}
.appbar-title{font-size:16px;font-weight:700;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.appbar-meta{display:flex;align-items:center;gap:8px;min-width:0}
.appbar-meta .status-text{max-width:38vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.appbar-btn{min-height:38px;padding:6px 12px;font-size:13px}
.status-text{color:var(--muted);font-size:12.5px;line-height:1.4;overflow-wrap:anywhere;min-width:0}
.conn-dot{width:9px;height:9px;border-radius:50%;background:var(--muted);flex:0 0 auto;box-shadow:0 0 0 3px var(--panel-strong);transition:background var(--transition)}
.conn-dot.online{background:var(--success)}
.conn-dot.ap{background:var(--warning)}
.conn-dot.warn{background:var(--danger)}
.screens{flex:1;min-height:0}
.screen{padding:0 12px calc(24px + env(safe-area-inset-bottom))}
.screen-inner{max-width:900px;margin:0 auto;padding:16px 2px 48px}
.chat-screen{display:flex;flex-direction:column;min-height:calc(100vh - var(--appbar-h));padding:0 12px calc(var(--composer-h) + env(safe-area-inset-bottom) + 16px)}
.chat-log{width:100%;max-width:860px;margin:0 auto;padding:14px 0 8px;display:flex;flex-direction:column;gap:8px}
.chat-log:empty{min-height:0;padding:0}
.turn{display:flex;flex-direction:column;gap:6px;width:100%}
.turn-answer{display:flex;flex-direction:column;gap:6px;width:100%}
.bubble{position:relative;max-width:min(100%,820px);padding:8px 11px;border-radius:12px;background:var(--bot-bubble);border:1px solid var(--line);box-shadow:var(--shadow-sm);white-space:pre-wrap;word-break:break-word;line-height:1.55;font-size:14.5px}
.bubble.user{background:var(--user-bubble);border-color:var(--user-border);margin-left:auto;border-bottom-right-radius:4px}
.bubble.assistant{border-bottom-left-radius:4px}
.bubble.system{background:transparent;border-style:dashed;color:var(--muted);font-family:var(--font-mono);font-size:13px;box-shadow:none;padding:7px 10px}
.bubble.system.error{border-color:var(--danger);color:var(--danger-strong);background:var(--danger-soft)}
.bubble.tool{border-left:3px solid var(--accent);background:var(--panel-soft);font-family:var(--font-mono);font-size:13px}
.bubble.tool.err{border-left-color:var(--danger);background:var(--danger-soft)}
.bubble.reasoning{background:var(--panel-soft);border-style:dashed;color:var(--muted);font-size:13px;box-shadow:none}
.bubble.code-card{border-left:3px solid var(--accent);font-family:var(--font-mono);font-size:13px}
.bubble.question-card{border-color:var(--accent);background:var(--accent-soft)}
.bubble.background-card{border-color:var(--accent);background:var(--panel-soft)}
.bubble.meta-bubble{background:transparent;border:0;box-shadow:none;color:var(--muted);font-family:var(--font-mono);font-size:11px;padding:0 4px}
.bubble.command-echo{font-family:var(--font-mono);font-size:13px;background:var(--panel-strong);border-color:var(--line-strong)}
.bubble.command-output{font-family:var(--font-mono);font-size:13px;background:var(--panel-soft);border-style:solid;color:var(--text-soft)}
.bubble-label{display:flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;color:var(--text-soft);letter-spacing:.03em;margin-bottom:4px;font-family:var(--font-sans)}
.bubble-label .badge{margin-left:auto}
.bubble-body{white-space:pre-wrap}
.bubble-meta{display:block;font-size:11px;color:var(--muted);font-family:var(--font-mono);margin-top:4px}
.bubble .meta{display:block;color:var(--muted);font-size:11px;margin-top:8px;font-family:var(--font-mono);line-height:1.4}
.bubble pre,.bubble code{font-family:var(--font-mono);background:var(--code-bg);border-radius:6px}
.bubble pre{padding:10px 12px;overflow:auto;white-space:pre-wrap;border:1px solid var(--line);color:var(--code-text)}
.bubble code{padding:1px 5px;font-size:.9em}
.turn-answer .bubble{max-width:min(100%,820px)}
details.reasoning,details.collapsible{margin:4px 0;color:var(--muted);font-size:13px;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--panel-soft);overflow:hidden}
details summary{cursor:pointer;padding:8px 10px;font-weight:600;color:var(--text-soft);list-style:none;display:flex;align-items:center;gap:8px;min-height:36px;user-select:none}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"";width:0;height:0;border-left:5px solid currentColor;border-top:4px solid transparent;border-bottom:4px solid transparent;transition:transform var(--transition);flex:0 0 auto;opacity:.7}
details[open] summary::before{transform:rotate(90deg)}
details summary:hover{background:var(--panel-strong)}
details.reasoning pre,details.collapsible pre{max-height:300px;overflow:auto;white-space:pre-wrap;margin:0;padding:10px 12px;border:0;border-top:1px solid var(--line);border-radius:0;background:var(--code-bg);color:var(--code-text);font-family:var(--font-mono)}
.card{border:1px solid var(--line);border-radius:var(--radius);padding:10px 12px;margin:0;background:var(--panel);box-shadow:var(--shadow-sm)}
.card .actions{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.card .actions button{flex:1 1 120px;min-height:40px}
.question-card .question-text{font-size:14.5px;line-height:1.6;margin:0 0 4px;white-space:pre-wrap}
.question-card .question-options button{flex:1 1 180px}
.question-card.answered{opacity:.7}
.question-card.answered .question-options button{cursor:default}
.background-card{border-color:var(--accent);background:var(--panel-soft)}
.background-job{border-top:1px solid var(--line);margin-top:8px;padding-top:8px}
.background-job:first-of-type{border-top:0;margin-top:0;padding-top:0}
.background-list .background-card{border:0;background:transparent;box-shadow:none;padding:0;margin:0}
.background-card .actions,.background-job .actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.background-card .actions button,.background-job .actions button{font-size:13px;padding:6px 10px;min-height:36px}
.skill-job .actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.schedule-list{display:flex;flex-direction:column;gap:10px}
.schedule-list .empty-hint{padding:4px 0}
.schedule-job{border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;background:var(--panel-soft)}
.schedule-job.disabled{opacity:.7}
.schedule-job h4{font-size:15px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px}
.schedule-job .schedule-meta{font-size:13px;color:var(--muted);line-height:1.55;white-space:pre-wrap;word-break:break-word}
.schedule-job .schedule-preview{font-size:13px;color:var(--text-soft);margin-top:6px;white-space:pre-wrap;word-break:break-word}
.schedule-job .actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.schedule-job .actions button{font-size:13px;padding:6px 10px;min-height:36px}
.schedule-editor{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
.schedule-editor summary{cursor:pointer;font-weight:700;color:var(--text-soft);padding:6px 0}
.schedule-editor .field{margin-top:10px}
.msg-actions{display:flex;justify-content:flex-end;gap:6px;margin-top:6px}
.msg-actions .rewind-btn{font-size:12px;padding:4px 10px;min-height:28px;white-space:normal}
.rewind-btn{font-family:var(--font-sans)}
.empty-state{display:flex;flex-direction:column;align-items:center;text-align:center;padding:44px 20px 24px;color:var(--muted)}
.empty-icon{width:56px;height:56px;border-radius:16px;display:flex;align-items:center;justify-content:center;background:var(--accent-soft);color:var(--accent);font-family:var(--font-mono);font-weight:800;font-size:22px;margin-bottom:14px;border:1px solid var(--line)}
.empty-state h2{font-size:17px;color:var(--text);margin-bottom:6px}
.empty-state p{font-size:14px;max-width:420px;margin:0;line-height:1.6}
.composer{position:fixed;left:var(--sidebar-w);right:0;bottom:0;z-index:20;background:var(--panel);border-top:1px solid var(--line);box-shadow:0 -6px 20px rgba(15,23,42,.06);padding:6px 12px calc(8px + env(safe-area-inset-bottom))}
.composer-inner{max-width:900px;margin:0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.composer-options{order:0;display:flex;align-items:center;gap:8px;flex:0 0 auto;min-width:0}
.composer-options label{margin:0;font-size:12px;white-space:nowrap}
.composer-thinking{display:flex;align-items:center;gap:5px;color:var(--muted);font-weight:600}
.composer-thinking select{width:auto;min-width:76px;min-height:38px;padding:4px 8px;font-size:14px;margin-top:0}
.composer-thinking-label{display:inline}
.auto-toggle{display:flex;align-items:center;gap:5px;cursor:pointer;font-weight:700;letter-spacing:.02em;min-height:38px;padding:0 2px;font-size:12px}
.auto-toggle input{width:20px;height:20px;margin:0;accent-color:var(--danger)}
.composer textarea{flex:1 1 180px;min-width:100px;min-height:38px;max-height:140px;font-size:16px;padding:8px 10px}
.composer button{min-width:62px;min-height:38px;padding:8px 12px;font-size:14px}
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
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stat-cell{border:1px solid var(--line);border-radius:var(--radius-sm);padding:9px 11px;background:var(--panel-soft);min-width:0}
.stat-label{display:block;font-size:12px;color:var(--muted);margin-bottom:2px}
.stat-value{display:block;font-size:14px;font-weight:700;overflow-wrap:anywhere}
.context-meter{height:10px;border-radius:var(--radius-pill);background:var(--panel-strong);overflow:hidden;margin:4px 0 10px;border:1px solid var(--line)}
.context-bar{height:100%;width:0;background:var(--accent);transition:width var(--transition)}
.file-toolbar{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.file-toolbar button{min-height:38px;padding:6px 10px;font-size:13px}
.file-path{font-family:var(--font-mono);font-size:13px;background:var(--panel-soft);border:1px solid var(--line);padding:8px 10px;border-radius:var(--radius-sm);word-break:break-all}
.file-list{display:flex;flex-direction:column;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;margin-top:10px;background:var(--panel)}
.file-row{display:flex;align-items:center;gap:8px;padding:8px 10px;border-top:1px solid var(--line);flex-wrap:wrap}
.file-row:first-child{border-top:0}
.file-name{flex:1 1 180px;text-align:left;background:transparent;color:var(--text);border:0;padding:4px 0;min-height:36px;font-weight:600;overflow-wrap:anywhere;display:flex;align-items:center;gap:8px}
.file-name:hover:not(:disabled){background:transparent;color:var(--accent-strong);border:0;text-decoration:underline}
.file-meta{font-size:12px;color:var(--muted);font-family:var(--font-mono)}
.file-actions{display:flex;gap:4px;flex-wrap:wrap}
.file-actions button{font-size:12px;padding:4px 8px;min-height:32px;flex:0 0 auto}
.file-editor{margin-top:16px;border-top:1px solid var(--line);padding-top:12px}
.file-editor textarea{font-family:var(--font-mono);font-size:13px}
.skills-list{display:flex;flex-direction:column;gap:10px}
.skill-job{border:1px solid var(--line);border-radius:var(--radius);padding:12px 14px;background:var(--panel-soft)}
.skill-job h3{font-size:15px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}
.skill-job p{font-size:13px;color:var(--text-soft);margin:0}
.skill-job .skill-content{margin-top:8px}
.toast{position:fixed;left:50%;bottom:calc(var(--composer-h) + env(safe-area-inset-bottom) + 14px);transform:translateX(-50%);background:var(--text);color:var(--bg);padding:10px 16px;border-radius:var(--radius-pill);z-index:60;font-size:14px;font-weight:600;box-shadow:var(--shadow-lg);max-width:calc(100vw - 32px);text-align:center;animation:toast-in 160ms ease-out}
@keyframes toast-in{from{opacity:0;transform:translate(-50%,6px)}to{opacity:1;transform:translate(-50%,0)}}
@media (max-width:900px){
  .sidebar{transform:translateX(-100%)}
  body.sidebar-open .sidebar{transform:translateX(0)}
  body.sidebar-collapsed .sidebar{transform:translateX(-100%)}
  .sidebar-backdrop{display:block}
  .main-area{margin-left:0}
  body.sidebar-collapsed .main-area{margin-left:0}
  .composer{left:0}
  body.sidebar-collapsed .composer{left:0}
  .appbar-meta .status-text{display:none}
  .bubble{max-width:100%}
}
@media (max-width:640px){
  :root{--composer-h:84px}
  .appbar{padding:6px 10px}
  .appbar-btn{padding:6px 9px}
  .composer{padding:5px 8px calc(5px + env(safe-area-inset-bottom))}
  .composer-inner{gap:6px}
  .composer-options{gap:5px}
  .composer-thinking-label{display:none}
  .composer-thinking select{min-width:68px;min-height:36px;padding:4px 6px;font-size:14px}
  .auto-toggle{font-size:11px;gap:4px;min-height:36px;padding:0}
  .auto-toggle input{width:18px;height:18px}
  .composer textarea{flex:1 1 90px;min-width:80px;min-height:38px;padding:7px 8px}
  .composer button{min-width:54px;min-height:38px;padding:6px 8px;font-size:13px}
  .composer-options label{font-size:12px}
  .auth-card{padding:22px 16px 18px}
  .settings-card{padding:16px 14px;border-radius:var(--radius)}
  .file-name{flex:1 1 100%}
  .file-actions{flex:1 0 100%;justify-content:flex-end}
}
@media (min-width:720px){.auth-view{min-height:100vh}}
@media (min-width:901px){
  .sidebar-backdrop{display:none !important}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important;scroll-behavior:auto !important}}
"""

APP_JS = r"""'use strict';
var state = {
  status:null, config:null, mcp:null, streaming:false, deviceBusy:false,
  stopWaiting:false, current:null, reasoning:null, setup:false,
  setupInfo:null, authed:false, pendingKey:null, pendingEl:null,
  pendingCode:null, runningCode:null, consumedCodes:[], answeredQuestion:null,
  lastWifiError:'', activeStream:null, behaviorSaving:false, historyLoaded:false,
  backgroundKey:null, backgroundEl:null, backgroundPhase:null,
  backgroundTimer:null, backgroundResuming:false, autoContinueSeq:null,
  autoBusyNotice:false, liveUserEl:null, liveSeq:null, liveContent:'',
  liveReasoning:'', liveEventIndex:0, liveTimer:null, livePolling:false,
  liveGen:0, turnEl:null, scheduleKey:null, scheduleEditId:null,
  scheduleSeq:null, screen:'chat', settingsSection:'network',
  settingsLoaded:false, sidebarCollapsed:false, sidebarOpen:false,
  files:[], filePath:'/', fileEditorPath:null, skills:null
};
function $(id){return document.getElementById(id);}
function setText(el,text){el.textContent = text == null ? '' : String(text);}
function show(el, yes){if(el) el.hidden = !yes;}
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
  var empty=$('chatEmpty'); if(empty) empty.hidden=$('chatLog').childElementCount>0;
}
function chatVisible(){
  return !$('mainView').hidden&&state.screen==='chat'&&!$('screenChat').hidden;
}
function autoScrollChat(force){
  if(chatVisible()&&(force||pageNearBottom())) scrollPageBottom();
}
function syncComposerHeight(){
  var composer=$('chatForm');
  if(!composer||!chatVisible()) return;
  var height=composer.offsetHeight||0;
  if(height>0) document.documentElement.style.setProperty('--composer-h',height+'px');
}
function toast(text){
  var el=document.createElement('div');el.className='toast';
  el.setAttribute('role','status');el.textContent=text;
  document.body.appendChild(el);setTimeout(function(){el.remove();},2500);
}
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

/* ---------- navigation and sidebar ---------- */
var SCREEN_IDS = {
  status:'screenStatus', chat:'screenChat', background:'screenBackground',
  schedules:'screenSchedules', skills:'screenSkills', files:'screenFiles',
  settings:'screenSettings'
};
var SCREEN_TITLES = {
  status:'状态', chat:'对话', background:'后台任务',
  schedules:'自动化任务', skills:'Skill', files:'文件'
};
var SETTINGS_TITLES = {
  network:'网络', model:'模型', mcp:'MCP', search:'网页搜索',
  behavior:'Harness 行为', security:'安全', factory:'恢复出厂设置'
};
function isMobile(){
  if(window.matchMedia) return window.matchMedia('(max-width:900px)').matches;
  return window.innerWidth<=900;
}
function syncSidebar(){
  var mobile=isMobile();
  if(!mobile&&state.sidebarCollapsed){
    document.body.classList.add('sidebar-collapsed');
  } else {
    document.body.classList.remove('sidebar-collapsed');
  }
  if(mobile&&state.sidebarOpen){
    document.body.classList.add('sidebar-open');
  } else {
    document.body.classList.remove('sidebar-open');
  }
  show($('sidebarBackdrop'),mobile&&state.sidebarOpen);
}
function toggleSidebar(){
  if(isMobile()){state.sidebarOpen=!state.sidebarOpen;}
  else {
    state.sidebarCollapsed=!state.sidebarCollapsed;
    try{localStorage.setItem('harness-sidebar-collapsed',state.sidebarCollapsed?'1':'0');}catch(e){}
  }
  syncSidebar();
}
function closeSidebar(){
  if(isMobile()) state.sidebarOpen=false;
  syncSidebar();
}
function openGroup(name,open){
  var groups=document.querySelectorAll('.nav-group');
  for(var i=0;i<groups.length;i++){
    if(groups[i].getAttribute('data-group')!==name) continue;
    if(open===undefined) open=!groups[i].classList.contains('open');
    if(open) groups[i].classList.add('open');
    else groups[i].classList.remove('open');
    var sub=groups[i].querySelector('.nav-sub');
    if(sub) show(sub,open);
  }
}
function navKey(){
  return state.screen==='settings'?'settings-'+state.settingsSection:state.screen;
}
function updateNavActive(){
  var key=navKey();
  var items=document.querySelectorAll('.nav-item');
  for(var i=0;i<items.length;i++){
    var nav=items[i].getAttribute('data-nav');
    if(!nav) continue;
    if(nav===key) items[i].classList.add('active');
    else items[i].classList.remove('active');
  }
}
function showSettingsPanel(section){
  var panels=document.querySelectorAll('[data-settings-panel]');
  for(var i=0;i<panels.length;i++){
    show(panels[i],panels[i].getAttribute('data-settings-panel')===section);
  }
}
function onScreenShown(name,section){
  if(name==='status'){
    renderStatusScreen(state.status);
    refreshStatus().catch(function(){});
  } else if(name==='background'||name==='schedules'){
    if(state.status){syncBackground(state.status);syncSchedules(state.status);}
  } else if(name==='skills'){
    loadSkills().catch(function(e){toast(e.message);});
  } else if(name==='files'){
    loadFiles(state.filePath).catch(function(e){toast(e.message);});
  } else if(name==='settings'){
    ensureSettings(section||state.settingsSection);
  } else if(name==='chat'){
    updateEmptyState(); autoScrollChat(true); syncComposerHeight();
  }
}
function showScreen(name,section){
  if(!SCREEN_IDS[name]) name='chat';
  state.screen=name;
  if(name==='settings'&&section) state.settingsSection=section;
  var key;
  for(key in SCREEN_IDS){
    show($(SCREEN_IDS[key]),key===name);
  }
  if(name==='settings'){
    showSettingsPanel(state.settingsSection);
    setText($('screenTitle'),'设置 · '+(SETTINGS_TITLES[state.settingsSection]||'网络'));
    openGroup('settings',true);
  } else {
    setText($('screenTitle'),SCREEN_TITLES[name]||'对话');
  }
  if(name==='background'||name==='schedules') openGroup('tasks',true);
  updateNavActive();
  if(isMobile()){state.sidebarOpen=false;syncSidebar();}
  onScreenShown(name,state.settingsSection);
}

/* ---------- status ---------- */
function formatBytes(value){
  var number=Number(value);
  if(!isFinite(number)||number<0) return '-';
  if(number<1024) return number+' B';
  if(number<1024*1024) return (Math.round(number/1024*10)/10)+' KB';
  return (Math.round(number/1024/1024*10)/10)+' MB';
}
function renderStatusScreen(status){
  if(!status) return;
  var history=status.history||{};
  var percent=Number(history.percent);
  if(!isFinite(percent)) percent=0;
  percent=Math.max(0,Math.min(100,percent));
  setText($('statusLine'),(status.mode||'off')+' · '+(status.sta_ip||status.ap_ip||'-')+' · '+(status.version||''));
  setText($('statusMode'),status.mode||'off');
  setText($('statusIp'),status.sta_ip||status.ap_ip||'-');
  setText($('statusSsid'),status.sta_ssid||status.ap_ssid||'-');
  setText($('statusHeap'),formatBytes(status.heap_free));
  setText($('statusModel'),(status.model&&status.model.model)||'-');
  setText($('statusSessions'),status.sessions==null?'-':status.sessions);
  var wifiError=$('statusWifiError');
  if(status.wifi_error){setText(wifiError,'Wi-Fi: '+status.wifi_error);show(wifiError,true);}else{show(wifiError,false);}
  setText($('statusVersion'),'版本 '+(status.version||'-')+' · 固件运行正常时 heap 会随请求波动');
  var bar=$('contextBar'); if(bar) bar.style.width=percent+'%';
  setText($('contextText'),'消息 '+((history.messages==null)?'-':history.messages)+' 条 · '+
    ((history.chars==null)?'-':history.chars)+' 字符 · 约 '+
    ((history.estimated_tokens==null)?'-':history.estimated_tokens)+' tokens · '+
    (Math.round(percent*10)/10)+'%');
  var usage=(status.model&&status.model.usage)||{};
  var summaryText='压缩摘要 '+(history.summary_chars||0)+' 字符';
  if(usage.input_tokens||usage.output_tokens){
    summaryText+=' · 最近用量 in/out '+((usage.input_tokens||0))+'/'+((usage.output_tokens||0));
    if(usage.cache_read_tokens) summaryText+=' · 缓存读 '+(usage.cache_read_tokens||0);
    if(usage.cache_creation_tokens) summaryText+=' · 缓存写 '+(usage.cache_creation_tokens||0);
  }
  setText($('contextSummary'),summaryText);
  setText($('statusTurn'),status.turn_busy?'运行中':'空闲');
  var backgrounds=status.backgrounds||[];
  setText($('statusBackground'),backgrounds.length?backgrounds.length+' 个':'0');
  var quota=$('backgroundQuota');
  if(quota&&status.background_records!=null&&status.background_limit!=null){
    var records=status.background_records, limit=status.background_limit;
    var finished=backgrounds.filter(function(bg){
      return bg.phase==='ready'||bg.phase==='error';
    }).length;
    setText(quota,'记录 '+records+'/'+limit+' · 已完成 '+finished+' · 空闲 '+
      Math.max(0,limit-records)+
      (records>=limit?'（已达上限，请先继续或丢弃部分结果）':''));
  }
  setText($('statusLive'),status.live&&status.live.active?'接收中':'无');
  setText($('statusVersionShort'),status.version||'-');
}
function statusConnectionText(status){
  var mode=status.mode||'off';
  var ip=status.sta_ip||status.ap_ip||'-';
  var apNote='';
  if(status.ap_active&&status.ap_off_in_s>0){
    apNote=' · AP '+(status.ap_off_in_s>=120?Math.ceil(status.ap_off_in_s/60)+' 分钟后关闭':status.ap_off_in_s+' 秒后关闭');
  }
  return mode+' · '+ip+(status.model&&status.model.model?' · '+status.model.model:'')+apNote;
}
function renderStatus(status){
  state.status=status;
  state.deviceBusy=!!(status.turn_busy||(status.live&&status.live.active));
  syncStopButton();
  var text=statusConnectionText(status);
  setText($('modeText'),text);
  setText($('sideStatusText'),text);
  var health=status.wifi_error?'warn':(status.sta_ip?'online':(status.ap_active?'ap':'warn'));
  var title=status.wifi_error?'Wi-Fi: '+status.wifi_error:(status.sta_ip?'STA 已连接':(status.ap_active?'AP 模式':'网络未连接'));
  ['connDot','sideConnDot'].forEach(function(id){
    var dot=$(id);
    if(dot){dot.className='conn-dot '+health;dot.title=title;}
  });
  var autoState=status.auto||{};
  var auto=!!autoState.run;
  show($('autoBadge'),auto);
  syncBehaviorControls(!!autoState.run,(status.thinking||{}).level);
  if(status.wifi_error&&state.lastWifiError!==status.wifi_error){toast('Wi-Fi: '+status.wifi_error);state.lastWifiError=status.wifi_error;}
  else if(!status.wifi_error){state.lastWifiError='';}
  renderStatusScreen(status);
  maybeShowPending(status);
  syncBackground(status);
  syncSchedules(status);
  var liveActive=!!(status.live&&status.live.active);
  if(!state.streaming&&((status.turn_busy&&(status.busy_source==='auto-continue'||status.busy_source==='schedule'))||liveActive)){
    state.autoBusyNotice=false;
    startLivePoll();
  } else {
    state.autoBusyNotice=false;
    stopLivePoll();
  }
  var scheduleSeq=status.schedule_seq;
  if(scheduleSeq!=null){
    if(state.scheduleSeq==null){state.scheduleSeq=scheduleSeq;}
    else if(scheduleSeq!==state.scheduleSeq){
      state.scheduleSeq=scheduleSeq;
      if(state.historyLoaded&&!state.streaming){refreshHistory().catch(function(){});}
    }
  }
  var continueSeq=status.auto&&status.auto.continue_seq;
  if(continueSeq!=null){
    if(state.autoContinueSeq==null){state.autoContinueSeq=continueSeq;}
    else if(continueSeq!==state.autoContinueSeq){
      state.autoContinueSeq=continueSeq;
      if(state.historyLoaded&&!state.streaming){refreshHistory().catch(function(){});}
    }
  }
}
function refreshStatus(){
  return api('/api/status').then(function(status){renderStatus(status);return status;}).catch(function(err){if(err.status===401)showLogin();throw err;});
}
function refreshCurrentScreen(){
  var promise;
  if(state.screen==='files') promise=loadFiles(state.filePath);
  else if(state.screen==='skills') promise=loadSkills();
  else if(state.screen==='settings') promise=loadConfig().then(function(){return loadMcp();});
  else promise=refreshStatus().then(function(){if(state.screen==='chat') return refreshHistory();});
  promise.then(function(){toast('已刷新');}).catch(function(err){toast(err.message);});
}

/* ---------- chat bubbles ---------- */
function startTurn(){
  var turn=document.createElement('div'); turn.className='turn';
  $('chatLog').appendChild(turn);
  state.turnEl=turn; state.current=null; state.reasoning=null;
  return turn;
}
function ensureTurn(){
  if(!state.turnEl||!document.body.contains(state.turnEl)) startTurn();
  return state.turnEl;
}
function addBubble(cls,label){
  var stick=pageNearBottom();
  var el=document.createElement('div'); el.className='bubble '+(cls||'assistant');
  if(label){
    var heading=document.createElement('div'); heading.className='bubble-label';
    heading.textContent=label; el.appendChild(heading);
  }
  ensureTurn().appendChild(el); updateEmptyState(); scrollIfStick(stick); return el;
}
function bubbleBody(el,text){
  var body=document.createElement('div'); body.className='bubble-body';
  body.textContent=text==null?'':String(text); el.appendChild(body); return body;
}
function logMessage(role, text, cls){
  if(role==='user'){
    startTurn();
    var el=addBubble('user','你'); bubbleBody(el,text);
    return el;
  }
  state.current=null;
  var system=addBubble((cls||'system'),'');
  bubbleBody(system,text);
  return system;
}
function card(title){
  return addBubble('card-bubble',title);
}
function collapsibleBlock(summaryText, text, open){
  var details=document.createElement('details'); details.className='reasoning collapsible';
  if(open===true) details.open=true;
  var summary=document.createElement('summary'); summary.textContent=summaryText; details.appendChild(summary);
  var pre=document.createElement('pre'); pre.textContent=text || ''; details.appendChild(pre);
  return details;
}
function collapsedMessage(summaryText, text){
  var el=addBubble('compact','');
  el.appendChild(collapsibleBlock(summaryText,text,false));
  return el;
}
function blockLabel(language){
  var labels={python:'Python 代码',py:'Python 代码',micropython:'MicroPython 代码',
    tool:'工具调用',tool_call:'工具调用',toolcall:'工具调用','tool-call':'工具调用',
    skill:'Skill 提案'};
  return labels[language.toLowerCase()] || language || '代码';
}
function assistantSegments(text){
  var segments=[], cursor=0;
  while(cursor<text.length){
    var start=text.indexOf('```',cursor);
    if(start<0){segments.push({kind:'text',text:text.slice(cursor)});break;}
    if(start>cursor) segments.push({kind:'text',text:text.slice(cursor,start)});
    var lineEnd=text.indexOf('\n',start+3);
    if(lineEnd<0){
      segments.push({kind:'code',label:'代码块',content:text.slice(start+3),open:true});
      break;
    }
    var language=text.slice(start+3,lineEnd).trim().split(/\s+/)[0];
    var close=text.indexOf('```',lineEnd+1);
    if(close<0){
      segments.push({kind:'code',label:blockLabel(language),content:text.slice(lineEnd+1),open:true});
      break;
    }
    segments.push({kind:'code',label:blockLabel(language),content:text.slice(lineEnd+1,close),open:false});
    cursor=close+3;
  }
  return segments;
}
function renderAssistantBody(b){
  var openStates=[];
  var oldDetails=b.el.querySelectorAll('details');
  for(var i=0;i<oldDetails.length;i++) openStates.push(oldDetails[i].open===true);
  b.el.textContent=''; b.body=null; b.tailKind='';
  var segments=assistantSegments(b.text);
  var detailIndex=0;
  for(var s=0;s<segments.length;s++){
    var segment=segments[s];
    if(segment.kind==='text'){
      if(!segment.text||!segment.text.trim()) continue;
      var el=document.createElement('div'); el.className='bubble assistant';
      var body=document.createElement('div'); body.className='bubble-body';
      body.textContent=segment.text; el.appendChild(body);
      b.el.appendChild(el); b.body=body; b.tailKind='text';
    } else {
      var codeEl=document.createElement('div'); codeEl.className='bubble assistant code-bubble';
      var label=document.createElement('div'); label.className='bubble-label';
      label.textContent=segment.label; codeEl.appendChild(label);
      var summary=(segment.open?'代码接收中（':'展开代码（')+segment.content.length+' 字符）';
      codeEl.appendChild(collapsibleBlock(
        summary,segment.content,openStates[detailIndex]===true));
      detailIndex+=1;
      b.el.appendChild(codeEl); b.tailKind='code'; b.body=null;
    }
  }
  b.fenceCount=b.text.split('```').length-1;
}
function assistantAnswer(){
  var host=document.createElement('div'); host.className='turn-answer';
  ensureTurn().appendChild(host);
  return {el:host,text:'',body:null,tailKind:'',fenceCount:0};
}
function ensureAssistant(){if(!state.current) state.current=assistantAnswer();return state.current;}
function appendAssistantText(text){
  var chunk=text||'';
  if(!chunk) return;
  var stick=pageNearBottom();
  var b=ensureAssistant();
  b.text+=chunk;
  var fences=b.text.split('```').length-1;
  if(fences%2===0&&chunk.indexOf('```')<0&&b.tailKind==='text'&&b.body){
    b.body.appendChild(document.createTextNode(chunk));
    b.fenceCount=fences;
    scrollIfStick(stick);
    return;
  }
  renderAssistantBody(b);
  scrollIfStick(stick);
}
function setAssistantText(text){
  var stick=pageNearBottom();
  var b=ensureAssistant(); b.text=text||''; renderAssistantBody(b);
  scrollIfStick(stick);
}
function reasoningBubble(){
  var el=addBubble('reasoning','');
  var details=collapsibleBlock('思考过程 (0 字)','',false);
  el.appendChild(details);
  return {
    el:el, details:details, pre:details.querySelector('pre'),
    summary:details.querySelector('summary'), chars:0
  };
}
function appendReasoning(chunk){
  var text=chunk||'';
  if(!text) return;
  var stick=pageNearBottom();
  if(!state.reasoning) state.reasoning=reasoningBubble();
  var reasoning=state.reasoning;
  var pre=reasoning.pre;
  var nearBottom=pre.scrollHeight-pre.scrollTop-pre.clientHeight<24;
  var previousTop=pre.scrollTop;
  pre.appendChild(document.createTextNode(text));
  reasoning.chars+=text.length;
  reasoning.summary.textContent='思考过程 ('+reasoning.chars+' 字)';
  pre.scrollTop=nearBottom?pre.scrollHeight:previousTop;
  scrollIfStick(stick);
}
function toolCallBubble(name,args){
  var text=typeof args==='string'?args:JSON.stringify(args||{});
  var el=addBubble('tool','工具调用 · '+(name||''));
  el.appendChild(collapsibleBlock('参数（'+text.length+' 字符）',text,text.length<=200));
}
function toolResultBubble(name,ok,output,error,elapsed){
  var text=(output||'')+(error?'\n'+error:'');
  if(!text) text='<无输出>';
  var label='工具结果 · '+(name||'')+' · '+(ok?'OK':'ERROR');
  if(elapsed!=null) label+=' · '+elapsed+'ms';
  var el=addBubble('tool'+(ok?'':' err'),label);
  el.appendChild(collapsibleBlock('输出（'+text.length+' 字符）',text,false));
}
function modelMeta(text){
  var turn=ensureTurn();
  var bubbles=turn.querySelectorAll('.bubble');
  var target=bubbles.length?bubbles[bubbles.length-1]:null;
  if(!target){
    var el=addBubble('meta-bubble',''); el.textContent=text; return;
  }
  var meta=document.createElement('span'); meta.className='bubble-meta';
  meta.textContent=text; target.appendChild(meta);
}

/* ---------- pending cards ---------- */
function questionKey(ev){return (ev.header||'')+'\n'+(ev.question||'');}
function codeConsumed(source){
  var key=String(source||'');
  for(var i=0;i<state.consumedCodes.length;i++){
    if(state.consumedCodes[i]===key) return true;
  }
  return false;
}
function markCodeConsumed(source){
  var key=String(source||'');
  if(!key||codeConsumed(key)) return;
  state.consumedCodes.push(key);
  if(state.consumedCodes.length>8) state.consumedCodes.shift();
}
function unmarkCodeConsumed(source){
  var key=String(source||'');
  var kept=[];
  for(var i=0;i<state.consumedCodes.length;i++){
    if(state.consumedCodes[i]!==key) kept.push(state.consumedCodes[i]);
  }
  state.consumedCodes=kept;
}
function maybeShowPending(status){
  if(state.streaming) return;
  var q=status&&status.question;
  if(q&&q.question){
    if(state.answeredQuestion===questionKey(q)) return;
    var attached=state.pendingEl&&document.body.contains(state.pendingEl);
    if(state.pendingKey!=='question'||!attached) renderQuestion(q);
    return;
  }
  var p=status&&status.pending;
  if(!p||!p.kind||p.kind==='none') return;
  if(p.kind==='code'&&p.code){
    if(codeConsumed(p.code)) return;
    if(state.pendingKey!=='code'){
      state.pendingKey='code';
      renderCode({source:p.code,safe:p.safe,auto:false,timeout_s:p.timeout_s});
    }
    return;
  }
  if(p.kind==='skill'&&p.skill){
    var skillKey='skill:'+(p.skill.name||'');
    if(state.pendingKey!==skillKey){state.pendingKey=skillKey;renderProposal(p.skill);}
  }
}
function disableQuestions(){
  var cards=document.querySelectorAll('.question-card');
  for(var i=0;i<cards.length;i++){
    cards[i].classList.add('answered');
    if(cards[i]._questionKey) state.answeredQuestion=cards[i]._questionKey;
    var buttons=cards[i].querySelectorAll('button');
    for(var j=0;j<buttons.length;j++) buttons[j].disabled=true;
  }
  if(state.pendingKey==='question'){state.pendingKey=null;state.pendingEl=null;}
}
function renderQuestion(ev){
  var el=card('模型提问: '+(ev.header||'问题'));
  el.classList.add('question-card');
  el._questionKey=questionKey(ev);
  state.answeredQuestion=null;
  var text=document.createElement('p'); text.className='question-text';
  text.textContent=ev.question||''; el.appendChild(text);
  var options=ev.options||[];
  if(options.length){
    var actions=document.createElement('div'); actions.className='actions question-options';
    options.forEach(function(option,index){
      var label=typeof option==='string'?option:(option.label||'');
      var description=typeof option==='string'?'':(option.description||'');
      var button=document.createElement('button');
      button.type='button'; button.textContent=(index+1)+'. '+label;
      if(description) button.title=description;
      button.onclick=function(){answerQuestion(label,el);};
      actions.appendChild(button);
    });
    el.appendChild(actions);
    if(options.some(function(option){return typeof option!=='string'&&option.description;})){
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
function disableCodeCards(){
  var cards=document.querySelectorAll('.code-card');
  for(var i=0;i<cards.length;i++){
    var actions=cards[i].querySelector('.actions');
    if(actions) actions.remove();
  }
  if(state.pendingKey==='code'){state.pendingKey=null;state.pendingEl=null;}
  state.pendingCode=null;
}
function removeCodeCards(source){
  var cards=document.querySelectorAll('.code-card');
  for(var i=0;i<cards.length;i++){
    if(cards[i]._codeSource===source) cards[i].remove();
  }
}
function renderCode(ev){
  var runnable=ev.runnable!==false;
  var source=ev.source||'';
  var title=runnable?(ev.auto?'代码（自动模式）':('待执行代码'+(ev.safe===false?'（检测到明显无限循环，需手动运行）':''))):'代码未调度';
  if(ev.timeout_s!=null) title+=' · 超时 '+(ev.timeout_s===0?'∞':ev.timeout_s+'s');
  var el=card(title);
  el.classList.add('code-card');
  el._codeSource=source;
  state.pendingCode=null;
  el.appendChild(collapsibleBlock('查看代码（'+source.length+' 字符）',source,false));
  if(!runnable){
    var blocked=document.createElement('p');blocked.className='hint';
    blocked.textContent='这段代码没有排入执行；可以让助手重新生成，或直接在下方输入新消息。';
    el.appendChild(blocked);return;
  }
  if(ev.auto){
    var note=document.createElement('p');note.className='hint';
    note.textContent='自动模式已开启，代码已提交执行。';el.appendChild(note);return;
  }
  state.pendingKey='code'; state.pendingEl=el; state.pendingCode=source;
  var actions=document.createElement('div'); actions.className='actions';
  var backgroundEnabled=!(state.status&&state.status.auto&&state.status.auto.background===false);
  if(ev.timeout_s!==0){
    var run=document.createElement('button'); run.textContent='前台运行';
    run.onclick=function(){runPending('foreground',source);};
    actions.appendChild(run);
  }
  if(backgroundEnabled){
    var bgRun=document.createElement('button'); bgRun.type='button'; bgRun.textContent='后台运行';
    bgRun.onclick=function(){runPending('background',source);};
    actions.appendChild(bgRun);
  }
  var discard=document.createElement('button'); discard.className='ghost'; discard.textContent='丢弃';
  discard.onclick=function(){
    markCodeConsumed(source);
    post('/api/discard',{}).then(function(){
      removeCodeCards(source);
      state.pendingKey=null; state.pendingEl=null; state.pendingCode=null;
      toast('已丢弃'); refreshStatus().catch(function(){});
    }).catch(function(e){toast(e.message);});
  };
  actions.appendChild(discard); el.appendChild(actions);
}
function renderProposal(ev){
  var el=card('Skill 提案: '+(ev.name||''));
  el.classList.add('skill-card');
  var content=ev.body||'';
  el.appendChild(collapsibleBlock('查看完整提案（'+content.length+' 字符）',content,false));
  state.pendingKey='skill:'+(ev.name||''); state.pendingEl=el;
  var actions=document.createElement('div'); actions.className='actions';
  var save=document.createElement('button'); save.textContent='保存';
  save.onclick=function(){
    post('/api/save-skill',{}).then(function(){
      state.pendingKey=null;state.pendingEl=null;el.remove();
      toast('已保存');refreshStatus();
    }).catch(function(e){toast(e.message);});
  };
  var discard=document.createElement('button'); discard.className='ghost'; discard.textContent='丢弃';
  discard.onclick=function(){
    post('/api/discard',{}).then(function(){
      state.pendingKey=null;state.pendingEl=null;el.remove();
      toast('已丢弃');refreshStatus().catch(function(){});
    }).catch(function(e){toast(e.message);});
  };
  actions.appendChild(save);actions.appendChild(discard);el.appendChild(actions);
}

/* ---------- streaming and events ---------- */
function handleEvent(ev){
  if(!ev||!ev.t) return;
  if(ev.t==='model_start'){
    state.current=null; state.reasoning=null;
    return;
  }
  if(ev.t==='reasoning'){
    if(!state.reasoning) state.current=null;
    appendReasoning(ev.d||'');
    return;
  }
  if(ev.t==='content'){appendAssistantText(ev.d||'');return;}
  if(ev.t==='notice'){
    logMessage('system',ev.d||'','system'); return;
  }
  if(ev.t==='model'){
    var text='模型: '+(ev.ok?'OK':'ERROR')+(ev.finish?' finish='+ev.finish:'')+(ev.elapsed_ms!=null?' '+ev.elapsed_ms+'ms':'');
    if(ev.first_token_ms) text+=' 首字='+ev.first_token_ms+'ms';
    if(ev.token_count) text+=' chunks='+ev.token_count;
    if(ev.max_gap_ms) text+=' 最大间隔='+ev.max_gap_ms+'ms';
    if(ev.tail_gap_ms) text+=' 尾间隔='+ev.tail_gap_ms+'ms';
    if(ev.input_tokens||ev.output_tokens) text+=' tokens='+(ev.input_tokens||0)+'/'+(ev.output_tokens||0);
    if(ev.cache_read_tokens) text+=' cache_read='+ev.cache_read_tokens;
    if(ev.error) text+=' '+ev.error;
    modelMeta(text);
    return;
  }
  if(ev.t==='tool'){
    state.current=null;
    if(ev.phase==='start') toolCallBubble(ev.name,ev.args||{});
    else toolResultBubble(ev.name,!!ev.ok,ev.output||'',ev.error||'',ev.elapsed_ms);
    return;
  }
  if(ev.t==='response'){
    if(!state.current&&ev.text) setAssistantText(ev.text);
    else if(state.current&&ev.text&&!state.current.text) setAssistantText(ev.text);
    return;
  }
  if(ev.t==='code'){state.current=null;renderCode(ev);return;}
  if(ev.t==='execution'){state.current=null;renderExecution(ev);return;}
  if(ev.t==='skill'){state.current=null;renderProposal(ev);return;}
  if(ev.t==='question'){state.current=null;renderQuestion(ev);return;}
  if(ev.t==='feedback'){
    state.current=null;
    logMessage('system','自动反馈第 '+(ev.turn||'?')+' 轮...','system');return;
  }
  if(ev.t==='background'){
    if(!state.backgroundTimer) state.backgroundTimer=setTimeout(pollBackground,500);
    return;
  }
  if(ev.t==='done'){
    state.current=null; state.runningCode=null;
    if(state.liveUserEl){attachRewindButton(state.liveUserEl,ev.user_index);state.liveUserEl=null;}
    if(ev.pending&&ev.pending!=='none'&&ev.pending!=='question'){
      logMessage('system','有待处理项: '+ev.pending,'system');
    }
    return;
  }
  if(ev.t==='error'){logMessage('system','错误: '+(ev.message||ev.code||''),'system');return;}
}
function renderExecution(ev){
  var resultText=ev.cancelled?'已停止':(ev.timed_out?'超时停止':(ev.ok?'成功':'失败'));
  var el=card('执行结果: '+resultText+' ('+(ev.elapsed_ms||0)+' ms)');
  el.classList.add('execution-card');
  var output=(ev.output||'')+(ev.error?'\n'+ev.error:'')||'<无输出>';
  el.appendChild(collapsibleBlock('查看执行输出（'+output.length+' 字符）',output,false));
}
function stream(path, value){
  state.stopWaiting=false;
  setBusy(true); state.current=null; state.reasoning=null;
  var controller=null;
  try{controller=new AbortController();}catch(e){}
  var active={controller:controller, stopped:false};
  state.activeStream=active;
  var options={method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Harness':'1'},body:JSON.stringify(value||{})};
  if(controller) options.signal=controller.signal;
  return fetch(path,options)
  .then(function(response){
    if(!response.ok){
      return response.text().then(function(text){
        var data={};try{data=JSON.parse(text);}catch(e){}
        var err=new Error(data.error||('HTTP '+response.status));
        err.status=response.status;throw err;
      });
    }
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
        buffer+=decoder.decode(result.value,{stream:true});
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
    if(active.stopped){waitForStoppedTurn(active);return;}
    state.activeStream=null;
    state.stopWaiting=false;
    state.answeredQuestion=null;
    var staleCode=String(err.message||'').indexOf('No pending code')>=0;
    if(staleCode){disableCodeCards();}
    else if(state.runningCode){unmarkCodeConsumed(state.runningCode);}
    state.runningCode=null;
    setBusy(false);
    logMessage('system','请求失败: '+err.message,'system');
    if(err.status===401){showLogin();}
    refreshStatus().then(function(){
      if(staleCode){refreshHistory().catch(function(){});}
    }).catch(function(){});
  });
}
function waitForStoppedTurn(active){
  if(state.activeStream!==active||active.waiting) return;
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
    state.stopWaiting=false;
    setBusy(false);
    logMessage('system','设备已结束当前回合。','system');
    refreshHistory().catch(function(){});
  }).catch(function(err){
    if(state.activeStream!==active) return;
    if(err.status===401){
      state.activeStream=null;state.stopWaiting=false;setBusy(false);showLogin();return;
    }
    setTimeout(function(){pollStoppedTurn(active);},1500);
  });
}
function pollDeviceStop(){
  if(!state.authed) return;
  refreshStatus().then(function(status){
    var busy=!!(status.turn_busy||(status.live&&status.live.active));
    if(busy){state.stopWaiting=true;syncStopButton();setTimeout(pollDeviceStop,1000);return;}
    state.stopWaiting=false;
    refreshHistory().catch(function(){});
  }).catch(function(){setTimeout(pollDeviceStop,1500);});
}
function stopStream(){
  var active=state.activeStream;
  if(active&&active.stopped) return;
  if(!active){
    if(!state.deviceBusy||state.stopWaiting) return;
    state.stopWaiting=true;
    syncStopButton();
    logMessage('system','已请求停止当前回合，等待设备结束。','system');
    post('/api/stop',{}).then(function(data){
      toast(data.message||'已请求停止');pollDeviceStop();
    }).catch(function(err){
      state.stopWaiting=false;syncStopButton();toast(err.message);
    });
    return;
  }
  active.stopped=true;
  state.stopWaiting=true;
  syncStopButton();
  logMessage('system','已停止接收响应；设备上已开始的代码不会中断，正在等待设备结束。','system');
  if(active.controller){try{active.controller.abort();}catch(e){}}
  waitForStoppedTurn(active);
}
function runPending(mode, source){
  if(state.streaming)return;
  source=source||state.pendingCode;
  if(source) markCodeConsumed(source);
  state.runningCode=source||null;
  disableCodeCards();
  stream('/api/run',{mode:mode||'background'});
}

/* ---------- history ---------- */
function attachRewindButton(el,index){
  if(!el||index==null||el.querySelector('.rewind-btn')) return;
  var actions=document.createElement('div'); actions.className='msg-actions';
  var button=document.createElement('button'); button.type='button';
  button.className='ghost rewind-btn'; button.textContent='回退到这里';
  button.onclick=function(){rewindContext({index:index});};
  actions.appendChild(button); el.appendChild(actions);
}
function rewindContext(payload){
  if(state.streaming) return;
  if(!confirm('回退会删除这条用户消息及其后的所有回复（包括工具结果和执行结果）。确定继续？')) return;
  post('/api/rewind',payload||{count:1}).then(function(data){
    state.pendingKey=null;state.pendingEl=null;state.liveUserEl=null;
    toast('已回退 '+(data.removed||0)+' 条消息');
    return refreshHistory();
  }).then(function(){return refreshStatus();}).catch(function(err){toast(err.message);});
}
function renderNativeToolCalls(content){
  var lines=String(content||'').split('\n');
  for(var i=0;i<lines.length;i++){
    var line=lines[i];
    var match=line.match(/^Native tool call:\s*(\S+)\s*(.*)$/);
    if(match) toolCallBubble(match[1],match[2]||'{}');
    else if(line.trim()) logMessage('system',line,'system');
  }
}
function refreshHistory(){
  if(state.streaming) return Promise.resolve();
  stopLivePoll(); state.liveSeq=null; state.liveContent=''; state.liveReasoning=''; state.liveEventIndex=0;
  return api('/api/history?limit=200').then(function(data){
    $('chatLog').innerHTML='';
    state.current=null; state.reasoning=null; state.pendingKey=null; state.pendingEl=null;
    state.pendingCode=null; state.liveUserEl=null; state.turnEl=null;
    state.backgroundEl=null; state.backgroundKey=null; state.backgroundPhase=null;
    function renderObservation(content){
      if(content.indexOf("Local tool result for '")===0){
        var match=content.match(/^Local tool result for '([^']+)'/);
        toolResultBubble(match?match[1]:'tool',true,content,'',null);
        return;
      }
      var label='本地记录';
      if(content.indexOf('Local ESP32 background execution result')===0){
        label='后台任务结果'; startTurn();
      }
      else if(content.indexOf('Local ESP32')===0) label='执行结果';
      else if(content.indexOf('Local harness event')===0) label='Harness 记录';
      collapsedMessage(label,content);
    }
    (data.messages||[]).forEach(function(message){
      var role=message.role==='assistant'?'assistant':'user';
      if(message.role==='user'&&message.kind==='observation'){
        renderObservation(message.content||''); return;
      }
      if(role==='assistant'){
        var content=message.content||'';
        var reasoning=message.reasoning||'';
        var native=content.indexOf('Native tool call:')===0;
        if(reasoning){
          var reasoningEl=addBubble('reasoning','');
          reasoningEl.appendChild(collapsibleBlock('思考过程 ('+reasoning.length+' 字)',reasoning,false));
        }
        if(native) renderNativeToolCalls(content);
        else if(content.trim()){
          var bubble=assistantAnswer(); bubble.text=content; renderAssistantBody(bubble);
        }
      } else {
        var el=logMessage(role,message.content,role);
        if(message.kind==='user') attachRewindButton(el,message.index);
      }
    });
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
    refreshStatus().catch(function(){});
    autoScrollChat(true);
  }).catch(function(err){if(err.status===401)showLogin();throw err;});
}

/* ---------- live continuation polling ---------- */
function stopLivePoll(){
  if(state.liveTimer){clearTimeout(state.liveTimer);state.liveTimer=null;}
  state.livePolling=false;
  state.liveGen+=1;
  state.liveSeq=null; state.liveContent=''; state.liveReasoning=''; state.liveEventIndex=0;
}
function startLivePoll(){
  if(state.liveTimer||state.livePolling||state.streaming) return;
  state.liveGen+=1; state.livePolling=true; pollLive();
}
function pollLive(){
  var gen=state.liveGen;
  state.liveTimer=null;
  if(!state.authed||state.streaming){state.livePolling=false;return;}
  if(document.hidden){state.livePolling=false;state.liveTimer=setTimeout(pollLive,800);return;}
  state.livePolling=true;
  api('/api/live').then(function(data){
    if(gen!==state.liveGen||state.streaming) return;
    data=data||{};
    if(!data.id){state.livePolling=false;state.liveTimer=setTimeout(pollLive,400);return;}
    if(state.liveSeq!==data.id){
      state.liveSeq=data.id; state.liveContent=''; state.liveReasoning=''; state.liveEventIndex=0;
      state.current=null; state.reasoning=null;
      if(data.separate_turn) startTurn();
    }
    var content=data.content||'';
    var reasoning=data.reasoning||'';
    var replace=content!==state.liveContent&&content.indexOf(state.liveContent)!==0;
    if(replace){
      state.current=null; state.reasoning=null;
      state.liveContent=''; state.liveReasoning='';
    }
    var events=data.events||[];
    var base=data.event_base||0;
    var pos=state.liveContent.length;
    for(var i=0;i<events.length;i++){
      var absolute=base+i;
      if(absolute<state.liveEventIndex) continue;
      var ev=events[i];
      var cut=typeof ev.c==='number'?ev.c:content.length;
      if(cut<pos) cut=pos;
      if(cut>content.length) cut=content.length;
      if(cut<=pos){handleEvent(ev);state.liveEventIndex=absolute+1;}
    }
    if(reasoning.length>state.liveReasoning.length&&
       reasoning.slice(0,state.liveReasoning.length)===state.liveReasoning){
      handleEvent({t:'reasoning',d:reasoning.slice(state.liveReasoning.length)});
    }
    state.liveReasoning=reasoning;
    for(var j=0;j<events.length;j++){
      var absolute2=base+j;
      if(absolute2<state.liveEventIndex) continue;
      var ev2=events[j];
      var cut2=typeof ev2.c==='number'?ev2.c:content.length;
      if(cut2<pos) cut2=pos;
      if(cut2>content.length) cut2=content.length;
      if(cut2>pos){handleEvent({t:'content',d:content.slice(pos,cut2)});pos=cut2;}
      handleEvent(ev2);
      state.liveEventIndex=absolute2+1;
    }
    if(pos<content.length) handleEvent({t:'content',d:content.slice(pos)});
    state.liveContent=content;
    if(data.active){state.livePolling=false;state.liveTimer=setTimeout(pollLive,250);}
    else {
      state.livePolling=false;
      setTimeout(function(){refreshStatus().catch(function(){});},350);
    }
  }).catch(function(){
    if(gen!==state.liveGen||state.streaming) return;
    state.livePolling=false;state.liveTimer=setTimeout(pollLive,1200);
  });
}

/* ---------- composer behavior and commands ---------- */
function syncBehaviorControls(run, level){
  if(state.behaviorSaving) return;
  if(level==='default'||level==='medium'||level==='middle') level='high';
  if(level!=='disabled'&&level!=='low'&&level!=='high'&&level!=='max') level='high';
  $('chatThinking').value=level;
  $('chatAuto').checked=!!run;
  $('chatAuto').indeterminate=false;
}
function restoreChatBehavior(){
  var cfg=state.config||{};
  var status=state.status||{};
  var auto=status.auto||{};
  var thinking=status.thinking||{};
  var run=auto.run===undefined?!!cfg.auto_run:!!auto.run;
  syncBehaviorControls(run,thinking.level||cfg.thinking_level||'high');
}
function syncBehaviorDisabled(){
  var disabled=state.streaming||state.behaviorSaving;
  $('chatThinking').disabled=disabled;
  $('chatAuto').disabled=disabled;
}
function setBehaviorSaving(saving){state.behaviorSaving=saving;syncBehaviorDisabled();}
function changeThinking(level){
  setBehaviorSaving(true);
  return post('/api/config',{thinking_level:level}).then(function(){
    if(state.config) state.config.thinking_level=level;
    setBehaviorSaving(false);
    toast('Thinking 已设为 '+level);
    refreshStatus().catch(function(){});
  }).catch(function(err){
    setBehaviorSaving(false);restoreChatBehavior();toast(err.message);
    refreshStatus().catch(function(){});
  });
}
function changeAuto(enable){
  if(enable&&!confirm('AUTO 会自动运行模型生成的代码，绕过逐次确认且拥有设备完整权限。确定开启？')){
    restoreChatBehavior();return Promise.resolve(false);
  }
  var feedback=state.config&&typeof state.config.auto_feedback==='boolean'?state.config.auto_feedback:
    (state.status&&state.status.auto&&typeof state.status.auto.feedback==='boolean'?state.status.auto.feedback:true);
  setBehaviorSaving(true);
  return post('/api/auto',{auto_run:enable,auto_feedback:feedback,confirm:true}).then(function(){
    if(state.config){state.config.auto_run=enable;}
    setBehaviorSaving(false);
    toast(enable?'AUTO 已开启':'AUTO 已关闭');
    refreshStatus().catch(function(){});
    return true;
  }).catch(function(err){
    setBehaviorSaving(false);restoreChatBehavior();toast(err.message);
    refreshStatus().catch(function(){});
    return false;
  });
}
function syncStopButton(){
  show($('stopBtn'),!!(state.streaming||state.deviceBusy)&&!state.stopWaiting);
  syncComposerHeight();
}
function setBusy(busy){
  state.streaming=busy; $('sendBtn').disabled=busy; $('chatInput').disabled=busy;
  syncStopButton(); syncBehaviorDisabled();
  ['refreshBtn','historyBtn','clearBtn','compactBtn','testWifiBtn','testApiBtn'].forEach(function(id){
    var el=$(id); if(el) el.disabled=busy;
  });
}
function commandAffectsHistory(line){
  var command=String(line||'').split(/\s+/)[0].toLowerCase();
  return command==='/clear'||command==='/compact'||command==='/undo'||
    command==='/rewind'||command==='/run'||command==='/continue'||
    command==='/workspace';
}
function commandEcho(line){
  var el=addBubble('user command-echo','命令');
  bubbleBody(el,line);
  scrollPageBottomSoon();
}
function commandOutput(text,isError){
  if(!text) return;
  state.current=null;
  var el=addBubble('system command-output'+(isError?' error':''),'');
  bubbleBody(el,text);
  scrollPageBottomSoon();
}
function runCommand(line, confirmed){
  post('/api/command',{line:line,confirm:!!confirmed}).then(function(data){
    if(data.ok===false){
      commandOutput(data.output||'','');
      commandOutput('错误: '+(data.error||'命令失败'),true);
    } else if(data.output){
      commandOutput(data.output,false);
    }
    refreshStatus().catch(function(){});
    if(commandAffectsHistory(line)){refreshHistory().catch(function(){});}
  }).catch(function(err){
    var data=err.data||{};
    if(data.code==='confirm'&&!confirmed){
      if(window.confirm((data.error||'确认执行该命令？')+'\n\n'+line)){
        commandOutput(data.output||'','');
        runCommand(line,true);return;
      }
      commandOutput('已取消: '+line,false);return;
    }
    commandOutput('错误: '+err.message,true);
  });
}
function sendChat(event){
  if(event) event.preventDefault();
  if(state.streaming) return;
  var input=$('chatInput'); var text=input.value.trim(); if(!text) return;
  input.value='';
  if(text.charAt(0)==='/'){
    commandEcho(text);
    runCommand(text,false);
    return;
  }
  disableQuestions();
  state.liveUserEl=logMessage('user',text,'user');
  scrollPageBottomSoon();
  stream('/api/chat',{text:text});
}

/* ---------- background jobs ---------- */
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
function stopBackgroundPoll(){
  if(state.backgroundTimer){clearTimeout(state.backgroundTimer);state.backgroundTimer=null;}
}
function pollBackground(){
  state.backgroundTimer=null;
  if(!state.authed||document.hidden)return;
  refreshStatus().catch(function(){});
}
function backgroundStateLabel(bg){
  if(bg.phase==='queued'||bg.phase==='running'){
    if(bg.discard_on_finish) return '停止并丢弃中';
    if(bg.stop_requested) return '停止中';
  }
  if(bg.phase==='queued') return '排队中';
  if(bg.phase==='running') return '运行中';
  if(bg.phase==='ready'){
    var r=bg.result||{};
    return r.cancelled?'已停止':(r.timed_out?'已超时':(r.ok?'成功':'失败'));
  }
  return '失败';
}
function stopBackground(id, discard){
  var message=discard
    ? '请求停止后台程序并丢弃结果？停止仍依赖协作检查点；阻塞中的 C/SPI 调用可能只能等待看门狗，结果不会进入对话。'
    : '请求停止后台程序？只有执行到协作检查点的代码会停止；阻塞中的 C/SPI 调用可能只能等待看门狗。';
  if(!confirm(message))return;
  var body=id==null?{}:{id:id};
  if(discard) body.discard=true;
  post('/api/background/stop',body).then(function(data){
    toast(data.message||'已请求停止');refreshStatus().catch(function(){});
  }).catch(function(err){toast(err.message);});
}
function discardBackground(id){
  if(state.streaming)return;
  if(!confirm('丢弃这个后台执行结果？'))return;
  post('/api/background/discard',id==null?{}:{id:id}).then(function(){
    toast('已丢弃后台结果');refreshStatus().catch(function(){});
  }).catch(function(err){toast(err.message);});
}
function autoContinueBackground(jobId){
  if(state.streaming||state.backgroundResuming||!state.historyLoaded) return;
  if(state.status&&state.status.turn_busy) return;
  if(backgroundBlocked(state.status)) return;
  state.backgroundResuming=true;
  stopBackgroundPoll();
  startTurn();
  stream('/api/continue',{id:jobId}).then(function(){
    state.backgroundResuming=false;
    setTimeout(function(){refreshStatus().catch(function(){});},300);
  }).catch(function(){state.backgroundResuming=false;});
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
  if((bg.phase==='running'||bg.phase==='queued')&&bg.stop_requested){
    var stopping=document.createElement('p'); stopping.className='hint';
    stopping.textContent=bg.discard_on_finish
      ? '已请求停止并丢弃结果，等待协作检查点；结果不会写入对话。'
      : '停止请求已发送，等待协作检查点。';
    wrap.appendChild(stopping);
  }
  if(bg.phase==='ready'&&bg.result){
    var output=(bg.result.output||'')+(bg.result.error?'\n'+bg.result.error:'');
    wrap.appendChild(collapsibleBlock(
      '查看执行输出 #'+bg.id+'（'+output.length+' 字符）',output||'<无输出>',false));
  }
  if(bg.phase==='error'&&bg.error){
    var err=document.createElement('p'); err.className='hint';
    err.textContent='错误: '+bg.error.slice(0,240); wrap.appendChild(err);
  }
  var actions=document.createElement('div'); actions.className='actions';
  if(backgroundRunning(bg)){
    if(!bg.stop_requested){
      var stop=document.createElement('button'); stop.type='button'; stop.className='ghost';
      stop.textContent='停止 #'+bg.id; stop.onclick=function(){stopBackground(bg.id,false);};
      actions.appendChild(stop);
    }
    if(!bg.discard_on_finish){
      var stopDiscard=document.createElement('button'); stopDiscard.type='button'; stopDiscard.className='danger';
      stopDiscard.textContent='停止并丢弃 #'+bg.id;
      stopDiscard.onclick=function(){stopBackground(bg.id,true);};
      actions.appendChild(stopDiscard);
    }
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
      disc.textContent='丢弃结果'; disc.onclick=function(){discardBackground(bg.id);};
      actions.appendChild(disc);
    }
  } else if(bg.phase==='error'){
    var disc2=document.createElement('button'); disc2.type='button'; disc2.className='ghost';
    disc2.textContent='丢弃记录'; disc2.onclick=function(){discardBackground(bg.id);};
    actions.appendChild(disc2);
  }
  if(actions.childNodes.length) wrap.appendChild(actions);
  return wrap;
}
function backgroundEmptyHint(){
  var host=$('backgroundList'); if(!host) return;
  host.textContent='';
  var p=document.createElement('p'); p.className='hint empty-hint';
  p.textContent='暂无后台任务。可以让助手在后台运行长时间程序。';
  host.appendChild(p);
}
function rebuildBackgroundBody(el,jobs){
  el.textContent='';
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
    stopAll.onclick=function(){stopBackground(null,false);};
    all.appendChild(stopAll);
    var stopDiscardAll=document.createElement('button'); stopDiscardAll.type='button'; stopDiscardAll.className='danger';
    stopDiscardAll.textContent='停止并丢弃全部';
    stopDiscardAll.onclick=function(){stopBackground(null,true);};
    all.appendChild(stopDiscardAll);
    el.appendChild(all);
  }
}
function renderBackgroundCard(jobs){
  var host=$('backgroundList'); if(!host) return;
  var key=jobs.map(function(bg){
    return bg.id+':'+bg.phase+':'+(bg.stop_requested?'s':'')+':'+
      (bg.discard_on_finish?'d':'')+':'+(bg.result?bg.result.elapsed_ms:0)+':'+
      (bg.note||'')+':'+(bg.error||'');
  }).join('|');
  if(state.backgroundKey!==key||!state.backgroundEl||!host.contains(state.backgroundEl)){
    if(state.backgroundEl) state.backgroundEl.remove();
    host.textContent='';
    var el=document.createElement('div'); el.className='background-card';
    state.backgroundKey=key; state.backgroundEl=el;
    host.appendChild(el); rebuildBackgroundBody(el,jobs); return;
  }
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
    backgroundEmptyHint();
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

/* ---------- scheduled tasks ---------- */
function scheduleHumanSeconds(value){
  value=parseInt(value,10)||0;
  if(value>=86400&&value%86400===0) return (value/86400)+' 天';
  if(value>=3600&&value%3600===0) return (value/3600)+' 小时';
  if(value>=60&&value%60===0) return (value/60)+' 分钟';
  return value+' 秒';
}
function scheduleWhenLabel(task){
  if(task.when==='once') return task.at_text?('一次 · '+task.at_text):('一次 · 延迟 '+scheduleHumanSeconds(task.delay_s));
  if(task.when==='interval') return '每 '+scheduleHumanSeconds(task.every_s)+' 重复';
  return '每天 '+task.at_text;
}
function scheduleStateLabel(task){
  if(task.running) return '执行中';
  if(!task.enabled){return (task.when==='once'&&task.last_status==='ok')?'已完成':'已停用';}
  if(task.last_status==='error') return '上次失败';
  return '已启用';
}
function scheduleEmptyHint(){
  var host=$('scheduleList'); if(!host) return;
  host.textContent='';
  var p=document.createElement('p'); p.className='hint empty-hint';
  p.textContent='暂无定时任务。可以新建任务，也可以直接让助手“10 分钟后提醒我”。';
  host.appendChild(p);
}
function renderScheduleRow(task){
  var row=document.createElement('div');
  row.className='schedule-job'+(task.enabled?'':' disabled');
  row.setAttribute('data-schedule-id',String(task.id));
  var head=document.createElement('h4');
  head.appendChild(document.createTextNode('#'+task.id+' · '+(task.name||'未命名')+' '));
  var badge=document.createElement('span');
  badge.className='badge'+(task.enabled?'':' soft');
  badge.textContent=scheduleStateLabel(task);
  head.appendChild(badge); row.appendChild(head);
  var meta=document.createElement('div'); meta.className='schedule-meta';
  var lines=[];
  lines.push((task.type==='prompt'?'提示词任务':'代码任务')+' · '+scheduleWhenLabel(task));
  lines.push('下次: '+(task.next_local||'-'));
  lines.push('上次: '+(task.last_local||'从未')+(task.last_status?(' · '+task.last_status):'')+' · 已运行 '+(task.run_count||0)+' 次');
  meta.textContent=lines.join('\n'); row.appendChild(meta);
  if(task.preview){
    var preview=document.createElement('div'); preview.className='schedule-preview';
    preview.textContent=task.preview; row.appendChild(preview);
  }
  if(task.last_status==='error'&&task.last_error){
    var error=document.createElement('p'); error.className='hint';
    error.textContent='上次错误: '+task.last_error.slice(0,240); row.appendChild(error);
  }
  var actions=document.createElement('div'); actions.className='actions';
  if(!task.running){
    var run=document.createElement('button'); run.type='button'; run.textContent='立即运行';
    run.onclick=function(){scheduleAction('run',task.id);}; actions.appendChild(run);
  }
  var toggle=document.createElement('button'); toggle.type='button'; toggle.className='ghost';
  toggle.textContent=task.enabled?'停用':'启用';
  toggle.onclick=function(){scheduleAction(task.enabled?'disable':'enable',task.id);};
  actions.appendChild(toggle);
  var edit=document.createElement('button'); edit.type='button'; edit.className='ghost';
  edit.textContent='编辑'; edit.onclick=function(){editSchedule(task.id);};
  actions.appendChild(edit);
  var remove=document.createElement('button'); remove.type='button'; remove.className='danger';
  remove.textContent='删除'; remove.onclick=function(){scheduleAction('delete',task.id);};
  actions.appendChild(remove);
  row.appendChild(actions);
  return row;
}
function syncSchedules(status){
  var tasks=(status&&status.schedules)||[];
  var host=$('scheduleList'); if(!host) return;
  var key=tasks.map(function(task){
    return [task.id,task.enabled?'1':'0',task.running?'r':'',task.next_run,
      task.last_run,task.last_status,task.run_count,task.name,task.type,
      task.when,task.at_text,task.delay_s,task.every_s,task.last_error].join(':');
  }).join('|');
  if(state.scheduleKey===key) return;
  state.scheduleKey=key;
  host.textContent='';
  if(!tasks.length){scheduleEmptyHint();return;}
  tasks.forEach(function(task){host.appendChild(renderScheduleRow(task));});
}
function syncScheduleForm(){
  var type=$('scheduleType').value;
  show($('schedulePromptField'),type==='prompt');
  show($('scheduleSourceField'),type==='code');
  show($('scheduleTimeoutField'),type==='code');
  var when=$('scheduleWhen').value;
  show($('scheduleOnceFields'),when==='once');
  show($('scheduleIntervalField'),when==='interval');
  show($('scheduleDailyField'),when==='daily');
}
function resetScheduleForm(closeEditor){
  state.scheduleEditId=null;
  $('scheduleEditorSummary').textContent='新建定时任务';
  $('scheduleSaveBtn').textContent='创建任务';
  show($('scheduleCancelBtn'),false);
  $('scheduleName').value=''; $('schedulePrompt').value='';
  $('scheduleSource').value=''; $('scheduleTimeout').value='';
  $('scheduleDelay').value=''; $('scheduleAt').value='';
  $('scheduleEvery').value=''; $('scheduleDailyAt').value='';
  $('scheduleEnabled').checked=true;
  $('scheduleType').value='prompt'; $('scheduleWhen').value='once';
  syncScheduleForm();
  if(closeEditor) $('scheduleEditor').open=false;
}
function editSchedule(id){
  api('/api/schedules?id='+encodeURIComponent(id)).then(function(data){
    var task=(data.tasks||[])[0];
    if(!task){toast('任务不存在');return;}
    state.scheduleEditId=task.id;
    $('scheduleEditor').open=true;
    $('scheduleEditorSummary').textContent='编辑定时任务 #'+task.id;
    $('scheduleSaveBtn').textContent='保存修改';
    show($('scheduleCancelBtn'),true);
    $('scheduleName').value=task.name||'';
    $('scheduleType').value=task.type||'prompt';
    $('schedulePrompt').value=task.prompt||'';
    $('scheduleSource').value=task.source||'';
    $('scheduleTimeout').value=task.timeout_s==null?'':task.timeout_s;
    $('scheduleWhen').value=task.when||'once';
    $('scheduleDelay').value='';
    $('scheduleAt').value=(task.when==='once')?(task.next_input||task.at_text||''):'';
    $('scheduleEvery').value=task.every_s||'';
    $('scheduleDailyAt').value=task.when==='daily'?(task.at_text||''):'';
    $('scheduleEnabled').checked=task.enabled!==false;
    syncScheduleForm();
    try{$('scheduleEditor').scrollIntoView({behavior:'smooth',block:'start'});}catch(e){}
  }).catch(function(err){toast(err.message);});
}
function scheduleAction(action,id){
  if(action==='delete'&&!confirm('删除定时任务 #'+id+'？')) return;
  post('/api/schedules',{action:action,id:id}).then(function(data){
    toast(data.message||'已提交');refreshStatus().catch(function(){});
  }).catch(function(err){toast(err.message);});
}
function saveSchedule(){
  var type=$('scheduleType').value;
  var when=$('scheduleWhen').value;
  var body={action:state.scheduleEditId?'update':'create',name:$('scheduleName').value.trim(),type:type,when:when,enabled:$('scheduleEnabled').checked};
  if(state.scheduleEditId) body.id=state.scheduleEditId;
  if(type==='prompt'){
    body.prompt=$('schedulePrompt').value.trim();
    if(!body.prompt){toast('请输入提示词');return;}
  } else {
    body.source=$('scheduleSource').value;
    if(!body.source.trim()){toast('请输入代码');return;}
    var timeout=parseInt($('scheduleTimeout').value,10);
    body.timeout=isFinite(timeout)&&timeout>=0?timeout:null;
  }
  if(when==='once'){
    var at=$('scheduleAt').value.trim();
    var delay=parseInt($('scheduleDelay').value,10);
    if(at) body.at=at;
    else if(isFinite(delay)&&delay>0) body.delay_s=delay;
    else {toast('请填写延迟秒数或本地时间');return;}
  } else if(when==='interval'){
    var every=parseInt($('scheduleEvery').value,10);
    if(!isFinite(every)||every<30){toast('重复间隔至少 30 秒');return;}
    body.every_s=every;
  } else {
    var daily=$('scheduleDailyAt').value.trim();
    if(!daily){toast('请填写每天时间（HH:MM）');return;}
    body.at=daily;
  }
  if(type==='code'){
    if(!confirm('代码任务会在后台自动执行并拥有完整设备权限。确定保存？')) return;
    body.confirm=true;
  }
  post('/api/schedules',body).then(function(data){
    toast(data.message||'已保存');
    resetScheduleForm(true);refreshStatus().catch(function(){});
  }).catch(function(err){toast(err.message);});
}

/* ---------- skills ---------- */
function loadSkills(){
  return api('/api/skills').then(function(data){
    state.skills=data.skills||[];
    renderSkills(state.skills,data.limit);
    return data;
  }).catch(function(err){if(err.status===401)showLogin();throw err;});
}
function renderSkills(skills,limit){
  var host=$('skillsList'); if(!host) return;
  host.textContent='';
  setText($('skillsStatus'),skills.length?
    ('共 '+skills.length+' 个 Skill'+(limit?'（上限 '+limit+'）':'')):'暂无 Skill。');
  if(!skills.length){
    var empty=document.createElement('p'); empty.className='hint';
    empty.textContent='模型可以通过 __skill 提案把常用流程保存为 Skill；也可以在串口使用 /skill-save。';
    host.appendChild(empty); return;
  }
  skills.forEach(function(skill){
    var row=document.createElement('div'); row.className='skill-job';
    var head=document.createElement('h3');
    head.appendChild(document.createTextNode(skill.name));
    var badge=document.createElement('span'); badge.className='badge soft';
    badge.textContent=(skill.chars||0)+' 字符'; head.appendChild(badge);
    row.appendChild(head);
    var desc=document.createElement('p'); desc.className='hint';
    desc.textContent=skill.description||'<无描述>'; row.appendChild(desc);
    var host2=document.createElement('div'); row.appendChild(host2);
    var view=document.createElement('button'); view.type='button'; view.className='ghost';
    view.textContent='查看 SKILL.md';
    view.onclick=function(){
      if(host2.childNodes.length){
        host2.hidden=!host2.hidden;
        view.textContent=host2.hidden?'查看 SKILL.md':'收起';
        return;
      }
      view.disabled=true;
      api('/api/skills?name='+encodeURIComponent(skill.name)).then(function(data){
        host2.appendChild(collapsibleBlock(
          'SKILL.md（'+(data.chars||0)+' 字符）',data.content||'',true));
        host2.hidden=false; view.disabled=false; view.textContent='收起';
      }).catch(function(err){
        view.disabled=false; toast(err.message);
      });
    };
    row.appendChild(view);
    host.appendChild(row);
  });
}

/* ---------- file manager ---------- */
function loadFiles(path){
  return api('/api/files?path='+encodeURIComponent(path||'/')).then(function(data){
    state.filePath=data.path||'/';
    state.fileParent=data.parent||'';
    state.files=data.entries||[];
    state.fileWritable=!!data.writable;
    renderFiles(data);
    return data;
  }).catch(function(err){if(err.status===401)showLogin();throw err;});
}
function renderFiles(data){
  setText($('filePath'),data.path||'/');
  $('fileUpBtn').disabled=!data.parent;
  $('fileMkdirBtn').disabled=!data.writable;
  $('fileNewBtn').disabled=!data.writable;
  $('fileUploadBtn').disabled=!data.writable;
  setText($('fileHint'),(data.writable?
    '当前目录可写。':'当前目录只读，只能查看或下载系统文件。')+
    (data.truncated?' 目录项过多，仅显示前 200 项。':''));
  var host=$('fileList'); host.textContent='';
  if(!data.entries.length){
    var empty=document.createElement('p'); empty.className='hint';
    empty.textContent='目录为空。'; host.appendChild(empty);
  }
  data.entries.forEach(function(entry){host.appendChild(fileRow(entry));});
  state.fileEditorPath=null; show($('fileEditor'),false);
}
function fileRow(entry){
  var row=document.createElement('div'); row.className='file-row';
  var name=document.createElement('button'); name.type='button';
  name.className='file-name'; name.textContent=entry.name;
  if(entry.sensitive){
    name.onclick=function(){toast('敏感文件不可通过网页读取或下载');};
  } else {
    name.onclick=function(){
      if(entry.kind==='dir'){loadFiles(entry.path).catch(function(e){toast(e.message);});}
      else {openFile(entry.path);}
    };
  }
  row.appendChild(name);
  var meta=document.createElement('span'); meta.className='file-meta';
  meta.textContent=(entry.kind==='dir'?'目录':formatBytes(entry.size));
  row.appendChild(meta);
  if(entry.sensitive){
    var sensitive=document.createElement('span'); sensitive.className='badge warn';
    sensitive.textContent='敏感'; row.appendChild(sensitive);
  } else if(entry.protected){
    var locked=document.createElement('span'); locked.className='badge soft';
    locked.textContent='系统'; row.appendChild(locked);
  }
  var actions=document.createElement('div'); actions.className='file-actions';
  if(entry.kind==='file'&&!entry.sensitive){
    var download=document.createElement('button'); download.type='button'; download.className='ghost';
    download.textContent='下载';
    download.onclick=function(){window.location.href='/api/files/download?path='+encodeURIComponent(entry.path);};
    actions.appendChild(download);
  }
  if(entry.kind==='file'&&!entry.sensitive){
    var edit=document.createElement('button'); edit.type='button'; edit.className='ghost';
    edit.textContent=entry.editable?'编辑':'查看';
    edit.onclick=function(){openFile(entry.path);};
    actions.appendChild(edit);
  }
  if(entry.writable){
    var rename=document.createElement('button'); rename.type='button'; rename.className='ghost';
    rename.textContent='重命名'; rename.onclick=function(){renameFile(entry);};
    actions.appendChild(rename);
    var remove=document.createElement('button'); remove.type='button'; remove.className='danger';
    remove.textContent='删除'; remove.onclick=function(){deleteFile(entry);};
    actions.appendChild(remove);
  }
  if(actions.childNodes.length) row.appendChild(actions);
  return row;
}
function openFile(path){
  return api('/api/files/read?path='+encodeURIComponent(path)).then(function(data){
    state.fileEditorPath=data.path;
    setText($('fileEditorPath'),data.path);
    var editor=$('fileEditorText');
    editor.value=data.text||'';
    editor.readOnly=!data.editable;
    $('fileSaveBtn').disabled=!data.editable;
    $('fileSaveBtn').hidden=!data.editable;
    setText($('fileEditorHint'),
      data.chars+' 字符'+(data.truncated?' · 仅显示前 32768 字符':'')+
      (data.editable?'':' · 只读'));
    show($('fileEditor'),true);
    try{$('fileEditor').scrollIntoView({behavior:'smooth',block:'start'});}catch(e){}
    return data;
  }).catch(function(err){toast(err.message);throw err;});
}
function saveFile(){
  var path=state.fileEditorPath;
  if(!path) return;
  post('/api/files/write',{path:path,content:$('fileEditorText').value}).then(function(){
    toast('文件已保存');loadFiles(state.filePath).catch(function(e){toast(e.message);});
  }).catch(function(err){toast(err.message);});
}
function uploadFile(name,text,overwrite){
  var path=(state.filePath==='/'?'':state.filePath)+'/'+name;
  path=path.replace(/\/+/g,'/');
  return post('/api/files/upload',{
    path:path,content:text,overwrite:!!overwrite
  }).then(function(data){
    toast('已上传 '+data.path);
    return loadFiles(state.filePath);
  });
}
function readUpload(file){
  return new Promise(function(resolve,reject){
    var reader=new FileReader();
    reader.onerror=function(){reject(new Error('读取文件失败'));};
    reader.onload=function(){
      var text=String(reader.result||'');
      if(text.indexOf('\u0000')>=0) reject(new Error('仅支持 UTF-8 文本文件'));
      else resolve(text);
    };
    reader.readAsText(file);
  });
}
function chosenUpload(){
  var input=$('fileUploadInput');
  var file=input.files&&input.files[0];
  if(!file) return;
  var name=file.name;
  var exists=state.files.some(function(entry){return entry.name===name;});
  var overwrite=exists;
  if(exists&&!confirm('同名文件已存在，覆盖 '+name+'？')){input.value='';return;}
  readUpload(file).then(function(text){
    return uploadFile(name,text,overwrite);
  }).then(function(){input.value='';}).catch(function(err){
    input.value='';toast(err.message);
  });
}
function newTextFile(){
  if(!state.fileWritable) return;
  var name=prompt('新文件名（字母、数字、点、下划线、连字符）');
  if(!name) return;
  var exists=state.files.some(function(entry){return entry.name===name;});
  if(exists){toast('同名文件已存在');return;}
  uploadFile(name,'',false).then(function(){
    openFile((state.filePath==='/'?'':state.filePath)+'/'+name).catch(function(){});
  }).catch(function(err){toast(err.message);});
}
function newFolder(){
  if(!state.fileWritable) return;
  var name=prompt('新文件夹名（字母、数字、点、下划线、连字符）');
  if(!name) return;
  var path=(state.filePath==='/'?'':state.filePath)+'/'+name;
  post('/api/files/mkdir',{path:path.replace(/\/+/g,'/')}).then(function(){
    toast('已创建文件夹');loadFiles(state.filePath).catch(function(e){toast(e.message);});
  }).catch(function(err){toast(err.message);});
}
function renameFile(entry){
  var name=prompt('新名称',entry.name);
  if(!name||name===entry.name) return;
  post('/api/files/rename',{path:entry.path,name:name}).then(function(data){
    toast('已重命名');loadFiles(state.filePath).catch(function(e){toast(e.message);});
    if(state.fileEditorPath===entry.path) show($('fileEditor'),false);
  }).catch(function(err){toast(err.message);});
}
function deleteFile(entry){
  if(!confirm('删除 '+(entry.kind==='dir'?'目录':'文件')+' '+entry.path+'？')) return;
  post('/api/files/delete',{path:entry.path}).then(function(){
    toast('已删除');
    if(state.fileEditorPath===entry.path){state.fileEditorPath=null;show($('fileEditor'),false);}
    loadFiles(state.filePath).catch(function(e){toast(e.message);});
  }).catch(function(err){toast(err.message);});
}

/* ---------- settings ---------- */
function ensureSettings(section){
  showSettingsPanel(section);
  if(!state.settingsLoaded){
    state.settingsLoaded=true;
    loadConfig().then(function(){return loadMcp();}).catch(function(e){
      state.settingsLoaded=false;
      toast(e.message);
    });
  } else if(section==='mcp'){
    loadMcp().catch(function(){});
  }
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
    $('cfgMaxToolCalls').value=cfg.max_tool_calls||16;
    $('cfgContextTokens').value=cfg.context_tokens||200000;
    $('cfgAutoCompactTokens').value=cfg.auto_compact_tokens||150000;
    $('cfgAutoCompact').checked=cfg.auto_compact!==false;
    $('cfgNtpServer').value=cfg.ntp_server||'pool.ntp.org';
    $('cfgTimezoneOffset').value=cfg.timezone_offset_minutes==null?480:cfg.timezone_offset_minutes;
    $('cfgNtpIntervalHours').value=Math.round((cfg.ntp_sync_interval_s==null?21600:cfg.ntp_sync_interval_s)/3600*100)/100;
    $('cfgPersistConversation').checked=cfg.persist_conversation!==false;
    $('cfgSearchEnabled').checked=cfg.search_enabled!==false;
    $('cfgSearchProvider').value=cfg.search_provider||'auto';
    setText($('searchEffective'),'当前实际使用：'+searchProviderLabel(cfg.search_provider_effective||cfg.search_provider||'auto')+(cfg.search_provider_fallback?'（失败时自动回退到 Bing）':''));
    $('cfgHttpToolEnabled').checked=cfg.http_tool_enabled!==false;
    $('cfgHttpAllowPlain').checked=!!cfg.http_tool_allow_http;
    setText($('netStatus'),'模式 '+(cfg.mode||'')+' · STA '+(cfg.sta_ip||'-')+' · AP '+(cfg.ap_active?(cfg.ap_ssid||'on'):'off'));
    setText($('securityStatus'),'管理密码: '+(cfg.web_password_set?'已设置':'未设置')+' · AP 密码: '+(cfg.ap_password_set?'已设置':'未设置'));
    return cfg;
  }).catch(function(err){if(err.status===401)showLogin();throw err;});
}
function loadMcp(){
  return api('/api/mcp').then(function(mcp){
    state.mcp=mcp;
    $('cfgMcpEnabled').checked=!!mcp.enabled;
    var servers=(mcp.servers||[]).map(function(s){
      return {name:s.name,url:s.url,enabled:s.enabled,auto:s.auto,
        timeout_s:s.timeout_s,protocol:s.protocol};
    });
    $('cfgMcpServers').value=JSON.stringify(servers,null,2);
    var errors=mcp.errors||{}; var names=Object.keys(errors);
    var text='已加载工具: '+(mcp.tools||0)+' 个';
    if(names.length){text+='；错误: '+names.map(function(n){return n+': '+errors[n];}).join('; ');}
    if(!mcp.enabled){text='MCP 已关闭';}
    setText($('mcpStatus'),text);
    return mcp;
  }).catch(function(err){
    setText($('mcpStatus'),'MCP 状态不可用: '+err.message);throw err;
  });
}
function saveConfig(fields){
  return post('/api/config',fields).then(function(result){
    toast(result.relogin_required?'密码已修改，请重新登录':'已保存');
    if(result.relogin_required){showLogin();return result;}
    return refreshStatus().then(loadConfig);
  }).catch(function(err){toast(err.message);throw err;});
}

/* ---------- auth and boot ---------- */
function showLogin(){
  state.authed=false; state.historyLoaded=false; state.liveUserEl=null;
  state.sidebarOpen=false; syncSidebar(); stopBackgroundPoll();
  show($('loginView'),true); show($('setupView'),false);
  show($('mainView'),false); show($('loginNotice'),false);
  var note=state.setupInfo&&state.setupInfo.reset_note;
  if(note){setText($('loginNotice'),note);show($('loginNotice'),true);}
}
function showSetup(setup){
  state.setup=setup;state.authed=false;state.historyLoaded=false;
  state.sidebarOpen=false;syncSidebar();stopBackgroundPoll();
  show($('setupView'),true);show($('loginView'),false);show($('mainView'),false);
  if(setup){
    var port=setup.port||80;
    setText($('setupHint'),'请连接设备 AP'+(setup.ap_ssid?' '+setup.ap_ssid:'')+'，然后打开 http://'+(setup.ap_ip||'192.168.4.1')+(port===80?'':':'+port)+'/ 完成设置。');
  }
}
function showApp(){
  state.authed=true;
  show($('mainView'),true);show($('loginView'),false);
  show($('setupView'),false);show($('loginNotice'),false);
  syncSidebar();
}
function boot(){
  try{state.sidebarCollapsed=localStorage.getItem('harness-sidebar-collapsed')==='1';}catch(e){}
  syncSidebar();
  return api('/api/setup').then(function(setup){
    state.setupInfo=setup;
    if(setup.setup_required){showSetup(setup);return;}
    return refreshStatus().then(function(){
      showApp();showScreen('chat');return refreshHistory();
    }).catch(function(){showLogin();});
  }).catch(function(){showLogin();});
}

/* ---------- event wiring ---------- */
document.addEventListener('DOMContentLoaded',function(){
  $('loginForm').onsubmit=function(e){
    e.preventDefault();setText($('loginError'),'');
    post('/api/login',{password:$('loginPassword').value}).then(function(){
      showApp();showScreen('chat');return refreshStatus();
    }).then(function(){return refreshHistory();}).catch(function(err){
      setText($('loginError'),err.message);show($('loginError'),true);
    });
  };
  $('setupForm').onsubmit=function(e){
    e.preventDefault();setText($('setupError'),'');
    var password=$('setupPassword').value, confirmValue=$('setupConfirm').value;
    if(password!==confirmValue){setText($('setupError'),'两次密码不一致');show($('setupError'),true);return;}
    var body={password:password,confirm:confirmValue,wifi_ssid:$('setupSsid').value,
      wifi_password:$('setupWifiPassword').value,api_provider:$('setupProvider').value,
      base_url:$('setupBaseUrl').value,model:$('setupModel').value,api_key:$('setupApiKey').value};
    post('/api/setup',body).then(function(){
      showApp();showScreen('chat');return refreshStatus();
    }).then(function(){return refreshHistory();}).catch(function(err){
      setText($('setupError'),err.message);show($('setupError'),true);
    });
  };
  $('chatForm').onsubmit=sendChat;
  $('chatInput').addEventListener('keydown',function(e){
    if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendChat();}
  });
  $('sendBtn').onclick=sendChat;
  $('stopBtn').onclick=stopStream;
  $('chatThinking').onchange=function(){changeThinking(this.value);};
  $('chatAuto').onchange=function(){changeAuto(this.checked);};
  $('refreshBtn').onclick=refreshCurrentScreen;
  $('sidebarToggleBtn').onclick=toggleSidebar;
  $('sidebarCloseBtn').onclick=toggleSidebar;
  $('sidebarBackdrop').onclick=closeSidebar;
  $('logoutBtn').onclick=function(){post('/api/logout',{}).then(function(){showLogin();});};
  var navItems=document.querySelectorAll('.nav-item');
  for(var i=0;i<navItems.length;i++){
    navItems[i].onclick=function(){
      var nav=this.getAttribute('data-nav');
      if(!nav) return;
      if(nav.indexOf('settings-')===0) showScreen('settings',nav.slice(9));
      else showScreen(nav);
    };
  }
  var groupToggles=document.querySelectorAll('[data-group-toggle]');
  for(var g=0;g<groupToggles.length;g++){
    groupToggles[g].onclick=function(){
      openGroup(this.getAttribute('data-group-toggle'));
    };
  }
  window.addEventListener('resize',function(){syncSidebar();syncComposerHeight();});
  function switchProviderBase(selectId,baseId){
    var base=$(baseId).value.replace(/\/+$/,'');
    var known=['https://api.openai.com/v1','https://api.openai.com','https://api.anthropic.com','https://api.deepseek.com','https://api.deepseek.com/v1'];
    if(known.indexOf(base)>=0){
      $(baseId).value=$(selectId).value==='anthropic'?'https://api.anthropic.com':'https://api.openai.com/v1';
    }
  }
  $('setupProvider').onchange=function(){switchProviderBase('setupProvider','setupBaseUrl');};
  $('cfgProvider').onchange=function(){switchProviderBase('cfgProvider','cfgBaseUrl');};
  $('wifiSaveBtn').onclick=function(){
    var body={ssid:$('cfgSsid').value,password:$('cfgWifiClear').checked?'-':$('cfgWifiPassword').value};
    post('/api/wifi/connect',body).then(function(result){
      toast(result.connecting?'正在连接，若断线请连接设备 AP':'已提交');refreshStatus().catch(function(){});
    }).catch(function(e){toast(e.message);});
  };
  $('modelSaveBtn').onclick=function(){
    var body={api_provider:$('cfgProvider').value,base_url:$('cfgBaseUrl').value,
      model:$('cfgModel').value,tls_verify:$('cfgTls').checked,ca_file:$('cfgCaFile').value};
    if($('cfgApiClear').checked) body.api_key_clear=true;
    else if($('cfgApiKey').value) body.api_key=$('cfgApiKey').value;
    saveConfig(body);
  };
  $('caSaveBtn').onclick=function(){
    post('/api/config/ca',{pem:$('cfgCaPem').value}).then(function(){toast('CA 已保存');}).catch(function(e){toast(e.message);});
  };
  $('mcpSaveBtn').onclick=function(){
    var raw=$('cfgMcpServers').value.trim(); var servers=[];
    if(raw){try{servers=JSON.parse(raw);}catch(e){toast('MCP JSON 无效');return;}}
    if(!Array.isArray(servers)){toast('MCP 配置必须是数组');return;}
    var auto=servers.some(function(s){return s&&s.auto;});
    if(auto&&!confirm('MCP auto 模式会无确认执行远程工具，继续？')) return;
    post('/api/config',{mcp_enabled:$('cfgMcpEnabled').checked}).then(function(){
      return post('/api/mcp',{servers:servers,confirm:auto});
    }).then(function(){
      toast('MCP 已保存，正在刷新工具');return post('/api/mcp/refresh',{});
    }).then(function(){toast('MCP 工具已刷新');return loadMcp();})
      .catch(function(e){toast(e.message);});
  };
  $('mcpRefreshBtn').onclick=function(){
    post('/api/mcp/refresh',{}).then(function(result){
      toast('MCP 工具已刷新: '+(result.tools||0));return loadMcp();
    }).catch(function(e){toast(e.message);});
  };
  $('searchSaveBtn').onclick=function(){
    saveConfig({
      search_enabled:$('cfgSearchEnabled').checked,
      search_provider:$('cfgSearchProvider').value,
      http_tool_enabled:$('cfgHttpToolEnabled').checked,
      http_tool_allow_http:$('cfgHttpAllowPlain').checked
    }).then(function(){toast('搜索设置已保存');}).catch(function(){});
  };
  $('behaviorSaveBtn').onclick=function(){
    var cfg=state.config||{};
    var currentAuto=(state.status&&state.status.auto)||{};
    var run=typeof cfg.auto_run==='boolean'?cfg.auto_run:!!currentAuto.run;
    var ntpHours=parseFloat($('cfgNtpIntervalHours').value);
    if(!isFinite(ntpHours)||ntpHours<0||ntpHours>24){toast('自动校时间隔需为 0-24 小时');return;}
    var tzOffset=parseInt($('cfgTimezoneOffset').value,10);
    if(!isFinite(tzOffset)||tzOffset< -720||tzOffset>840){toast('时区偏移需为 -720 到 840 分钟');return;}
    var toolCalls=parseInt($('cfgMaxToolCalls').value,10);
    if(!isFinite(toolCalls)||toolCalls<1||toolCalls>64){toast('工具调用上限需为 1-64');return;}
    var contextTokens=parseInt($('cfgContextTokens').value,10);
    if(!isFinite(contextTokens)||contextTokens<1024||contextTokens>2000000){toast('上下文长度需为 1024-2000000');return;}
    var compactTokens=parseInt($('cfgAutoCompactTokens').value,10);
    if(!isFinite(compactTokens)||compactTokens<1024||compactTokens>=contextTokens){toast('自动压缩阈值需不小于 1024 且小于上下文长度');return;}
    post('/api/config',{
      show_thinking:$('cfgShowThinking').checked,
      background_execution:$('cfgBackground').checked,
      background_max_jobs:parseInt($('cfgBackgroundMax').value,10)||4,
      max_turns:parseInt($('cfgMaxTurns').value,10)||32,
      max_tool_calls:parseInt($('cfgMaxToolCalls').value,10)||16,
      context_tokens:contextTokens,
      auto_compact_tokens:compactTokens,
      auto_compact:$('cfgAutoCompact').checked,
      persist_conversation:$('cfgPersistConversation').checked,
      ntp_server:$('cfgNtpServer').value.trim(),
      timezone_offset_minutes:tzOffset,
      ntp_sync_interval_s:Math.round(ntpHours*3600)
    }).then(function(){
      return post('/api/auto',{auto_run:run,auto_feedback:$('cfgAutoFeedback').checked,confirm:true});
    }).then(function(){toast('行为与时间设置已保存');return refreshStatus();})
      .catch(function(e){toast(e.message);});
  };
  $('passwordSaveBtn').onclick=function(){
    if(!$('cfgNewPassword').value){toast('请输入新管理密码');return;}
    var body={web_password:$('cfgNewPassword').value,
      current_password:$('cfgCurrentPassword').value,
      confirm_password:$('cfgNewPassword2').value};
    saveConfig(body).then(function(){
      $('cfgCurrentPassword').value='';$('cfgNewPassword').value='';$('cfgNewPassword2').value='';
    }).catch(function(){});
  };
  $('apPasswordBtn').onclick=function(){
    var value=$('cfgApPassword').value;
    if(value.length<8||value.length>63){toast('AP 密码需为 8-63 位');return;}
    saveConfig({ap_password:value}).then(function(){$('cfgApPassword').value='';}).catch(function(){});
  };
  $('clearBtn').onclick=function(){
    if(confirm('清空当前对话历史？')){
      post('/api/command',{line:'/clear'}).then(function(){
        refreshHistory();refreshStatus();
      }).catch(function(e){toast(e.message);});
    }
  };
  $('compactBtn').onclick=function(){
    var button=this;button.disabled=true;
    post('/api/command',{line:'/compact'}).then(function(data){
      toast('上下文已压缩');
      if(data.output) toast('压缩完成');
      refreshStatus();
    }).catch(function(e){toast(e.message);}).then(function(){button.disabled=false;});
  };
  $('historyBtn').onclick=function(){refreshHistory().catch(function(e){toast(e.message);});};
  $('factoryResetBtn').onclick=function(){
    if(!$('factoryConfirm').checked){toast('请先勾选确认删除所有用户数据');return;}
    var password=$('factoryPassword').value;
    if(!password){toast('请输入当前管理密码');return;}
    if(!confirm('恢复出厂设置会删除所有用户数据并重启设备，且不可撤销。确定继续？')) return;
    post('/api/factory-reset',{confirm:true,password:password}).then(function(data){
      $('factoryPassword').value='';$('factoryConfirm').checked=false;
      toast('已删除 '+(data.deleted||[]).length+' 项用户数据，设备正在重启');
      setTimeout(function(){
        showLogin();
        setText($('loginError'),'恢复出厂设置已完成。请连接设备 AP（ESP32-Harness-XXXX）后打开 http://192.168.4.1/ 重新设置。');
        show($('loginError'),true);
      },1500);
    }).catch(function(e){toast(e.message);});
  };
  $('testWifiBtn').onclick=function(){
    post('/api/test',{kind:'wifi'}).then(function(data){toast(data.ok?'Wi-Fi 正常':'Wi-Fi 异常');}).catch(function(e){toast(e.message);});
  };
  $('testApiBtn').onclick=function(){
    post('/api/test',{kind:'api'}).then(function(data){toast(data.ok?'API 正常':'API 异常');}).catch(function(e){toast(e.message);});
  };
  $('scheduleType').onchange=syncScheduleForm;
  $('scheduleWhen').onchange=syncScheduleForm;
  $('backgroundClearBtn').onclick=function(){
    if(!confirm('丢弃所有已完成的后台结果？正在运行或排队的任务不受影响。'))return;
    post('/api/background/discard',{all:true}).then(function(res){
      toast('已丢弃 '+((res&&res.discarded)||0)+' 个结果');
      refreshStatus();
    }).catch(function(e){toast(e.message);});
  };
  $('scheduleSaveBtn').onclick=saveSchedule;
  $('scheduleCancelBtn').onclick=function(){resetScheduleForm(true);};
  $('fileUpBtn').onclick=function(){
    if(state.fileParent) loadFiles(state.fileParent).catch(function(e){toast(e.message);});
  };
  $('fileRefreshBtn').onclick=function(){loadFiles(state.filePath).catch(function(e){toast(e.message);});};
  $('fileMkdirBtn').onclick=newFolder;
  $('fileNewBtn').onclick=newTextFile;
  $('fileUploadBtn').onclick=function(){$('fileUploadInput').click();};
  $('fileUploadInput').onchange=chosenUpload;
  $('fileSaveBtn').onclick=saveFile;
  $('fileCancelBtn').onclick=function(){state.fileEditorPath=null;show($('fileEditor'),false);};
  setInterval(function(){
    if(state.authed&&!state.streaming&&!document.hidden){refreshStatus().catch(function(){});}
  },5000);
  document.addEventListener('visibilitychange',function(){
    if(!document.hidden&&state.authed&&!state.streaming){refreshStatus().catch(function(){});}
  });
  syncScheduleForm();backgroundEmptyHint();scheduleEmptyHint();boot();
});
"""
