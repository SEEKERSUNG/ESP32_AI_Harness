import unittest

from harness.webui import APP_CSS, APP_JS, INDEX_HTML


class WebUiTests(unittest.TestCase):
    def test_assistant_text_is_rendered_as_markdown(self):
        self.assertIn("function renderMarkdown", APP_JS)
        self.assertIn("function markdownInline", APP_JS)
        self.assertIn("markdown-body", APP_CSS)
        self.assertIn("renderMarkdown(body,segment.text)", APP_JS)
        # Raw HTML from the model must be escaped before Markdown formatting.
        self.assertIn("function escapeHtmlText", APP_JS)
        self.assertIn(".replace(/</g,'&lt;')", APP_JS)
        # Streaming chunks coalesce re-renders instead of appending raw text.
        self.assertIn("function scheduleAssistantRender", APP_JS)
        self.assertIn("bubble assistant answer-bubble", APP_JS)
        self.assertIn("code-segment", APP_JS)

    def test_chat_composer_owns_thinking_and_auto_controls(self):
        self.assertIn('id="chatThinking"', INDEX_HTML)
        self.assertIn('id="chatAuto"', INDEX_HTML)
        self.assertIn('class="composer-options"', INDEX_HTML)
        self.assertLess(INDEX_HTML.index('class="composer-options"'),
                        INDEX_HTML.index('id="chatInput"'))
        self.assertIn('value="max">Max</option>', INDEX_HTML)
        self.assertNotIn('value="medium"', INDEX_HTML)
        self.assertNotIn('id="cfgThinking"', INDEX_HTML)
        self.assertNotIn('id="cfgAutoRun"', INDEX_HTML)
        self.assertNotIn('id="cfgAutoSaveTools"', INDEX_HTML)
        self.assertIn('id="cfgShowThinking"', INDEX_HTML)
        self.assertIn('id="cfgAutoFeedback"', INDEX_HTML)

    def test_question_card_is_rendered_from_events_and_status(self):
        self.assertIn("function renderQuestion", APP_JS)
        self.assertIn("ev.t==='question'", APP_JS)
        self.assertIn("status.question", APP_JS)
        self.assertIn("question-card", APP_JS)
        self.assertIn("question-card", APP_CSS)
        self.assertIn("answerQuestion", APP_JS)
        # A card wiped from the DOM must be restored by the status poll.
        self.assertIn("document.body.contains(state.pendingEl)", APP_JS)
        self.assertIn("visibilitychange", APP_JS)

    def test_answered_question_is_not_redrawn_into_the_next_turn(self):
        # A status response issued before the user answered can land while the
        # answer turn is streaming. It must not insert the old question card at
        # the top of the new agent bubble.
        self.assertIn("answeredQuestion:null", APP_JS)
        self.assertIn("function questionKey(ev){", APP_JS)
        self.assertIn("el._questionKey=questionKey(ev);", APP_JS)
        self.assertIn(
            "if(state.answeredQuestion===questionKey(q)) return;", APP_JS)
        self.assertIn("function maybeShowPending(status){\n  if(state.streaming) return;",
                      APP_JS)
        self.assertIn(
            "if(cards[i]._questionKey) state.answeredQuestion=cards[i]._questionKey;",
            APP_JS)
        # A failed answer request must let the status poll restore the card.
        self.assertIn(
            "state.answeredQuestion=null;\n"
            "    var staleCode=", APP_JS)
        # A genuine question event still renders even when its text repeats.
        self.assertIn(
            "state.answeredQuestion=null;\n  var text=document.createElement('p');",
            APP_JS)

    def test_consumed_code_cards_and_device_owned_stop(self):
        # A code card whose Run button was pressed must not be recreated from
        # a status request that was issued before the click.
        self.assertIn("pendingCode:null", APP_JS)
        self.assertIn("runningCode:null", APP_JS)
        self.assertIn("consumedCodes:[]", APP_JS)
        self.assertIn("function codeConsumed", APP_JS)
        self.assertIn("function markCodeConsumed", APP_JS)
        self.assertIn("function unmarkCodeConsumed", APP_JS)
        self.assertIn("if(codeConsumed(p.code)) return;", APP_JS)
        self.assertIn("function disableCodeCards", APP_JS)
        self.assertIn("function removeCodeCards", APP_JS)
        self.assertIn("runPending('foreground',source)", APP_JS)
        self.assertIn("runPending('background',source)", APP_JS)
        self.assertIn("No pending code", APP_JS)
        self.assertIn("if(staleCode){refreshHistory()", APP_JS)
        # The stop button must also cover turns the page is only observing.
        self.assertIn("deviceBusy", APP_JS)
        self.assertIn("function syncStopButton", APP_JS)
        self.assertIn("function pollDeviceStop", APP_JS)
        self.assertIn("/api/stop", APP_JS)
        self.assertIn("if(!state.deviceBusy||state.stopWaiting) return;", APP_JS)

    def test_truncated_history_is_announced(self):
        self.assertIn("shown_messages", APP_JS)
        self.assertIn("total_messages", APP_JS)
        self.assertIn("web_history_limit / web_history_chars", APP_JS)
        self.assertIn("每条最多 8192 字符", APP_JS)

    def test_rewind_controls_use_history_indices(self):
        self.assertNotIn('id="undoBtn"', INDEX_HTML)
        self.assertIn("function rewindContext", APP_JS)
        self.assertIn("function attachRewindButton", APP_JS)
        self.assertIn("/api/rewind", APP_JS)
        self.assertIn("message.index", APP_JS)
        self.assertIn("state.liveUserEl", APP_JS)
        self.assertIn("ev.user_index", APP_JS)
        self.assertIn("回退到这里", APP_JS)
        self.assertIn("回退会删除这条用户消息", APP_JS)
        self.assertIn(".rewind-btn", APP_CSS)

    def test_login_shows_last_reset_note(self):
        self.assertIn('id="loginNotice"', INDEX_HTML)
        self.assertIn("reset_note", APP_JS)
        self.assertIn("setupInfo", APP_JS)
        self.assertIn("loginNotice", APP_JS)

    def test_background_execution_card_and_auto_resume(self):
        self.assertIn('id="cfgBackground"', INDEX_HTML)
        self.assertIn('id="cfgBackgroundMax"', INDEX_HTML)
        self.assertIn("background_execution", APP_JS)
        self.assertIn("background_max_jobs", APP_JS)
        self.assertIn("function syncBackground", APP_JS)
        self.assertIn("function backgroundJobs", APP_JS)
        self.assertIn("function autoContinueBackground", APP_JS)
        self.assertIn("/api/continue", APP_JS)
        self.assertIn("/api/background/discard", APP_JS)
        self.assertIn("ev.t==='background'", APP_JS)
        self.assertIn("status.backgrounds", APP_JS)
        self.assertIn("你可以同时发送新消息", APP_JS)
        self.assertIn("备注:", APP_JS)
        self.assertIn("backgroundBlocked", APP_JS)
        self.assertIn("请先处理待执行代码", APP_JS)
        self.assertIn("if(!runnable)", APP_JS)
        self.assertIn("代码未调度", APP_JS)
        self.assertIn("function stopBackground", APP_JS)
        self.assertIn("/api/background/stop", APP_JS)
        self.assertIn("停止全部后台程序", APP_JS)
        self.assertIn("停止并丢弃 #", APP_JS)
        self.assertIn("停止并丢弃全部", APP_JS)
        self.assertIn("discard_on_finish", APP_JS)
        self.assertIn("bg.discard_on_finish?'d'", APP_JS)
        self.assertIn("body.discard=true", APP_JS.replace(" ", ""))
        self.assertIn("stop_requested", APP_JS)
        self.assertIn("status.turn_busy", APP_JS)
        self.assertIn("前台运行", APP_JS)
        self.assertIn("deviceAuto", APP_JS)
        self.assertIn("无需手动选择", APP_JS)
        self.assertIn("continue_seq", APP_JS)
        self.assertIn("busy_source==='auto-continue'", APP_JS)
        self.assertIn("function startLivePoll", APP_JS)
        self.assertIn("function pollLive", APP_JS)
        self.assertIn("/api/live", APP_JS)
        self.assertIn("renderObservation", APP_JS)
        self.assertIn("后台运行", APP_JS)
        self.assertIn(".background-card", APP_CSS)
        self.assertIn(".background-job", APP_CSS)

    def test_tasks_screens_own_background_jobs_and_schedules(self):
        self.assertIn('id="screenBackground"', INDEX_HTML)
        self.assertIn('id="screenSchedules"', INDEX_HTML)
        self.assertIn('data-nav="background"', INDEX_HTML)
        self.assertIn('data-nav="schedules"', INDEX_HTML)
        self.assertNotIn('id="tasksPane"', INDEX_HTML)
        self.assertNotIn('data-tab=', INDEX_HTML)
        self.assertIn('id="backgroundList"', INDEX_HTML)
        self.assertIn('id="backgroundQuota"', INDEX_HTML)
        self.assertIn('id="backgroundClearBtn"', INDEX_HTML)
        self.assertIn("background_records", APP_JS)
        self.assertIn("backgroundClearBtn", APP_JS)
        self.assertIn('id="scheduleList"', INDEX_HTML)
        self.assertIn('id="scheduleEditor"', INDEX_HTML)
        for control in (
            "scheduleName", "scheduleType", "schedulePrompt",
            "scheduleSource", "scheduleWhen", "scheduleDelay",
            "scheduleAt", "scheduleEvery", "scheduleDailyAt",
            "scheduleEnabled", "scheduleSaveBtn", "scheduleCancelBtn",
        ):
            self.assertIn('id="%s"' % control, INDEX_HTML)
        # Background jobs are rendered on their screen, not in the chat log.
        self.assertIn("$('backgroundList')", APP_JS)
        self.assertNotIn("card('后台任务", APP_JS)
        self.assertIn("function syncSchedules", APP_JS)
        self.assertIn("function renderScheduleRow", APP_JS)
        self.assertIn("function saveSchedule", APP_JS)
        self.assertIn("/api/schedules", APP_JS)
        self.assertIn("status.schedules", APP_JS)
        self.assertIn("schedule_seq", APP_JS)
        self.assertIn("busy_source==='schedule'", APP_JS)
        self.assertIn(".schedule-job", APP_CSS)
        self.assertIn(".screen-inner", APP_CSS)

    def test_background_and_scheduled_continuations_start_new_turns(self):
        # A continuation has no new user bubble but is still a separate agent
        # message; live rendering and history refresh must both start a turn.
        self.assertIn("if(data.separate_turn) startTurn();", APP_JS)
        self.assertIn("后台任务结果", APP_JS)
        self.assertIn("Local ESP32 background execution result", APP_JS)
        self.assertIn("label='后台任务结果'; startTurn();", APP_JS)
        self.assertIn("startTurn();\n  stream('/api/continue'", APP_JS)
        self.assertIn("separate_turn", APP_JS)

    def test_search_settings_controls(self):
        for control in (
            "cfgSearchEnabled", "cfgSearchProvider", "searchSaveBtn",
            "cfgHttpToolEnabled", "cfgHttpAllowPlain", "searchEffective",
        ):
            self.assertIn(control, INDEX_HTML)
        search_select = INDEX_HTML[INDEX_HTML.index(
            'id="cfgSearchProvider"'):]
        search_select = search_select[:search_select.index("</select>")]
        self.assertIn('<option value="auto">', search_select)
        self.assertIn('<option value="bing">', search_select)
        self.assertNotIn('<option value="deepseek">', search_select)
        self.assertNotIn('<option value="anthropic">', search_select)
        self.assertNotIn('<option value="openai">', search_select)
        self.assertNotIn("cfgSearchUrl", INDEX_HTML)
        self.assertNotIn("cfgSearchApiKey", INDEX_HTML)
        self.assertIn("跟随模型 API", INDEX_HTML)
        self.assertIn("Bing（免 Key）", INDEX_HTML)
        self.assertIn("search_provider", APP_JS)
        self.assertIn("search_provider_effective", APP_JS)
        self.assertIn("searchProviderLabel", APP_JS)
        self.assertNotIn("search_api_key_clear", APP_JS)
        self.assertIn("http_tool_enabled", APP_JS)

    def test_web_command_confirmation_flow(self):
        self.assertIn("function runCommand", APP_JS)
        self.assertIn("data.code==='confirm'", APP_JS)
        self.assertIn("runCommand(line,true)", APP_JS)
        self.assertIn("confirm:!!confirmed", APP_JS)

    def test_live_poll_and_scroll_guards(self):
        self.assertIn("livePolling", APP_JS)
        self.assertIn("liveGen", APP_JS)
        self.assertIn("gen!==state.liveGen", APP_JS)
        self.assertIn("function scrollIfStick", APP_JS)
        self.assertIn("var stick=pageNearBottom();", APP_JS)
        self.assertIn("scrollIfStick(stick)", APP_JS)
        # Every model request starts a new set of independent bubbles.
        self.assertIn("if(ev.t==='model_start'){", APP_JS)
        self.assertIn("state.current=null; state.reasoning=null;", APP_JS)
        self.assertNotIn("turn-card", APP_JS)
        self.assertIn("function startTurn", APP_JS)
        self.assertIn("message.reasoning", APP_JS)

    def test_tool_only_rounds_do_not_create_empty_answer_bubbles(self):
        self.assertIn(
            "if(!state.current&&ev.text) setAssistantText(ev.text);", APP_JS)
        self.assertIn(
            "else if(state.current&&ev.text&&!state.current.text)", APP_JS)
        self.assertNotIn("if(ev.ok && !ev.error) return;", APP_JS)

    def test_stopped_stream_waits_for_device_before_history_refresh(self):
        # Aborting fetch rejects the request. The catch handler must keep
        # activeStream set so the stop poll can finish, refresh history and
        # attach the per-message rewind button.
        self.assertIn(
            "if(active.stopped){waitForStoppedTurn(active);return;}", APP_JS)
        self.assertIn(
            "state.activeStream=null;\n"
            "    state.stopWaiting=false;\n"
            "    setBusy(false);", APP_JS)
        self.assertIn("refreshHistory().catch(function(){});", APP_JS)
        self.assertIn("function waitForStoppedTurn(active){", APP_JS)
        self.assertIn("function pollStoppedTurn(active){", APP_JS)

    def test_auto_toggle_is_confirmed_and_preserves_feedback(self):
        self.assertIn("function changeAuto", APP_JS)
        self.assertIn("AUTO 会自动运行模型生成的代码", APP_JS)
        self.assertIn("auto_run:enable,auto_feedback:feedback", APP_JS)
        self.assertIn("confirm:true", APP_JS)
        self.assertNotIn("auto_save_tools", APP_JS)
        self.assertIn("function setBehaviorSaving", APP_JS)
        self.assertIn("restoreChatBehavior", APP_JS)

    def test_non_body_content_uses_default_closed_details(self):
        self.assertIn("function collapsibleBlock", APP_JS)
        self.assertIn("if(open===true) details.open=true", APP_JS)
        self.assertIn("function renderAssistantBody", APP_JS)
        self.assertIn("function assistantSegments", APP_JS)
        self.assertIn("代码接收中（", APP_JS)
        for summary in (
            "查看代码", "查看执行输出", "查看完整提案",
            "工具结果", "执行结果", "Harness 记录",
        ):
            self.assertIn(summary, APP_JS)
        self.assertIn("openStates[detailIndex]===true", APP_JS)

    def test_ca_customization_is_collapsed_unless_ca_is_missing(self):
        self.assertIn('<details id="caAdvanced" class="ca-advanced">', INDEX_HTML)
        self.assertIn('高级：自定义 CA（通常无需修改）', INDEX_HTML)
        self.assertIn('不想自定义 CA 时，保持当前路径和 TLS 验证开启即可', INDEX_HTML)
        self.assertIn("$('caAdvanced').open=false", APP_JS)
        self.assertIn("$('caAdvanced').open=true", APP_JS)
        self.assertIn('cfg.ca_present', APP_JS)
        self.assertIn('id="cfgCaFile"', INDEX_HTML)
        self.assertIn('id="cfgCaPem"', INDEX_HTML)
        self.assertIn('.ca-advanced', APP_CSS)

    def test_provider_mcp_and_persistence_controls_exist(self):
        self.assertIn('id="setupProvider"', INDEX_HTML)
        self.assertIn('id="cfgProvider"', INDEX_HTML)
        self.assertIn('value="anthropic"', INDEX_HTML)
        self.assertIn('id="cfgMcpEnabled"', INDEX_HTML)
        self.assertIn('id="cfgMcpServers"', INDEX_HTML)
        self.assertIn('id="cfgPersistConversation"', INDEX_HTML)
        self.assertIn('id="cfgMaxTurns"', INDEX_HTML)
        self.assertIn('id="cfgMaxToolCalls"', INDEX_HTML)
        self.assertIn('id="cfgContextTokens"', INDEX_HTML)
        self.assertIn('id="cfgAutoCompactTokens"', INDEX_HTML)
        self.assertIn('id="cfgAutoCompact"', INDEX_HTML)
        self.assertIn("max_tool_calls:parseInt", APP_JS)
        self.assertIn("context_tokens:contextTokens", APP_JS)
        self.assertIn("auto_compact_tokens:compactTokens", APP_JS)
        self.assertIn('id="cfgNtpServer"', INDEX_HTML)
        self.assertIn('id="cfgTimezoneOffset"', INDEX_HTML)
        self.assertIn('id="cfgNtpIntervalHours"', INDEX_HTML)
        self.assertIn("max_turns:parseInt", APP_JS)
        self.assertIn("timezone_offset_minutes:tzOffset", APP_JS)
        self.assertIn("ntp_sync_interval_s:Math.round", APP_JS)
        self.assertIn("function loadMcp", APP_JS)
        self.assertIn("api_provider:$('cfgProvider').value", APP_JS)
        self.assertIn("mcp_enabled:$('cfgMcpEnabled').checked", APP_JS)
        self.assertIn("persist_conversation:$('cfgPersistConversation').checked", APP_JS)
        self.assertIn("/api/mcp/refresh", APP_JS)

    def test_composer_controls_are_compact_and_responsive(self):
        self.assertIn(".composer-options", APP_CSS)
        self.assertIn(".composer-thinking", APP_CSS)
        self.assertIn(".auto-toggle", APP_CSS)
        self.assertIn("@media (max-width:640px)", APP_CSS)
        # Thinking/AUTO sit to the left of the textarea on one row.
        self.assertIn("order:0", APP_CSS)
        self.assertIn("flex:0 0 auto", APP_CSS)
        self.assertIn("--composer-h:78px", APP_CSS)
        self.assertIn("--composer-h:84px", APP_CSS)
        self.assertIn(".composer-thinking-label{display:none}", APP_CSS)
        self.assertIn("function syncComposerHeight", APP_JS)
        self.assertIn("--composer-h", APP_JS)
        self.assertIn('id="chatInput" rows="1"', INDEX_HTML)
        self.assertIn("flex-wrap:wrap", APP_CSS)

    def test_factory_reset_is_explicitly_confirmed(self):
        self.assertIn('id="factoryResetBtn"', INDEX_HTML)
        self.assertIn('id="factoryPassword"', INDEX_HTML)
        self.assertIn('id="factoryConfirm"', INDEX_HTML)
        self.assertIn("恢复出厂设置", INDEX_HTML)
        self.assertIn("/api/factory-reset", APP_JS)
        self.assertIn("confirm:true,password:password", APP_JS)
        self.assertIn("factoryConfirm", APP_JS)
        self.assertIn("请先勾选确认删除所有用户数据", APP_JS)
        self.assertIn("ESP32-Harness-XXXX", APP_JS)

    def test_sidebar_navigation_and_settings_sections(self):
        self.assertIn('<aside id="sidebar" class="sidebar"', INDEX_HTML)
        self.assertIn('id="sidebarToggleBtn"', INDEX_HTML)
        self.assertIn('id="sidebarCloseBtn"', INDEX_HTML)
        self.assertIn('id="sidebarBackdrop"', INDEX_HTML)
        for nav in (
            "status", "chat", "background", "schedules", "skills", "files",
            "settings-network", "settings-model", "settings-mcp",
            "settings-search", "settings-behavior", "settings-security",
            "settings-factory",
        ):
            self.assertIn('data-nav="%s"' % nav, INDEX_HTML)
        for panel in (
            "network", "model", "mcp", "search", "behavior", "security",
            "factory",
        ):
            self.assertIn('data-settings-panel="%s"' % panel, INDEX_HTML)
        self.assertNotIn('id="settingsView"', INDEX_HTML)
        self.assertNotIn('id="settingsBtn"', INDEX_HTML)
        self.assertIn("function showScreen", APP_JS)
        self.assertIn("function syncSidebar", APP_JS)
        self.assertIn("function showSettingsPanel", APP_JS)
        self.assertIn("harness-sidebar-collapsed", APP_JS)
        self.assertIn("@media (max-width:900px)", APP_CSS)
        self.assertIn("body.sidebar-collapsed .sidebar", APP_CSS)
        self.assertIn("body.sidebar-open .sidebar", APP_CSS)

    def test_status_screen_shows_network_context_and_actions(self):
        for control in (
            "statusMode", "statusIp", "statusSsid", "statusHeap",
            "statusModel", "contextBar", "contextText", "contextSummary",
            "clearBtn", "compactBtn", "historyBtn",
        ):
            self.assertIn('id="%s"' % control, INDEX_HTML)
        self.assertIn("function renderStatusScreen", APP_JS)
        self.assertIn("history.summary_chars", APP_JS)
        self.assertIn("name==='status'", APP_JS)
        self.assertIn('id="screenStatus"', INDEX_HTML)

    def test_commands_are_integrated_into_the_chat_composer(self):
        self.assertNotIn('id="commandForm"', INDEX_HTML)
        self.assertNotIn('id="commandsPane"', INDEX_HTML)
        self.assertIn("if(text.charAt(0)==='/'){", APP_JS)
        self.assertIn("runCommand(text,false);", APP_JS)
        self.assertIn("function commandEcho", APP_JS)
        self.assertIn("function commandAffectsHistory", APP_JS)
        self.assertIn("/api/command", APP_JS)
        self.assertIn("command-output", APP_JS)
        self.assertIn(".command-echo", APP_CSS)
        self.assertIn("输入消息，或 / 命令", INDEX_HTML)

    def test_agent_events_render_as_independent_bubbles(self):
        self.assertIn("function assistantAnswer", APP_JS)
        self.assertIn("function assistantSegments", APP_JS)
        self.assertIn("function reasoningBubble", APP_JS)
        self.assertIn("function toolCallBubble", APP_JS)
        self.assertIn("function toolResultBubble", APP_JS)
        self.assertIn("function addBubble", APP_JS)
        self.assertIn("代码接收中（", APP_JS)
        self.assertIn("工具调用 · ", APP_JS)
        self.assertIn("工具结果 · ", APP_JS)
        self.assertIn(".bubble.reasoning", APP_CSS)
        self.assertIn(".bubble.tool", APP_CSS)
        self.assertIn(".bubble.code-card", APP_CSS)
        self.assertIn("bubble-label", APP_CSS)

    def test_file_page_exposes_browse_and_write_controls(self):
        for control in (
            "fileUpBtn", "fileRefreshBtn", "fileMkdirBtn", "fileNewBtn",
            "fileUploadBtn", "fileUploadInput", "filePath", "fileList",
            "fileEditor", "fileEditorText", "fileSaveBtn", "fileCancelBtn",
        ):
            self.assertIn('id="%s"' % control, INDEX_HTML)
        self.assertIn("function loadFiles", APP_JS)
        self.assertIn("function fileRow", APP_JS)
        self.assertIn("function openFile", APP_JS)
        self.assertIn("function uploadFile", APP_JS)
        self.assertIn("/api/files", APP_JS)
        self.assertIn("/api/files/download", APP_JS)
        self.assertIn("/api/files/rename", APP_JS)
        self.assertIn("FileReader", APP_JS)
        self.assertIn("系统", INDEX_HTML)
        self.assertIn("敏感", APP_JS)
        self.assertIn(".file-row", APP_CSS)

    def test_skill_page_lists_and_opens_skill_bundles(self):
        self.assertIn('id="screenSkills"', INDEX_HTML)
        self.assertIn('id="skillsList"', INDEX_HTML)
        self.assertIn('id="skillsStatus"', INDEX_HTML)
        self.assertIn("function loadSkills", APP_JS)
        self.assertIn("function renderSkills", APP_JS)
        self.assertIn("/api/skills", APP_JS)
        self.assertIn("SKILL.md", APP_JS)
        self.assertIn(".skill-job", APP_CSS)

    def test_sidebar_navigation_scrolls_chat_to_latest_activity(self):
        self.assertIn("function scrollPageBottom", APP_JS)
        self.assertIn("document.scrollingElement", APP_JS)
        self.assertIn("function pageNearBottom", APP_JS)
        self.assertIn("function autoScrollChat", APP_JS)
        self.assertNotIn("$('chatLog').scrollTop", APP_JS)
        self.assertIn("autoScrollChat(true)", APP_JS)
        self.assertIn(
            "state.liveUserEl=logMessage('user',text,'user');"
            "\n  scrollPageBottomSoon();", APP_JS)
        self.assertIn("showScreen('chat')", APP_JS)
        self.assertIn("if(name==='chat'){\n    updateEmptyState(); autoScrollChat(true);",
                      APP_JS)


if __name__ == "__main__":
    unittest.main()
