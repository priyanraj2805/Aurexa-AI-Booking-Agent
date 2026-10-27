/**
 * DotStark Booking Widget — Kilo Code Design v2
 * Shadow DOM web component with rich micro-interactions.
 *
 * Embedding (production):
 *   <script src="https://your-domain/static/widget/widget.js"
 *     data-widget-token="ds_wid_xxx" data-api-base="https://your-domain/api/v1"
 *     data-color="#FF6B00" defer></script>
 *
 * Embedding (dev):
 *   window.DotStarkBookingConfig = { widgetToken, apiBase, primaryColor };
 */

(function () {
  const _WIDGET_LOAD_BUILD = "2026-09-26-whatsapp-only-otp";
  console.warn("[DotStark] widget.js loaded, build:", _WIDGET_LOAD_BUILD, "href:", window.location.href);
  console.warn("[DotStark] If you see no logs after Verify, ensure console filter is set to All/Verbose, not Errors only");

  // Force-refresh build tag so embed caches bust after this fix
  const WIDGET_BUILD = "2026-09-26-whatsapp-only-otp";

  // Send lightweight beacon to backend so docker logs show widget events even if browser console hidden
  function _logToBackend(event, detail) {
    try {
      const base = (typeof CONFIG !== "undefined" && CONFIG.apiBase) ? CONFIG.apiBase : "/api/v1";
      const token = (typeof CONFIG !== "undefined" && CONFIG.widgetToken) ? CONFIG.widgetToken || "" : "";
      // sessionId is stored globally by widget after it gets one
      let sid = "";
      try { sid = window._dotstarkLastSessionId || ""; } catch (_) {}
      try {
        const el = document.querySelector("booking-widget");
        if (el && el.sessionId) sid = el.sessionId;
      } catch (_) {}
      const payload = {
        event: String(event || "unknown").slice(0, 40),
        session_id: String(sid || "").slice(0, 36),
        channel: "phone",
        detail: String(detail || "").slice(0, 200),
        phone_masked: ""
      };
      fetch(`${base}/widget/client-log`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Widget-Token": token, "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify(payload),
        keepalive: true
      }).catch(() => {});
    } catch (_) {}
  }

  function _defaultApiBase(scriptEl) {
    try {
      if (scriptEl && scriptEl.src) {
        const srcUrl = new URL(scriptEl.src, window.location.href);
        return `${srcUrl.origin}/api/v1`;
      }
      return `${window.location.origin}/api/v1`;
    } catch (e) {
      return "/api/v1";
    }
  }

  // Auto-detect config from <script data-widget-token="..."> attributes
  function _readScriptAttrs() {
    const scripts = document.querySelectorAll("script[data-widget-token]");
    if (!scripts.length) {
      // Fallback: try legacy data-api-key for backward compat
      const legacy = document.querySelectorAll("script[data-api-key]");
      if (!legacy.length) return null;
      const s = legacy[legacy.length - 1];
      const defaultApiBase = _defaultApiBase(s);
      return {
        widgetToken: s.getAttribute("data-api-key"),
        apiBase: s.getAttribute("data-api-base") || defaultApiBase,
        primaryColor: s.getAttribute("data-color") || null,
      };
    }
    const s = scripts[scripts.length - 1];
    const defaultApiBase = _defaultApiBase(s);
    return {
      widgetToken: s.getAttribute("data-widget-token"),
      apiBase: s.getAttribute("data-api-base") || defaultApiBase,
      primaryColor: s.getAttribute("data-color") || null,
    };
  }

  const CONFIG = _readScriptAttrs() ||
    window.DotStarkBookingConfig || {
      widgetToken: null,
      apiBase: _defaultApiBase(document.currentScript),
    };

  class BookingWidget extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this.isOpen = false;
      this.sessionId = null;
      this.isLoading = false;
      this._messages = [];
      this._agentConfig = null;
      // Org-mode: picked department + team member
      this.selectedUnitId = null;
      this.selectedProviderId = null;
      this._catalogPickerShown = false;
      this.userTimezone = this._detectBrowserTimezone();
      this.timezoneSource = "detected";
      this.selectedSlotId = null;
      this.timezoneFallbackUsed = false;
      this.timezoneFallbackReason = null;
      this._timezoneNoticeShown = false;
      this.orgTimezone = null;
      this.selectedDate = null;
      this._directiveDrafts = {};
      this._activeFieldMeta = null;
      this._skipToken = "__DOTSTARK_SKIP__";
      this._lastMessageTimestamp = null;
      this._pollInterval = null;
      this._humanActive = false;
      // Conversation mode: null (choose) | "ask" (KB Q&A) | "booking" (guided).
      this._chatMode = null;
      this._lastDirective = null;
      this._logoSrc = null;
      // Phase 4: WebSocket lifecycle state
      this._ws = null;
      this._wsReconnectTimer = null;
      this._wsReconnectAttempts = 0;
      this._wsConnected = false;
      // Phase 6/9: track rendered message ids to avoid duplicates when WS + poll overlap
      this._renderedMessageIds = new Set();
      // Bubbles just rendered locally during a send; the post-send history
      // refresh must skip re-rendering these (they have no DB ids yet).
      this._pendingRefreshSkips = [];
    }

    _sessionStorageKey() {
      const tokenPart = (CONFIG.widgetToken || "anonymous").trim() || "anonymous";
      const basePart = (CONFIG.apiBase || "").trim() || "default";
      return `dotstark_session_id:${tokenPart}:${basePart}`;
    }

    _modeStorageKey() {
      return `${this._sessionStorageKey()}:mode`;
    }

    _sessionKeyFor(mode) {
      // Per-section session: Ask AI and Start Booking each keep their own
      // backend session, so switching between sections never loses the
      // booking details the visitor already shared.
      return `${this._sessionStorageKey()}:${mode === "ask" ? "ask" : "booking"}`;
    }

    _loadChatMode() {
      try {
        const m = sessionStorage.getItem(this._modeStorageKey());
        this._chatMode = m === "ask" || m === "booking" ? m : null;
      } catch (_) {
        this._chatMode = null;
      }
    }

    _saveChatMode() {
      try {
        if (this._chatMode) sessionStorage.setItem(this._modeStorageKey(), this._chatMode);
        else sessionStorage.removeItem(this._modeStorageKey());
      } catch (_) {}
    }

    async connectedCallback() {
      try {
        const sessionKey = this._sessionStorageKey();
        if (CONFIG.forceNewSession === true) {
          sessionStorage.removeItem(sessionKey);
          sessionStorage.removeItem("dotstark_session_id");
          try { sessionStorage.removeItem(this._sessionKeyFor("ask")); } catch (_) {}
          try { sessionStorage.removeItem(this._sessionKeyFor("booking")); } catch (_) {}
          this.sessionId = null;
        } else {
          // Restore the session that belongs to the section the visitor
          // was last in (per-section sessions). Fall back to the legacy
          // shared key for visitors from before the split.
          const _m0 = sessionStorage.getItem(this._modeStorageKey());
          const _mode0 = _m0 === "ask" || _m0 === "booking" ? _m0 : null;
          this.sessionId =
            (_mode0 && sessionStorage.getItem(this._sessionKeyFor(_mode0))) ||
            sessionStorage.getItem(sessionKey) ||
            null;
        }
        try {
          const _m = sessionStorage.getItem(`${sessionKey}:mode`);
          this._chatMode = _m === "ask" || _m === "booking" ? _m : null;
        } catch (_) {}
        const storedTimezone = sessionStorage.getItem("dotstark_user_timezone");
        const storedSource = sessionStorage.getItem("dotstark_timezone_source");
        if (storedTimezone) {
          this.userTimezone = storedTimezone;
          this.timezoneSource =
            storedSource === "user_selected" ? "user_selected" : "detected";
        }
      } catch (e) {
        /* sessionStorage unavailable */
      }

      await this._fetchAgentConfig();
      this._buildShell();
      // Poll config every 30s so admin mode changes appear without page reload
      this._startConfigPolling();
      // Re-check the browser zone when the tab regains focus — VPN toggles
      // typically happen while the page sits in the background.
      try {
        this._tzVisibilityHandler = () => {
          if (document.visibilityState === "visible") this._refreshTimezone(true);
        };
        document.addEventListener("visibilitychange", this._tzVisibilityHandler);
        this._tzFocusHandler = () => this._refreshTimezone(true);
        window.addEventListener("focus", this._tzFocusHandler);
      } catch (_) {}
    }

    disconnectedCallback() {
      try {
        if (this._configPollInterval) clearInterval(this._configPollInterval);
        this._configPollInterval = null;
        if (this._tzVisibilityHandler) {
          document.removeEventListener("visibilitychange", this._tzVisibilityHandler);
          this._tzVisibilityHandler = null;
        }
        if (this._tzFocusHandler) {
          window.removeEventListener("focus", this._tzFocusHandler);
          this._tzFocusHandler = null;
        }
      } catch (_) {}
    }

    _startConfigPolling() {
      try {
        if (this._configPollInterval) clearInterval(this._configPollInterval);
        this._configPollInterval = setInterval(async () => {
          const prevMode = this._agentConfig?.meeting_mode;
          const prevAvail = JSON.stringify(this._agentConfig?.available_meeting_types || []);
          await this._fetchAgentConfig();
          const newMode = this._agentConfig?.meeting_mode;
          const newAvail = JSON.stringify(this._agentConfig?.available_meeting_types || []);
          if (prevMode !== newMode || prevAvail !== newAvail) {
            this._updateMeetingModeBadge();
            console.log("[DotStark] meeting mode updated via poll:", prevMode, "->", newMode);
          }
        }, 30000);
      } catch (_) {}
    }

    _updateMeetingModeBadge() {
      try {
        const badge = this.shadowRoot && this.shadowRoot.getElementById("meeting-mode-badge");
        if (!badge) return;
        const cfg = this._agentConfig || {};
        const mode = (cfg.meeting_mode || "").toString();
        const avail = Array.isArray(cfg.available_meeting_types) ? cfg.available_meeting_types : [];
        let text = "";
        let show = true;
        if (mode === "ONLINE_ONLY" || (avail.length === 1 && avail[0] === "ONLINE")) {
          text = "Online • " + (cfg.meeting_provider_default && cfg.meeting_provider_default !== "NONE" ? "Google Meet" : "Online");
        } else if (mode === "OFFLINE_ONLY" || (avail.length === 1 && avail[0] === "OFFLINE")) {
          text = "Offline • In-person";
        } else if (mode === "HYBRID" || avail.length === 2) {
          text = "Hybrid • Online + Offline";
        } else {
          show = false;
        }
        if (show && text) {
          badge.textContent = text;
          badge.style.display = "inline-block";
          // White text, border = brand color
          try {
            const brand = this._agentConfig?.brand_color || (typeof CONFIG !== "undefined" && CONFIG.primaryColor) || this.style.getPropertyValue("--brand-color") || "#3B82F6";
            badge.style.color = "white";
            badge.style.borderColor = brand || "#3B82F6";
            badge.style.borderWidth = "1.5px";
            badge.style.borderStyle = "solid";
            badge.style.background = "rgba(255,255,255,0.10)";
          } catch (_) {
            badge.style.color = "white";
          }
        } else {
          badge.style.display = "none";
        }
      } catch (_) {}
    }

    async _fetchAgentConfig() {
      if (!CONFIG.widgetToken) return;
      try {
        const base = CONFIG.apiBase.replace(/\/api\/v1\/?$/, "") + "/api/v1";
        const res = await fetch(
          `${base}/widget/config?widget_token=${encodeURIComponent(CONFIG.widgetToken)}`,
        );
        if (res.ok) {
          this._agentConfig = await res.json();
          // Update badge immediately if shell already built
          this._updateMeetingModeBadge();
        }
      } catch (e) {
        /* config fetch failed — use defaults */
      }
    }

    async _loadHistory(sinceTimestamp = null) {
      if (!this.sessionId || !CONFIG.widgetToken) return false;
      // Session token: a history fetch started before a section switch must
      // not paint the old section's transcript into the new section's view.
      const _histSession = this.sessionId;
      try {
        const base = CONFIG.apiBase.replace(/\/api\/v1\/?$/, "") + "/api/v1";
        let url = `${base}/widget/history?session_id=${encodeURIComponent(this.sessionId)}&widget_token=${encodeURIComponent(CONFIG.widgetToken)}`;
        if (sinceTimestamp) {
          url += `&since=${encodeURIComponent(sinceTimestamp)}`;
        }
        const res = await fetch(url);
        if (!res.ok) return false;
        const messages = await res.json();
        if (this.sessionId !== _histSession) return false;
        if (!messages || messages.length === 0) return false;
        for (const m of messages) {
          const isHumanJoined = m.metadata && m.metadata.human_joined;
          const isHumanLeft = m.metadata && m.metadata.human_left;
          const content =
            m.role === "agent"
              ? this._resolveAssistantText(m.content)
              : String(m.content || "");
          // Skip appending human_joined/human_left system messages (we show them in header instead)
          if (!isHumanJoined && !isHumanLeft && content) {
            // Skip agent messages that are directive cards — they are
            // re-rendered as cards by _resumePendingCard, so rendering them
            // here as bubbles causes duplicates.
            if (m.role === "agent" && m.metadata && m.metadata.directive) {
              if (m.id) this._renderedMessageIds.add(m.id);
              continue;
            }
            const alreadyRendered = !!(m.id && this._renderedMessageIds.has(m.id));
            const justRendered = this._pendingRefreshSkips.some(
              (j) => j.role === m.role && j.content === content,
            );
            if (!alreadyRendered && !justRendered) {
              const meta = m.sender_type === "human" ? { admin_name: m.human_agent_name } : {};
              if (m.id) this._renderedMessageIds.add(m.id);
              this._appendBubble(m.role, content, meta);
            }
          }
          // Track the latest message timestamp for incremental polling
          if (m.created_at) {
            this._lastMessageTimestamp = m.created_at;
          }
          // Check for human_left in metadata
          if (m.metadata && m.metadata.human_left) {
            this._onHumanLeft();
          }
          // Check for human_joined in metadata
          if (m.metadata && m.metadata.human_joined) {
            const adminName = m.metadata.admin_name || "Support agent";
            this._updateHumanStatus(adminName, true);
            this._startFastPolling();
          }
          // Also trigger fast polling if a human agent sent a message
          if (m.sender_type === "human") {
            this._startFastPolling();
          }
        }
        this._pendingRefreshSkips = [];
        return true;
      } catch (e) {
        return false;
      }
    }

    /* ─────────── POLLING (fallback only when WS is down) ─────────── */
    _startPolling() {
      if (this._pollInterval) return;
      // Slow polling (10s) when WS is connected, fast polling (3s) when
      // human is active and WS is down (recovery path).
      const baseInterval = this._wsConnected ? 30000 : (this._humanActive ? 3000 : 10000);
      this._pollInterval = setInterval(() => {
        if (this.isOpen && this.sessionId) {
          this._loadHistory(this._lastMessageTimestamp);
        }
      }, baseInterval);
    }

    _stopPolling(preserveHumanActive = false) {
      if (this._pollInterval) {
        clearInterval(this._pollInterval);
        this._pollInterval = null;
      }
      if (!preserveHumanActive) {
        this._humanActive = false;
      }
    }

    _startFastPolling() {
      if (this._humanActive) return;
      this._humanActive = true;
      this._stopPolling(true);
      this._startPolling();
    }

    _onHumanLeft() {
      this._updateHumanStatus(null, false);
      this._stopPolling();
    }

    _updateHumanStatus(adminName, isActive) {
      const statusText = this.shadowRoot.getElementById("status-text");
      const statusDot = this.shadowRoot.querySelector(".status-dot");
      if (!statusText) return;

      if (isActive && adminName) {
        statusText.textContent = `👨‍💼 ${adminName} is now assisting you`;
        if (statusDot) statusDot.style.background = "#f59e0b"; // amber for human agent
      } else {
        statusText.textContent = "Online \u2014 Ready to help";
        if (statusDot) statusDot.style.background = "#22c55e"; // green for AI
      }
    }

    /* ─────────── WEBSOCKET (real-time channel; Phase 4) ─────────── */

    _wsBaseUrl() {
      // Convert http(s) → ws(s) and strip /api/v1 suffix because the WS
      // endpoint lives at /ws/widget (under api/v1).
      const base = (CONFIG.apiBase || "").replace(/\/+$/, "");
      if (base.startsWith("https://")) return "wss://" + base.slice(8);
      if (base.startsWith("http://")) return "ws://" + base.slice(7);
      return base;
    }

    _connectWebSocket() {
      if (!this.sessionId || !CONFIG.widgetToken) return;
      if (this._ws && this._ws.readyState <= 1) return; // CONNECTING or OPEN
      try {
        const url = `${this._wsBaseUrl()}/ws/widget?widget_token=${encodeURIComponent(CONFIG.widgetToken)}&session_id=${encodeURIComponent(this.sessionId)}`;
        const ws = new WebSocket(url);
        this._ws = ws;

        ws.onopen = () => {
          this._wsConnected = true;
          this._wsReconnectAttempts = 0;
          // Restart polling with the slow "WS-connected" cadence so it
          // exists as a recovery safety net but doesn't hammer the server.
          if (this._pollInterval) {
            this._stopPolling(true);
            this._startPolling();
          }
        };

        ws.onmessage = (e) => {
          let event;
          try { event = JSON.parse(e.data); } catch { return; }
          this._handleWsEvent(event);
        };

        ws.onclose = () => {
          this._wsConnected = false;
          this._ws = null;
          // Reconnect with exponential backoff (capped at 30s)
          const delay = Math.min(30000, 1000 * Math.pow(2, this._wsReconnectAttempts));
          this._wsReconnectAttempts++;
          if (this._wsReconnectTimer) clearTimeout(this._wsReconnectTimer);
          this._wsReconnectTimer = setTimeout(() => this._connectWebSocket(), delay);
          // While WS is down, ensure polling is active at the human-aware rate
          if (this._pollInterval) {
            this._stopPolling(true);
            this._startPolling();
          }
        };

        ws.onerror = () => {
          // onclose will fire after; nothing extra to do here.
        };
      } catch (e) {
        // Construction failure → retry via onclose path on next tick
        this._wsConnected = false;
        if (this._wsReconnectTimer) clearTimeout(this._wsReconnectTimer);
        this._wsReconnectTimer = setTimeout(() => this._connectWebSocket(), 3000);
      }
    }

    _handleWsEvent(event) {
      // Stale-socket echo: after a section switch the old socket is closed,
      // but an already-queued event for the abandoned session must not
      // render into the new section's view.
      try {
        if (event && event.session_id && this.sessionId && event.session_id !== this.sessionId) return;
      } catch (_) {}
      switch (event.type) {
        case "connected":
          // Server confirmed registration; nothing else to do.
          break;
        case "human_joined":
          this._updateHumanStatus(event.admin_name || "Support agent", true);
          try { this._clearQuickPrompts(); } catch (_) {}
          try { this._setComposerVisible(true); } catch (_) {}
          this._startFastPolling();
          // Idempotent: skip if a system message for this join already shown
          if (!this._humanJoinedShownFor) {
            this._humanJoinedShownFor = event.session_id;
            this._appendSystem(`👨‍💼 ${event.admin_name || "Support agent"} joined the chat`);
          }
          break;
        case "human_left":
          this._updateHumanStatus(null, false);
          this._humanJoinedShownFor = null;
          this._appendSystem("👨‍💼 Support agent left. AI assistant resumed.");
          this._stopPolling();
          // Composer follows the mode again once the human leaves.
          try { this._applyComposerRule(); } catch (_) {}
          // Reconnect polling at slow cadence
          this._startPolling();
          break;
        case "human_message":
          // Phase 6/9: render as a distinct human bubble
          if (event.message_id && this._renderedMessageIds.has(event.message_id)) {
            return; // de-dupe
          }
          this._appendHumanBubble(event);
          if (event.message_id) {
            this._renderedMessageIds.add(event.message_id);
            this._lastMessageTimestamp = event.created_at || this._lastMessageTimestamp;
          }
          break;
        case "customer_message":
          // Echo of our own message; no-op (already shown via _handleSend)
          break;
        default:
          break;
      }
    }

    /* ─────────── BUILD ONCE ─────────── */
    _buildShell() {
      const _logoUrl = this._agentConfig?.logo_url || null;
      let _logoSrc = null;
      if (_logoUrl) {
        try {
          _logoSrc = _logoUrl.startsWith("http") ? _logoUrl : new URL(_logoUrl, CONFIG.apiBase).href;
        } catch (e) {
          _logoSrc = _logoUrl;
        }
      }
      this._logoSrc = _logoSrc || null;
      const _avatarInner = _logoSrc
        ? `<img src="${_logoSrc}" alt="logo" style="width:100%;height:100%;object-fit:cover;border-radius:12px;display:block;" onerror="this.style.display='none';this.nextElementSibling&& (this.nextElementSibling.style.display='block')">` + `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" style="display:none"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10H5a2 2 0 0 0-2 2v1a7 7 0 0 0 14 0v-1a2 2 0 0 0-2-2z"/><line x1="12" y1="18" x2="12" y2="22"/></svg>`
        : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10H5a2 2 0 0 0-2 2v1a7 7 0 0 0 14 0v-1a2 2 0 0 0-2-2z"/><line x1="12" y1="18" x2="12" y2="22"/></svg>`;
      this.shadowRoot.innerHTML = `
                <style>${this._css()}</style>

                <div class="chat-container" id="chat-box" role="dialog" aria-label="Booking Assistant Chat" aria-hidden="true">
                    <div class="header">
                        <div class="header-avatar" id="avatar" aria-hidden="true">
                            ${_avatarInner}
                        </div>
                        <div class="header-info">
                            <div style="display:flex; align-items:center; gap:8px; min-width:0;">
                                <h3 id="agent-name-title" style="margin:0;">${this._agentConfig?.agent_name || "Booking Assistant"}</h3>
                                <span id="meeting-mode-badge" class="meeting-mode-badge" style="display:none; font-size:10px; font-weight:700; padding:2px 7px; border-radius:20px; background:rgba(255,255,255,0.10); border:1.5px solid var(--brand-color, #3B82F6); color:white; white-space:nowrap; flex-shrink:0;"></span>
                            </div>
                            ${(() => { const c = this._agentConfig || {}; const b = [c.business_name, c.business_tagline].filter(Boolean).join(" - "); if (!b) return ""; const esc = b.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); const dur = Math.max(8, Math.min(30, Math.round(b.length * 0.35))); return `<p class="header-business marquee" id="header-business" style="--marquee-duration:${dur}s"><span class="marquee-track"><span>${esc}</span><span>${esc}</span></span></p>`; })()}
                            <p><span class="status-dot"></span> <span id="status-text">Online \u2014 Ready to help</span></p>
                        </div>
                        <button class="header-back" id="back-btn" title="Back" style="display:none">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
                        </button>
                        <button class="header-close" id="close-btn" title="Close chat">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                    </div>
                    <div class="home-view" id="home-view" style="display:none">
                        <div class="home-avatar" id="home-avatar"></div>
                        <h3 class="home-title" id="home-title">Booking Assistant</h3>
                        <p class="home-tagline" id="home-tagline">How can we help you today?</p>
                        <button class="home-btn" id="home-ask" type="button">
                            <span class="home-btn-icon">🤖</span>
                            <span class="home-btn-text"><span class="home-btn-title">Ask AI</span><span class="home-btn-desc">Chat & get instant answers</span></span>
                        </button>
                        <button class="home-btn" id="home-book" type="button">
                            <span class="home-btn-icon">📅</span>
                            <span class="home-btn-text"><span class="home-btn-title">Book Appointment</span><span class="home-btn-desc">Pick a slot & confirm</span></span>
                        </button>
                    </div>
                    <div class="messages" id="message-list" role="log" aria-label="Chat messages" aria-live="polite"></div>
                    <!-- Suggestions row. The switch-pill group is a SIBLING of the
                         scrolling bar (not a sticky child of it), so a scrolled
                         suggestion can never slide under/behind the switch control. -->
                    <div class="qp-bar-wrap" id="qp-bar-wrap">
                        <span class="qp-switch-slot" id="qp-switch-slot"></span>
                        <button class="qp-nav qp-nav-left" id="qp-nav-left" type="button" aria-label="Scroll suggestions left">‹</button>
                        <div class="quick-prompts-bar" id="quick-prompts-bar" style="display:none" aria-label="Suggested questions"></div>
                        <button class="qp-nav qp-nav-right" id="qp-nav-right" type="button" aria-label="Scroll suggestions right">›</button>
                    </div>
                    <div class="input-area">
                        <input type="text" placeholder="Type a message..." id="user-input" autocomplete="off" aria-label="Type your message">
                        <button class="send-btn" id="send-trigger" title="Send message" aria-label="Send message">
                            <svg class="send-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                        </button>
                    </div>
                    <div class="widget-inline-validation" id="widget-inline-validation" aria-live="polite"></div>
                    <div class="powered-by">Powered by <a href="#">DotStark AI</a></div>
                </div>

                <div class="widget-launcher" id="launcher" role="button" tabindex="0" aria-label="Open booking chat" aria-expanded="false">
                    <svg class="icon-chat" viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.2L4 17.2V4h16v12z"/></svg>
                    <svg class="icon-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    <span class="ripple-ring"></span>
                </div>
            `;

      // Attach events
      this._launcher = this.shadowRoot.getElementById("launcher");
      this._chatBox = this.shadowRoot.getElementById("chat-box");
      this._input = this.shadowRoot.getElementById("user-input");
      this._sendBtn = this.shadowRoot.getElementById("send-trigger");
      this._msgList = this.shadowRoot.getElementById("message-list");
      this._inputArea = this.shadowRoot.querySelector(".input-area");
      this._qpBar = this.shadowRoot.getElementById("quick-prompts-bar");
      try { this._bindQpNav(); } catch (_) {}
      this._homeView = this.shadowRoot.getElementById("home-view");
      this._backBtn = this.shadowRoot.getElementById("back-btn");
      this._canGoHome = false;
      this._inlineValidation = this.shadowRoot.getElementById(
        "widget-inline-validation",
      );

      this._launcher.onclick = () => this._toggle();
      this._launcher.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          this._toggle();
        }
      };
      this.shadowRoot.getElementById("close-btn").onclick = () =>
        this._toggle();
      if (this._backBtn) {
        this._backBtn.onclick = () => this._showHomeView();
      }
      if (this.shadowRoot.getElementById("home-ask")) {
        this.shadowRoot.getElementById("home-ask").onclick = () => this._switchSection("ask");
      }
      if (this.shadowRoot.getElementById("home-book")) {
        this.shadowRoot.getElementById("home-book").onclick = () => this._switchSection("booking");
      }
      this._sendBtn.onclick = () => this._handleSend();
      this._input.onkeydown = (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          this._handleSend();
        }
        if (e.key === "Escape") this._toggle();
      };
      // Global Escape key to close widget
      this._chatBox.onkeydown = (e) => {
        if (e.key === "Escape" && this.isOpen) this._toggle();
      };

      // Blank open: home slide (two big buttons), composer hidden.
      // Returning visitors with a stored session resume history directly
      // (composer follows their saved mode).
      try { this._fillHomeView(); } catch (_) {}
      if (this.sessionId) {
        this._loadHistory().then((restored) => {
          if (!restored) {
            this._canGoHome = true;
            try { this._showHomeView(); } catch (_) {}
            try { this._setComposerVisible(false); } catch (_) {}
          } else {
            this._canGoHome = false;
            try { this._updateBackBtn(); } catch (_) {}
            try { this._applyComposerRule(); } catch (_) {}
            // Guided booking is card-driven: after restoring the transcript
            // also re-render the pending step as its directive card.
            if (this._chatMode === "booking") {
              try { this._resumePendingCard(); } catch (_) {}
            }
          }
          // Phase 4: open WebSocket first (real-time channel); polling acts
          // as a safety net that auto-disables when WS is connected.
          this._connectWebSocket();
          this._startPolling();
        });
      } else {
        this._canGoHome = true;
        try { this._showHomeView(); } catch (_) {}
        try { this._setComposerVisible(false); } catch (_) {}
      }

      // Apply brand color if configured
      const brandColor = this._agentConfig?.brand_color || CONFIG.primaryColor;
      if (brandColor) {
        this.style.setProperty("--brand-color", brandColor);
      }
      // Primary action color for card buttons (Pay, rebook, new booking).
      // --primary was never defined, which left those buttons transparent.
      this.style.setProperty("--primary", brandColor || "#ea580c");
      // Feature 2: apply widget theme (position, radius, dark mode, branding)
      try { this._applyWidgetTheme(); } catch (_) {}
      // Show meeting mode badge (Online/Offline/Hybrid) if configured
      this._updateMeetingModeBadge();
    }

    _applyWidgetTheme() {
      const theme = (this._agentConfig && this._agentConfig.ui_theme) || {};
      const position = theme.position || "bottom-right";
      const radius = Math.max(0, Math.min(28, Number(theme.border_radius ?? 16) || 16));
      const darkMode = theme.dark_mode !== false;
      // Branding is ALWAYS shown (product decision 2025-10): the powered-by
      // strip is not configurable anymore, so any stored show_branding flag
      // is intentionally ignored.
      // Position: host is fixed bottom/right by default CSS.
      if (position === "bottom-left") {
        this.style.setProperty("right", "auto");
        this.style.setProperty("left", "24px");
      } else if (position === "center") {
        this.style.setProperty("right", "auto");
        this.style.setProperty("left", "50%");
        this.style.setProperty("transform", "translateX(-50%)");
      } else {
        this.style.setProperty("left", "auto");
        this.style.setProperty("right", "24px");
        this.style.setProperty("transform", "");
      }
      try {
        const box = this.shadowRoot && this.shadowRoot.getElementById("chat-box");
        if (box) {
          box.style.borderRadius = `${radius + 4}px`;
          if (position === "bottom-left") {
            box.style.right = "auto";
            box.style.left = "0";
            box.style.transformOrigin = "bottom left";
          } else if (position === "center") {
            box.style.right = "auto";
            box.style.left = "50%";
            box.style.transform = "translateX(-50%)";
            box.style.transformOrigin = "bottom center";
          }
        }
        const launcher = this.shadowRoot && this.shadowRoot.getElementById("launcher");
        if (launcher) launcher.style.borderRadius = `${radius}px`;
      } catch (_) {}
      // Dark / light mode via CSS variables on host.
      if (!darkMode) {
        this.style.setProperty("--bg-primary", "#ffffff");
        this.style.setProperty("--bg-secondary", "#f4f4f5");
        this.style.setProperty("--border", "#e4e4e7");
      }
    }

    _detectBrowserTimezone() {
      try {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        return tz || null;
      } catch (e) {
        return null;
      }
    }

    _persistTimezonePreference() {
      try {
        if (this.userTimezone) {
          sessionStorage.setItem("dotstark_user_timezone", this.userTimezone);
          sessionStorage.setItem("dotstark_timezone_source", this.timezoneSource);
        } else {
          sessionStorage.removeItem("dotstark_user_timezone");
          sessionStorage.removeItem("dotstark_timezone_source");
        }
      } catch (e) {
        /* sessionStorage unavailable */
      }
    }

    // Re-resolve the browser timezone (VPN/network changes mid-session).
    // Manual user selection always wins and is never overwritten. Returns
    // true when the zone actually changed.
    _refreshTimezone(announce = false) {
      try {
        if (this.timezoneSource === "user_selected") return false;
        const live = this._detectBrowserTimezone();
        if (live && live !== this.userTimezone) {
          const prev = this.userTimezone;
          this.userTimezone = live;
          this.timezoneSource = "detected";
          this.timezoneFallbackUsed = false;
          this.timezoneFallbackReason = null;
          this._timezoneNoticeShown = false;
          this._persistTimezonePreference();
          console.log("[DotStark] timezone updated:", prev, "->", live);
          if (announce && this._msgList) {
            this._appendSystem(
              `Timezone updated to ${this._formatTimezoneDisplayName(live)} — upcoming times will use the new zone.`,
            );
          }
          return true;
        }
      } catch (_) {}
      return false;
    }

    // Latest bookable date (ISO) = today + horizon, for picker clamping.
    _maxDateISO(timezone = null, daysAhead = 60) {
      const base = this._currentDateISO(timezone);
      try {
        const parts = String(base).split("-").map(Number);
        const dt = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
        dt.setUTCDate(dt.getUTCDate() + Math.max(0, Number(daysAhead) || 0));
        const yy = dt.getUTCFullYear();
        const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
        const dd = String(dt.getUTCDate()).padStart(2, "0");
        return `${yy}-${mm}-${dd}`;
      } catch (e) {
        return base;
      }
    }

    _horizonDays() {
      // Leap-aware ceiling mirroring the backend: 366 only when a Feb 29
      // falls inside the next 366 days, otherwise 365.
      const raw = Number(this._agentConfig?.max_booking_days_ahead);
      const cap = (() => {
        const now = new Date();
        const isLeap = (y) => y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
        const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const end = new Date(start.getTime() + 366 * 864e5);
        for (let y = start.getFullYear(); y <= end.getFullYear(); y++) {
          if (!isLeap(y)) continue;
          const feb29 = new Date(y, 1, 29);
          if (feb29 >= start && feb29 <= end) return 366;
        }
        return 365;
      })();
      if (!Number.isFinite(raw)) return 60;
      return Math.min(cap, Math.max(1, Math.round(raw)));
    }

    _buildAvailabilityRequestUrl(dateValue) {
      const params = new URLSearchParams();
      params.set("date", dateValue);
      if (CONFIG.widgetToken) {
        params.set("widget_token", CONFIG.widgetToken);
      }
      if (this.userTimezone) {
        params.set("tz", this.userTimezone);
      }
      if (this.timezoneSource) {
        params.set("timezone_source", this.timezoneSource);
      }
      if (this.selectedUnitId) params.set("unit_id", this.selectedUnitId);
      if (this.selectedProviderId) params.set("provider_id", this.selectedProviderId);
      return `${CONFIG.apiBase}/widget/availability?${params.toString()}`;
    }

    _getCatalogUnits() {
      const snap = this._agentConfig?.catalog;
      if (!snap || !Array.isArray(snap.units)) return [];
      return snap.units.filter((u) => u && u.is_active !== false);
    }

    _getCatalogProviders(unitId) {
      const units = this._getCatalogUnits();
      if (!unitId) {
        const all = [];
        units.forEach((u) => (u.providers || []).forEach((p) => all.push(p)));
        return all.filter((p) => p && p.is_active !== false);
      }
      const u = units.find((x) => String(x.id) === String(unitId));
      return ((u && u.providers) || []).filter((p) => p && p.is_active !== false);
    }

    _maybeShowCatalogPicker() {
      if (this._catalogPickerShown) return;
      const units = this._getCatalogUnits();
      if (!units.length) return;
      if (this.selectedUnitId && this.selectedProviderId) return;
      this._catalogPickerShown = true;
      this._appendUnitPicker(units);
    }

    _appendUnitPicker(units) {
      // Compact dropdown (not a button wall): orgs can have 20+ departments.
      // Department only — the member is chosen later, AFTER the date
      // (engine-driven REQUIRE_PROVIDER_PICKER with members working
      // that date). Never pre-pin a member here.
      // Native <select> in LISTBOX mode (size attr): renders the options as
      // an inline scrollable list inside the card. A collapsed <select>
      // opens an OS-level popup that escapes the widget bounds; the listbox
      // never pops out — same element, same behaviour, always contained.
      this._clearDirectiveCards();
      const div = document.createElement("div");
      div.className = "message agent field-input-card directive-card";
      const label = document.createElement("div");
      label.className = "card-header";
      label.textContent = "🏢 Choose department";
      div.appendChild(label);
      const select = document.createElement("select");
      select.className = "widget-dept-select";
      select.setAttribute("aria-label", "Choose department");
      select.size = Math.max(3, Math.min(6, units.length));
      const hint = document.createElement("option");
      hint.value = "";
      hint.textContent = `Select department (${units.length} available)…`;
      select.appendChild(hint);
      units.forEach((u) => {
        const opt = document.createElement("option");
        opt.value = String(u.id);
        opt.textContent = u.name;
        select.appendChild(opt);
      });
      div.appendChild(select);
      const desc = document.createElement("p");
      desc.className = "field-description";
      desc.textContent = "Pick the department for your visit.";
      div.appendChild(desc);
      select.onchange = () => {
        const picked = units.find((u) => String(u.id) === select.value);
        desc.textContent = (picked && picked.description) || "Pick the department for your visit.";
      };
      const submit = document.createElement("button");
      submit.className = "widget-field-btn";
      submit.textContent = "Continue";
      submit.onclick = () => {
        const picked = units.find((u) => String(u.id) === select.value);
        if (!picked) {
          desc.textContent = "Please select a department first.";
          try { select.focus(); } catch (_) {}
          return;
        }
        submit.disabled = true;
        select.disabled = true;
        this.selectedUnitId = String(picked.id);
        this.selectedProviderId = null;
        this._clearDirectiveCards();
        this._handleSendManual(`I want to book in ${picked.name}`);
      };
      div.appendChild(submit);
      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    _selectedProviderFee() {
      if (!this.selectedProviderId) return null;
      for (const u of this._getCatalogUnits()) {
        for (const p of u.providers || []) {
          if (String(p.id) === String(this.selectedProviderId) && p.fee_display) {
            return p.fee_display;
          }
        }
      }
      return null;
    }

    _appendProviderPicker(providers) {
      // Legacy client-driven picker (dept tap). Kept for reference; the
      // engine now drives member choice via REQUIRE_PROVIDER_PICKER.
      this._renderProviderButtons(providers, (p) => {
        this.selectedProviderId = String(p.id);
        this._appendBubble("user", p.full_name);
        this._clearDirectiveCards();
        const fee = p.fee_display ? ` (Fee: ${p.fee_display})` : "";
        this._appendBubble("agent", `Great — ${p.full_name}${fee}. What date works for you?`);
      });
    }

    _appendEngineProviderPicker(fieldMeta = {}) {
      // Engine-driven member choice (post-date step). Buttons carry member
      // ids; the tap sends the id explicitly so the engine pins it.
      const providers = Array.isArray(fieldMeta?.providers) ? fieldMeta.providers : [];
      if (!providers.length) return;
      this._renderProviderButtons(providers, (p) => {
        // selectedProviderId flows into the dispatch payload (chat.py pins
        // the session); the visible text keeps the transcript readable.
        this.selectedProviderId = String(p.id);
        this._handleSendManual(p.full_name);
      });
    }

    _renderProviderButtons(providers, onPick) {
      this._clearDirectiveCards();
      const div = document.createElement("div");
      div.className = "message agent field-input-card directive-card";
      const label = document.createElement("div");
      label.className = "card-header";
      label.textContent = "👤 Choose team member";
      div.appendChild(label);
      const grid = document.createElement("div");
      grid.className = "select-options-grid";
      providers.forEach((p) => {
        const btn = document.createElement("button");
        btn.className = "select-option-btn";
        const nameSpan = document.createElement("span");
        nameSpan.className = "select-option-name";
        nameSpan.textContent = p.full_name;
        btn.appendChild(nameSpan);
        if (p.fee_display) {
          const feeSpan = document.createElement("span");
          feeSpan.className = "select-option-desc";
          feeSpan.textContent = p.fee_display;
          btn.appendChild(feeSpan);
        }
        if (p.role_label) btn.title = p.role_label;
        btn.onclick = () => {
          grid.querySelectorAll(".select-option-btn").forEach((b) => {
            b.disabled = true;
          });
          btn.classList.add("selected");
          onPick(p);
        };
        grid.appendChild(btn);
      });
      div.appendChild(grid);
      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    _formatTimezoneSource(source) {
      if (source === "user_selected") return "you selected";
      if (source === "fallback_org") return "business fallback";
      return "browser detected";
    }

    _formatTimezoneDisplayName(timezone) {
      if (!timezone) return "UTC";
      const city = timezone.split("/").pop()?.replace(/_/g, " ");
      return city ? `${timezone} (${city})` : timezone;
    }

    _isValidTimezone(tz) {
      if (!tz || typeof tz !== "string") return false;
      try {
        new Intl.DateTimeFormat("en-US", { timeZone: tz }).format(new Date());
        return true;
      } catch (e) {
        return false;
      }
    }

    _formatClockTime(dateValue, timezone = null) {
      const opts = {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      };
      if (timezone && this._isValidTimezone(timezone)) {
        opts.timeZone = timezone;
      }
      try {
        return new Intl.DateTimeFormat([], opts).format(dateValue);
      } catch (e) {
        return dateValue.toLocaleTimeString([], {
          hour: "numeric",
          minute: "2-digit",
          hour12: true,
        });
      }
    }

    _currentDateISO(timezone = null) {
      const now = new Date();
      if (timezone && this._isValidTimezone(timezone)) {
        try {
          const parts = new Intl.DateTimeFormat("en-US", {
            timeZone: timezone,
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
          }).formatToParts(now);
          const year = parts.find((part) => part.type === "year")?.value;
          const month = parts.find((part) => part.type === "month")?.value;
          const day = parts.find((part) => part.type === "day")?.value;
          if (year && month && day) {
            return `${year}-${month}-${day}`;
          }
        } catch (e) {
          // fallback to local date formatting below
        }
      }
      const year = now.getFullYear();
      const month = String(now.getMonth() + 1).padStart(2, "0");
      const day = String(now.getDate()).padStart(2, "0");
      return `${year}-${month}-${day}`;
    }

    _getTimezoneOptions() {
      // Full IANA timezone list at runtime (every zone the browser supports),
      // with a curated fallback for engines without Intl.supportedValuesOf.
      let all = [];
      try {
        const supported = Intl.supportedValuesOf("timeZone");
        if (Array.isArray(supported) && supported.length > 0) {
          all = [...supported].sort();
        }
      } catch (e) {
        // fall back to the curated list below
      }
      if (all.length === 0) {
        all = [
          "UTC", "Africa/Abidjan", "Africa/Cairo", "Africa/Casablanca", "Africa/Johannesburg",
          "Africa/Lagos", "Africa/Nairobi", "America/Argentina/Buenos_Aires", "America/Bogota",
          "America/Caracas", "America/Chicago", "America/Denver", "America/Halifax", "America/Lima",
          "America/Los_Angeles", "America/Mexico_City", "America/New_York", "America/Phoenix",
          "America/Santiago", "America/Sao_Paulo", "America/Toronto", "America/Vancouver",
          "Asia/Almaty", "Asia/Baghdad", "Asia/Bangkok", "Asia/Beirut", "Asia/Dhaka", "Asia/Dubai",
          "Asia/Hong_Kong", "Asia/Istanbul", "Asia/Jakarta", "Asia/Jerusalem", "Asia/Karachi",
          "Asia/Kathmandu", "Asia/Kolkata", "Asia/Kuala_Lumpur", "Asia/Manila", "Asia/Riyadh",
          "Asia/Seoul", "Asia/Shanghai", "Asia/Singapore", "Asia/Taipei", "Asia/Tashkent",
          "Asia/Tehran", "Asia/Tokyo", "Asia/Yangon", "Australia/Adelaide", "Australia/Brisbane",
          "Australia/Melbourne", "Australia/Perth", "Australia/Sydney", "Europe/Amsterdam",
          "Europe/Athens", "Europe/Berlin", "Europe/Brussels", "Europe/Bucharest", "Europe/Budapest",
          "Europe/Copenhagen", "Europe/Dublin", "Europe/Helsinki", "Europe/Lisbon", "Europe/London",
          "Europe/Madrid", "Europe/Moscow", "Europe/Oslo", "Europe/Paris", "Europe/Prague",
          "Europe/Rome", "Europe/Stockholm", "Europe/Vienna", "Europe/Warsaw", "Europe/Zurich",
          "Pacific/Auckland", "Pacific/Fiji", "Pacific/Honolulu",
        ];
      }
      return Array.from(
        new Set(
          [this.userTimezone, this.orgTimezone, this._detectBrowserTimezone(), ...all].filter(Boolean),
        ),
      );
    }

    _buildTimezoneContextBanner(selectedDate = null) {
      const wrap = document.createElement("div");
      wrap.className = "timezone-context-banner";

      const summary = document.createElement("div");
      summary.className = "timezone-context-text";
      summary.textContent = `Times shown in ${this._formatTimezoneDisplayName(this.userTimezone)} (${this._formatTimezoneSource(this.timezoneSource)})`;
      wrap.appendChild(summary);

      const selectorRow = document.createElement("div");
      selectorRow.className = "timezone-override-row";

      const selectorLabel = document.createElement("label");
      selectorLabel.className = "timezone-override-label";
      selectorLabel.textContent = "Timezone";
      selectorRow.appendChild(selectorLabel);

      const selector = document.createElement("select");
      selector.className = "timezone-override-select";
      this._getTimezoneOptions().forEach((tz) => {
        const option = document.createElement("option");
        option.value = tz;
        option.textContent = tz;
        if (tz === this.userTimezone) option.selected = true;
        selector.appendChild(option);
      });

      selector.onchange = async () => {
        const nextTz = selector.value;
        if (!nextTz || nextTz === this.userTimezone) return;
        this.selectedSlotId = null;
        if (this._isValidTimezone(nextTz)) {
          this.userTimezone = nextTz;
          this.timezoneSource = "user_selected";
          this.timezoneFallbackUsed = false;
          this.timezoneFallbackReason = null;
        } else {
          this.userTimezone = this.orgTimezone || this.userTimezone;
          this.timezoneSource = "fallback_org";
          this.timezoneFallbackUsed = true;
          this.timezoneFallbackReason = "invalid_tz";
        }
        this._persistTimezonePreference();
        this._showTimezoneFallbackNoticeIfNeeded();

        if (selectedDate) {
          await this._appendTimeSlotGrid({
            icon: "⏰",
            label: "Select a Time Slot",
            options: [],
          });
        }
      };

      selectorRow.appendChild(selector);
      wrap.appendChild(selectorRow);
      return wrap;
    }

    _normalizeAvailabilitySlots(slots) {
      if (!Array.isArray(slots)) return [];
      return slots
        .filter((slot) => slot && slot.slot_id && slot.label_user_tz)
        .map((slot) => {
          const userDate = slot.start_user_tz ? new Date(slot.start_user_tz) : null;
          const orgDate = slot.start_org_tz ? new Date(slot.start_org_tz) : null;
          return {
            id: slot.slot_id,
            label:
              orgDate
                ? this._formatClockTime(orgDate, this.orgTimezone)
                : slot.label_user_tz,
            userLabel: userDate
              ? this._formatClockTime(userDate, this.userTimezone)
              : null,
            sortKey: userDate ? userDate.getTime() : Number.MAX_SAFE_INTEGER,
            available: slot.available !== false,
            reason: slot.reason || null,
            raw: slot,
          };
        })
        .sort((a, b) => a.sortKey - b.sortKey);
    }

    _availabilityStatusMessage(status) {
      if (status === "closed_day" || status === "exception_closed") {
        return "No availability on this date (closed day/holiday). Please choose another date.";
      }
      if (status === "fully_booked") {
        return "This date is fully booked. Please choose another date.";
      }
      if (status === "no_schedule" || status === "no_slots_in_window") {
        return "No configured availability for this date yet. Please choose another date.";
      }
      return "No available time slots for this date. Please try another date.";
    }

    _availabilityRecoveryMessage(status, fallbackReason = null) {
      const statusMessage = this._availabilityStatusMessage(status);
      const reason = fallbackReason || this.timezoneFallbackReason;
      if (!reason) return statusMessage;
      const orgDisplay = this._formatTimezoneDisplayName(
        this.orgTimezone || this.userTimezone,
      );
      if (reason === "invalid_tz") {
        return `${statusMessage} We are using ${orgDisplay} while you pick another date.`;
      }
      if (reason === "missing_tz") {
        return `${statusMessage} We could not detect your timezone, so we are using ${orgDisplay} while you pick another date.`;
      }
      return `${statusMessage} Please pick another date.`;
    }

    _showTimezoneFallbackNoticeIfNeeded() {
      if (!this.timezoneFallbackUsed || this._timezoneNoticeShown) return;
      this._timezoneNoticeShown = true;
      this._appendSystem(
        "We couldn't detect your timezone, so times are shown in the business timezone for now.",
      );
    }

    /* ─────────── TOGGLE ─────────── */
    _toggle() {
      this.isOpen = !this.isOpen;
      this._launcher.classList.toggle("open", this.isOpen);
      this._launcher.setAttribute("aria-expanded", String(this.isOpen));
      this._launcher.setAttribute(
        "aria-label",
        this.isOpen ? "Close chat" : "Open booking chat",
      );
      this._chatBox.setAttribute("aria-hidden", String(!this.isOpen));

      if (this.isOpen) {
        this._chatBox.classList.add("visible");
        this._chatBox.classList.remove("hiding");
        setTimeout(() => {
          try { this._input.focus(); } catch (_) {}
          try { if (typeof this._applyHeaderMarquee === "function") this._applyHeaderMarquee(); } catch (_) {}
        }, 200);
      } else {
        this._chatBox.classList.add("hiding");
        setTimeout(() => {
          this._chatBox.classList.remove("visible", "hiding");
        }, 250);
      }
    }

    /* ─────────── SEND ─────────── */
    async _handleSend() {
      const text = this._input.value.trim();
      if (!text || this.isLoading) return;
      // Lock immediately so a rapid second click is ignored.
      this.isLoading = true;
      // First typed message locks the mode in: no going back to home after.
      this._canGoHome = false;
      try { this._updateBackBtn(); } catch (_) {}
      this._clearSkipAction();

      // Send animation
      this._sendBtn.classList.add("sending");
      setTimeout(() => this._sendBtn.classList.remove("sending"), 400);

      this._appendBubble("user", text);
      this._input.value = "";
      this._input.disabled = true;
      this._sendBtn.disabled = true;
      this.isLoading = true;
      this._pendingRefreshSkips = [{ role: "user", content: text }];

      const thinkingEl = this._appendTyping();

      // Re-resolve the browser zone every turn: a VPN/network change
      // mid-session must move the booking timezone with it. The backend
      // stores whatever we send here, so a stale value would stick forever.
      this._refreshTimezone();

      try {
        const payload = { 
            message: text,
            // Client-generated message ID for server-side deduplication
            message_id: `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
        };
        if (this.selectedUnitId) payload.unit_id = this.selectedUnitId;
        if (this.selectedProviderId) payload.provider_id = this.selectedProviderId;
        if (this.sessionId) payload.session_id = this.sessionId;
        // Conversation mode (ask | booking) pinned server-side per session.
        if (this._chatMode === "ask" || this._chatMode === "booking") {
          payload.mode = this._chatMode;
        }
        // Section token: if the visitor switches sections mid-flight, the
        // late reply belongs to the abandoned section and must never render
        // into the new section's view (checked after the fetch resolves).
        const _sendSession = this.sessionId || null;
        const _sendMode = this._chatMode || null;
        try { this._sendAbort?.abort?.(); } catch (_) {}
        const _aborter = new AbortController();
        this._sendAbort = _aborter;
        if (!this.sessionId) {
          this.selectedSlotId = null;
          this.timezoneFallbackUsed = false;
          this.timezoneFallbackReason = null;
          this._timezoneNoticeShown = false;
        }
        if (this.selectedSlotId) payload.selected_slot_id = this.selectedSlotId;
        if (this.userTimezone) payload.user_timezone = this.userTimezone;
        if (this.timezoneSource) payload.timezone_source = this.timezoneSource;
        if (this.timezoneFallbackUsed) {
          payload.timezone_fallback_used = true;
          if (this.timezoneFallbackReason) {
            payload.timezone_fallback_reason = this.timezoneFallbackReason;
          }
        }

        const response = await fetch(
          `${CONFIG.apiBase}/chat/message/dispatch`,
          {
            method: "POST",
            signal: _aborter.signal,
            headers: {
              "Content-Type": "application/json",
              "X-Widget-Token": CONFIG.widgetToken || "",
            },
            body: JSON.stringify(payload),
          },
        );

        if (!response.ok) {
          const errData = await response.json().catch(() => ({}));
          throw new Error(errData.detail || `Server error ${response.status}`);
        }

        const data = await response.json();
        // Section switched mid-flight → drop this reply entirely (no
        // bubbles, no directive cards). The finally block below still
        // resets loading state for the new section's view.
        if (this._chatMode !== _sendMode || (_sendSession && this.sessionId !== _sendSession)) {
          return;
        }
        if (data.session_id) {
          this.sessionId = data.session_id;
          try {
            sessionStorage.setItem(this._sessionStorageKey(), data.session_id);
            if (this._chatMode === "ask" || this._chatMode === "booking") {
              // Keep the session under its section's own key too, so the
              // other section can switch away and back without losing it.
              sessionStorage.setItem(this._sessionKeyFor(this._chatMode), data.session_id);
            }
            // Cleanup legacy global key to prevent stale cross-widget reuse.
            sessionStorage.removeItem("dotstark_session_id");
          } catch (e) {}
          // Fresh sessions (created on the first message) never had a channel:
          // open the WebSocket and start polling now so messages sent by a
          // human agent after takeover arrive in real time.
          this._connectWebSocket();
          this._startPolling();
        }

        const agentMessage = this._resolveAssistantText(
          data.action?.message || data.agent_response,
        );
        const directive = data.action?.widget_directive;
        this._lastDirective = directive || null;
        const rendersCardOnly =
          directive === "REQUEST_CONFIRMATION" ||
          directive === "BOOKING_SUMMARY_CARD" ||
          directive === "BOOKING_CONFIRMED_CARD";

        thinkingEl.remove();

        if (!rendersCardOnly) {
          if (agentMessage) {
            this._appendBubble("agent", agentMessage);
            this._pendingRefreshSkips.push({ role: "agent", content: agentMessage });
          } else if (directive !== "HUMAN_ACTIVE") {
            // HUMAN_ACTIVE legitimately carries an empty message (the text
            // was accepted for the human agent) — never show the error bubble.
            this._appendBubble(
              "agent",
              "I received your message, but something went wrong with the reply.",
            );
          }
        }

        const fieldMeta = data.action?.field_meta;
        this._activeFieldMeta = fieldMeta || null;
        this._setInlineValidation(fieldMeta?.validation_error || "");

        await this._renderDirectiveUI(directive, fieldMeta, agentMessage);
      } catch (err) {
        // Aborted by a section switch — stay silent. The new section owns
        // the view now; the finally block still resets loading state.
        if (err && err.name === "AbortError") {
          return;
        }
        console.error("Widget Error:", err);
        thinkingEl.remove();
        this._appendBubble(
          "agent",
          `Couldn't reach the server. Make sure the backend is running on ${CONFIG.apiBase}`,
        );
      } finally {
        this.isLoading = false;
        this._input.disabled = false;
        this._sendBtn.disabled = false;
        // Composer follows the mode: hidden in guided booking (cards carry
        // their own inputs), visible otherwise. Human chat always types.
        try { this._applyComposerRule(); } catch (_) {}
        try {
          if (this._inputArea && this._inputArea.style.display !== "none") {
            this._input.focus();
          }
        } catch (_) {}
        // Refresh incrementally after every send so messages written by a
        // human agent (takeover) show up even if WS/poll hadn't started yet.
        // Dedupe by message id prevents re-rendering our own just-sent bubble.
        if (this.sessionId) {
          this._loadHistory(this._lastMessageTimestamp);
        }
      }
    }

    /* ─────────── DOM HELPERS ─────────── */
  _appendBubble(sender, text) {
      const div = document.createElement("div");
      div.className = `message ${sender}`;
      div.setAttribute("role", "log");

      // Parse simple markdown for agent messages (safe DOM — no innerHTML)
      if (sender === "agent") {
        this._renderMarkdown(div, text);
      } else {
        div.textContent = text;
      }

      // Timestamp
      const ts = document.createElement("span");
      ts.className = "msg-time";
      const now = new Date();
      ts.textContent = now.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
      div.appendChild(ts);

      this._msgList.appendChild(div);
      this._scrollToBottom();

      // Screen reader announcement
      this._announceToSR(sender === "agent" ? "Agent: " + text : "");
      return div;
    }

    /* Phase 6/9: distinct bubble for human agent messages */
    _appendHumanBubble(event) {
      const div = document.createElement("div");
      div.className = "message human-agent";
      div.setAttribute("role", "log");

      const label = document.createElement("div");
      label.className = "human-label";
      label.textContent = event.admin_name || "Support agent";
      div.appendChild(label);

      const text = document.createElement("div");
      text.className = "human-text";
      text.textContent = event.content || "";
      div.appendChild(text);

      const ts = document.createElement("span");
      ts.className = "msg-time";
      try {
        const dt = event.created_at ? new Date(event.created_at) : new Date();
        ts.textContent = dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch {
        ts.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      }
      div.appendChild(ts);

      this._msgList.appendChild(div);
      this._scrollToBottom();
      this._announceToSR(`Support agent: ${event.content || ""}`);
      return div;
    }

    _renderMarkdown(container, text) {
      // Split by newlines, then parse inline markdown
      const lines = text.split("\n");
      lines.forEach((line, i) => {
        if (i > 0) container.appendChild(document.createElement("br"));
        const trimmed = line.trim();
        if (!trimmed) return;

        // Bullet list item
        if (/^[-•]\s/.test(trimmed)) {
          const li = document.createElement("span");
          li.className = "md-bullet";
          this._parseInline(li, trimmed.replace(/^[-•]\s*/, ""));
          container.appendChild(li);
          return;
        }

        this._parseInline(container, trimmed);
      });
    }

    _parseInline(container, text) {
      // Parse **bold**, *italic*, and ✏️ markers safely via DOM
      const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
      parts.forEach((part) => {
        if (/^\*\*(.+)\*\*$/.test(part)) {
          const strong = document.createElement("strong");
          strong.textContent = part.slice(2, -2);
          container.appendChild(strong);
        } else if (/^\*(.+)\*$/.test(part)) {
          const em = document.createElement("em");
          em.textContent = part.slice(1, -1);
          container.appendChild(em);
        } else if (part) {
          container.appendChild(document.createTextNode(part));
        }
      });
    }

    _announceToSR(text) {
      if (!text) return;
      let region = this.shadowRoot.getElementById("sr-live");
      if (!region) {
        region = document.createElement("div");
        region.id = "sr-live";
        region.setAttribute("role", "status");
        region.setAttribute("aria-live", "polite");
        region.className = "sr-only";
        this.shadowRoot.appendChild(region);
      }
      region.textContent = text;
    }

    _appendTyping() {
      const div = document.createElement("div");
      div.className = "message agent typing-msg";
      div.innerHTML = `
                <div class="typing-dots">
                    <span></span><span></span><span></span>
                </div>
            `;
      this._msgList.appendChild(div);
      this._scrollToBottom();
      return div;
    }

    _appendSystem(text) {
      const div = document.createElement("div");
      div.className = "message system";
      const span = document.createElement("span");
      span.textContent = text;
      div.appendChild(span);
      this._msgList.appendChild(div);
      this._scrollToBottom();
      return div;
    }

    _updateInputMode(type, placeholder) {
      if (this._input) {
        this._input.type = type || "text";
        this._input.placeholder = placeholder || "Type a message...";
      }
    }

    _setInlineValidation(message) {
      if (!this._inlineValidation) return;
      this._inlineValidation.textContent = message || "";
      this._inlineValidation.style.display = message ? "block" : "none";
    }

    _clearDirectiveCards() {
      this._msgList
        .querySelectorAll(".directive-card")
        .forEach((node) => node.remove());
    }

    _clearSkipAction() {
      if (!this._sendBtn) return;
      const existing = this.shadowRoot.getElementById("widget-skip-btn");
      if (existing) existing.remove();
    }

    _renderSkipAction(fieldMeta) {
      this._clearSkipAction();
      if (!fieldMeta || fieldMeta.required !== false) return;
      const skipBtn = document.createElement("button");
      skipBtn.id = "widget-skip-btn";
      skipBtn.className = "widget-skip-btn";
      skipBtn.textContent = "Skip";
      skipBtn.onclick = () => {
        skipBtn.disabled = true;
        this._handleSendManual(this._skipToken);
      };
      this._sendBtn.parentElement?.appendChild(skipBtn);
    }

    _defaultCountryCode() {
      try {
        const locale = Intl.DateTimeFormat().resolvedOptions().locale || "";
        const country = locale.split("-").pop()?.toUpperCase();
        if (country && country.length === 2) return country;
      } catch (e) {}
      const tz = this.userTimezone || "";
      if (tz.includes("London")) return "GB";
      if (tz.includes("Kolkata")) return "IN";
      if (tz.includes("Sydney")) return "AU";
      return "US";
    }

    _appendPhoneInput(fieldMeta) {
      this._clearDirectiveCards();
      this._updateInputMode("text", "Type a message...");
      this._clearSkipAction();
      const fieldName = fieldMeta?.name || "phone";
      const previous = this._directiveDrafts[fieldName] || {};
      const countryCode =
        (fieldMeta?.country_code || previous.countryCode || this._defaultCountryCode()).toUpperCase();
      const localNumber = fieldMeta?.raw_input || previous.localNumber || "";

      const countries = [
        { code: "US", dial: "+1", example: "(555) 000-0000" },
        { code: "GB", dial: "+44", example: "7700 900000" },
        { code: "IN", dial: "+91", example: "98765 43210" },
        { code: "AU", dial: "+61", example: "400 000 000" },
        { code: "CA", dial: "+1", example: "(555) 000-0000" },
      ];
      const exampleFor = (code) =>
        (countries.find((entry) => entry.code === code)?.example) || "98765 43210";

      const div = document.createElement("div");
      div.className = "message agent field-input-card directive-card";

      const label = document.createElement("div");
      label.className = "card-header";
      label.textContent = `${fieldMeta?.icon || "📞"} ${fieldMeta?.label || "Phone Number"}`;
      div.appendChild(label);

      const row = document.createElement("div");
      row.className = "widget-phone-row";

      const countrySelect = document.createElement("select");
      countrySelect.className = "widget-phone-country";
      countries.forEach((entry) => {
        const opt = document.createElement("option");
        opt.value = entry.code;
        opt.textContent = `${entry.code} (${entry.dial})`;
        if (entry.code === countryCode) opt.selected = true;
        countrySelect.appendChild(opt);
      });

      const localInput = document.createElement("input");
      localInput.type = "tel";
      localInput.className = "widget-phone-local";
      // Per-country example beats any stale backend placeholder (e.g. a US
      // example while IN is selected). Updates live on country change.
      localInput.placeholder = exampleFor(countryCode);
      localInput.value = localNumber;
      countrySelect.onchange = () => {
        localInput.placeholder = exampleFor(countrySelect.value);
      };

      row.appendChild(countrySelect);
      row.appendChild(localInput);
      div.appendChild(row);

      if (fieldMeta?.validation_error) {
        const error = document.createElement("div");
        error.className = "field-inline-error";
        error.textContent = fieldMeta.validation_error;
        div.appendChild(error);
      }

      const actions = document.createElement("div");
      actions.className = "widget-field-actions";

      const submitBtn = document.createElement("button");
      submitBtn.className = "widget-field-btn";
      submitBtn.textContent = "Submit Phone";
      submitBtn.onclick = () => {
        const selectedCode = countrySelect.value || this._defaultCountryCode();
        const dial = countries.find((entry) => entry.code === selectedCode)?.dial || "+1";
        const normalized = `${dial} ${localInput.value.trim()}`.trim();
        this._directiveDrafts[fieldName] = {
          countryCode: selectedCode,
          localNumber: localInput.value.trim(),
        };
        submitBtn.disabled = true;
        localInput.disabled = true;
        countrySelect.disabled = true;
        this._handleSendManual(normalized);
      };
      actions.appendChild(submitBtn);

      if (fieldMeta?.required === false) {
        const skipBtn = document.createElement("button");
        skipBtn.className = "widget-field-btn widget-secondary-btn";
        skipBtn.textContent = "Skip";
        skipBtn.onclick = () => {
          skipBtn.disabled = true;
          submitBtn.disabled = true;
          this._handleSendManual(this._skipToken);
        };
        actions.appendChild(skipBtn);
      }

      div.appendChild(actions);
      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    _appendDatePicker(fieldMeta) {
      this._clearDirectiveCards();
      this._clearSkipAction();
      const div = document.createElement("div");
      div.className = "message agent field-input-card directive-card";

      const label = document.createElement("div");
      label.className = "card-header";
      label.textContent = `${fieldMeta?.icon || "📅"} ${fieldMeta?.label || "Select a Date"}`;
      div.appendChild(label);
      div.appendChild(this._buildTimezoneContextBanner(null));

      const input = document.createElement("input");
      input.type = "date";
      input.className = "widget-date-input";
      input.min = this._currentDateISO(this.userTimezone);
      // Clamp the picker to the booking horizon — beyond this the backend
      // rejects the date, so don't offer it. Closed weekdays get a hint
      // (native date inputs can't disable individual days).
      input.max = this._maxDateISO(this.userTimezone, this._horizonDays());
      if (this.selectedDate) {
        input.value = this.selectedDate;
      } else {
        input.value = input.min;
      }
      div.appendChild(input);
      {
        const closedDays = Array.isArray(this._agentConfig?.closed_weekdays)
          ? this._agentConfig.closed_weekdays
          : [];
        const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
        const closedNames = closedDays
          .map((d) => dayNames[d] || "")
          .filter(Boolean)
          .join(", ");
        const hint = document.createElement("p");
        hint.className = "field-description";
        hint.textContent =
          `Book within ${this._horizonDays()} days ahead` +
          (closedNames ? ` · Closed: ${closedNames}` : "") +
          ".";
        div.appendChild(hint);
      }

      const btn = document.createElement("button");
      btn.className = "widget-field-btn";
      btn.textContent = "Confirm Date";
      btn.onclick = () => {
        if (!input.value) return;
        btn.disabled = true;
        input.disabled = true;
        this.selectedSlotId = null;
        this.selectedDate = input.value;
        this._handleSendManual(input.value);
      };
      div.appendChild(btn);
      if (fieldMeta?.required === false) {
        const skipBtn = document.createElement("button");
        skipBtn.className = "widget-field-btn widget-secondary-btn";
        skipBtn.textContent = "Skip";
        skipBtn.onclick = () => this._handleSendManual(this._skipToken);
        div.appendChild(skipBtn);
      }

      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    _appendSelectInput(fieldMeta) {
      this._clearDirectiveCards();
      this._clearSkipAction();
      if (!fieldMeta || !fieldMeta.options || fieldMeta.options.length === 0) {
        this._appendSystem(
          `Please type your choice for: ${fieldMeta?.label || "selection"}`,
        );
        return;
      }

      const div = document.createElement("div");
      div.className = "message agent field-input-card directive-card";

      const label = document.createElement("div");
      label.className = "card-header";
      label.textContent = `${fieldMeta.icon || "📋"} ${fieldMeta.label || "Choose an option"}`;
      div.appendChild(label);

      if (fieldMeta.description) {
        const desc = document.createElement("p");
        desc.className = "field-description";
        desc.textContent = fieldMeta.description;
        div.appendChild(desc);
      }

      const optionsContainer = document.createElement("div");
      optionsContainer.className = "select-options-grid";

      fieldMeta.options.forEach((opt) => {
        const btn = document.createElement("button");
        btn.className = "select-option-btn";
        btn.textContent = opt;
        btn.onclick = () => {
          optionsContainer
            .querySelectorAll(".select-option-btn")
            .forEach((b) => {
              b.disabled = true;
              b.classList.remove("selected");
            });
          btn.classList.add("selected");
          this._handleSendManual(opt);
        };
        optionsContainer.appendChild(btn);
      });

      div.appendChild(optionsContainer);
      if (fieldMeta?.required === false) {
        const skipBtn = document.createElement("button");
        skipBtn.className = "widget-field-btn widget-secondary-btn";
        skipBtn.textContent = "Skip";
        skipBtn.onclick = () => this._handleSendManual(this._skipToken);
        div.appendChild(skipBtn);
      }
      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    _quickPromptsFor(location) {
      // Quick prompts (suggested questions) from /widget/config.
      // location: "start" (fresh greeting) or "followup" (after replies).
      const all = this._agentConfig && Array.isArray(this._agentConfig.quick_prompts)
        ? this._agentConfig.quick_prompts
        : [];
      return all.filter((p) => {
        if (!p || typeof p.title !== "string" || !p.title.trim()) return false;
        if (!p.content && !p.link) return false;
        const at = (p.show_at || "both").toLowerCase();
        return location === "start"
          ? (at === "start" || at === "both")
          : (at === "followup" || at === "both");
      });
    }

    _fillHomeView() {
      // Brand the home slide from live agent config (name + tagline + logo).
      try {
        const cfg = this._agentConfig || {};
        const title = this.shadowRoot.getElementById("home-title");
        const tag = this.shadowRoot.getElementById("home-tagline");
        const av = this.shadowRoot.getElementById("home-avatar");
        if (title) title.textContent = cfg.agent_name || "Booking Assistant";
        if (tag) {
          tag.textContent =
            [cfg.business_name, cfg.business_tagline].filter(Boolean).join(" · ") ||
            "How can we help you today?";
        }
        if (av) {
          if (this._logoSrc) {
            av.innerHTML = "";
            const img = document.createElement("img");
            img.src = this._logoSrc;
            img.alt = "logo";
            img.onerror = () => {
              try {
                av.textContent = String(cfg.agent_name || "B").trim()[0] || "B";
              } catch (_) {}
            };
            av.appendChild(img);
          } else {
            const name = String(cfg.agent_name || "B");
            av.textContent = (name.trim()[0] || "B").toUpperCase();
          }
        }
      } catch (_) {}
    }

    _applyComposerRule() {
      // Single source of truth for composer visibility.
      if (this._humanActive) {
        this._setComposerVisible(true);
        return;
      }
      if (this._chatMode === "booking") {
        this._setComposerVisible(false);
        return;
      }
      this._setComposerVisible(true);
    }

    _setSessionContentVisible(visible) {
      // Show/hide the conversation chrome under the home slide.
      try {
        if (visible) {
          if (this._msgList) this._msgList.style.display = "";
          if (this._inputArea) this._inputArea.style.display = "";
          if (this._inlineValidation) this._inlineValidation.style.display = "";
        } else {
          if (this._msgList) this._msgList.style.display = "none";
          if (this._qpBar) this._qpBar.style.display = "none";
          if (this._inputArea) this._inputArea.style.display = "none";
          if (this._inlineValidation) this._inlineValidation.style.display = "none";
        }
      } catch (_) {}
    }

    _updateBackBtn() {
      // Back is only meaningful pre-first-message (mode can still change).
      try {
        if (!this._backBtn) return;
        const homeShown =
          !!this._homeView && this._homeView.style.display !== "none";
        this._backBtn.style.display =
          this._canGoHome && !homeShown ? "flex" : "none";
      } catch (_) {}
    }

    _showHomeView() {
      try {
        if (!this._homeView) return;
        this._fillHomeView();
        this._setSessionContentVisible(false);
        this._homeView.classList.remove("slide-out");
        // Force reflow so the animation replays on repeat visits.
        this._homeView.style.display = "flex";
        void this._homeView.offsetWidth;
        this._homeView.classList.add("slide-in");
      } catch (_) {}
      try { this._updateBackBtn(); } catch (_) {}
    }

    _showSessionView() {
      // Slide home away, reveal the conversation + composer per mode.
      try {
        if (!this._homeView) return;
        this._setSessionContentVisible(true);
        try { this._applyComposerRule(); } catch (_) {}
        if (this._homeView.style.display === "none") {
          try { this._updateBackBtn(); } catch (_) {}
          return;
        }
        this._homeView.classList.remove("slide-in");
        this._homeView.classList.add("slide-out");
        window.setTimeout(() => {
          try { this._homeView.style.display = "none"; } catch (_) {}
        }, 280);
      } catch (_) {}
      try { this._updateBackBtn(); } catch (_) {}
    }

    _renderModeExit() {
      // Single switch pill for guided-booking mode (admin prompts hidden).
      if (this._humanActive || !this._qpBar) return;
      this._clearQuickPrompts();
      const btn = document.createElement("button");
      btn.className = "qp-pill qp-mode";
      btn.textContent = "💬 Ask AI instead";
      btn.setAttribute("aria-label", "Switch to Ask AI");
      btn.onclick = () => this._switchSection("ask");
      this._qpBar.appendChild(btn);
      this._qpBar.style.display = "flex";
      try { this._refreshQpNav(); } catch (_) {}
    }

    _switchSection(mode) {
      // Going back to the active mode just reveals the session underneath.
      if (this._chatMode === mode && this.sessionId) {
        try { this._showSessionView(); } catch (_) {}
        return;
      }
      // Section switch KEEPS each section's session: the current one is
      // saved under its own mode key and the target section re-joins the
      // session it already had. Booking details the visitor shared before
      // switching to Ask AI therefore survive the round trip — the backend
      // session still holds them and the flow resumes from the next
      // pending field. A section that never had a session starts fresh,
      // exactly as before.
      let _storedSession = null;
      try {
        if ((this._chatMode === "ask" || this._chatMode === "booking") && this.sessionId) {
          sessionStorage.setItem(this._sessionKeyFor(this._chatMode), this.sessionId);
        }
        _storedSession = sessionStorage.getItem(this._sessionKeyFor(mode));
        this.sessionId = _storedSession || null;
        this._lastDirective = null;
        this._msgList.querySelectorAll(".message").forEach((m) => m.remove());
        try { this._renderedMessageIds = new Set(); } catch (_) {}
        this._pendingRefreshSkips = [];
        this._lastMessageTimestamp = null;
        try { this._clearQuickPrompts(); } catch (_) {}
        this.selectedSlotId = null;
        this.selectedDate = null;
        this.selectedUnitId = null;
        this.selectedProviderId = null;
        this._catalogPickerShown = false;
        this._humanActive = false;
        this._humanJoinedShownFor = null;
        // Rebind live updates to the (possibly different) session. Without
        // this the old socket kept listening on the abandoned session.
        try {
          if (this._ws) { this._ws.onclose = null; this._ws.close(); }
        } catch (_) {}
        this._ws = null;
        this._wsConnected = false;
        // Abort any in-flight send: its late reply belongs to the abandoned
        // section (the send handler also drops it via its section token —
        // belt and braces).
        try { this._sendAbort?.abort?.(); } catch (_) {}
        this._sendAbort = null;
      } catch (_) {}
      // silent when re-joining: no greeting, no auto booking kick, no
      // composer lock — the rejoin block below restores the transcript.
      this._setMode(mode, { silent: !!_storedSession });
      if (_storedSession) {
        (async () => {
          try { await this._loadHistory(); } catch (_) {}
          try { this._connectWebSocket(); } catch (_) {}
          try { this._startPolling(); } catch (_) {}
          if (mode === "booking") {
            // Guided booking stays fully card-driven: restore the
            // transcript, then re-render the PENDING step as its directive
            // card (name/email/phone/date/slot/review) — never free text.
            try {
              this._appendSystem("Welcome back! Your booking details so far are saved — continue below.");
            } catch (_) {}
            await this._resumePendingCard();
          } else {
            try {
              this._appendBubble("agent", "Welcome back! What would you like to ask?");
            } catch (_) {}
          }
        })();
      }
    }

    _setMode(mode, opts = {}) {
      // Re-picking the active mode (e.g. via home): just reveal the session,
      // never re-trigger booking or re-announce.
      if (this._chatMode === mode && this.sessionId) {
        try { this._showSessionView(); } catch (_) {}
        return;
      }
      this._chatMode = mode;
      this._saveChatMode();
      this._clearQuickPrompts();
      try { this._showSessionView(); } catch (_) {}
      if (mode === "ask") {
        // Ask AI types: composer appears immediately for free queries.
        try {
          this._setComposerVisible(true);
          this._input.disabled = false;
          this._sendBtn.disabled = false;
          this._input.placeholder = "Type a message...";
          this._input.style.opacity = "1";
        } catch (_) {}
        if (!opts.silent) {
          // Ask-AI greeting: Q&A-flavored (this section answers questions;
          // booking/cancel/reschedule redirect to the button). Never the
          // booking greeting from admin config.
          const greeting =
            this._agentConfig?.ask_greeting_message ||
            "Hello! 👋 I'm here to answer your questions. What would you like to know?";
          this._appendBubble("agent", greeting);
        }
        // Admin's start quick-prompts live here (entry shows only the two
        // mode buttons). Follow-ups after replies use the followup set.
        // The booking switch pill is appended inside _renderQuickPrompts
        // for ask mode, so it survives every re-render.
        try { this._renderQuickPrompts("start"); } catch (_) {}
      } else if (mode === "booking") {
        if (!opts.silent) {
          // Lock free text immediately (card steps stay locked; the reply
          // hook re-opens field-value steps). Exit pill shows right away.
          try {
            this._input.disabled = true;
            this._sendBtn.disabled = true;
            this._input.placeholder = "Please choose an option above ☝️";
            this._input.style.opacity = "0.5";
          } catch (_) {}
          try { this._renderModeExit(); } catch (_) {}
          // Department picker FIRST when the org has one (pure directive
          // UI); otherwise kick the booking intent directly.
          let _deptShown = false;
          try {
            if (this._getCatalogUnits().length > 0) {
              this._maybeShowCatalogPicker();
              _deptShown = this._catalogPickerShown === true;
            }
          } catch (_) {}
          if (!_deptShown) {
            this._handleSendManual("I want to book an appointment");
          }
        }
        // Exit pill renders after the booking reply arrives (post-directive hook).
      }
    }

    _clearQuickPrompts() {
      try {
        if (this._qpBar) {
          this._qpBar.innerHTML = "";
          this._qpBar.style.display = "none";
        }
        // The pinned switch group lives outside the bar, so it needs its own
        // reset — otherwise a stale "Start Booking" pill survives every
        // re-render and the de-dup guard blocks the fresh one.
        const slot = this.shadowRoot && this.shadowRoot.getElementById("qp-switch-slot");
        if (slot) slot.innerHTML = "";
        const wrap = this._qpWrap || (this.shadowRoot && this.shadowRoot.getElementById("qp-bar-wrap"));
        if (wrap) wrap.classList.remove("qp-has-switch");
        this._msgList.querySelectorAll(".quick-prompts").forEach((el) => el.remove());
      } catch (_) {}
      try { this._refreshQpNav(); } catch (_) {}
    }

    _bindQpNav() {
      // One-time wiring for the suggestions-bar scroll controls: arrow
      // buttons + mouse-wheel-to-horizontal. Laptop users have no touch
      // swipe and the bar hides its scrollbar, so without this the
      // overflow pills are unreachable on desktop.
      if (this._qpNavBound) return;
      this._qpNavBound = true;
      try {
        const wrap = this.shadowRoot.getElementById("qp-bar-wrap");
        const bar = this._qpBar;
        const left = this.shadowRoot.getElementById("qp-nav-left");
        const right = this.shadowRoot.getElementById("qp-nav-right");
        if (!wrap || !bar || !left || !right) return;
        this._qpWrap = wrap;
        left.addEventListener("click", () => { try { this._qpScrollOne(-1); } catch (_) {} });
        right.addEventListener("click", () => { try { this._qpScrollOne(1); } catch (_) {} });
        bar.addEventListener("scroll", () => { try { this._refreshQpNav(); } catch (_) {} }, { passive: true });
        bar.addEventListener("wheel", (e) => {
          // Quantized: one wheel notch = exactly one pill. A short lockout
          // swallows the burst of events a single flick produces so fast
          // scrolling can never jump multiple pills per action.
          try {
            const max = bar.scrollWidth - bar.clientWidth;
            if (max <= 2) return; // no overflow: leave page scroll alone
            const dx = (e.deltaX || 0) + (e.deltaY || 0);
            if (!dx) return;
            const now = Date.now();
            if (now - (this._qpWheelLock || 0) < 350) { e.preventDefault(); return; }
            if ((dx > 0 && bar.scrollLeft < max - 1) || (dx < 0 && bar.scrollLeft > 1)) {
              e.preventDefault();
              this._qpWheelLock = now;
              this._qpScrollOne(dx > 0 ? 1 : -1);
            }
          } catch (_) {}
        }, { passive: false });
        window.addEventListener("resize", () => { try { this._refreshQpNav(); } catch (_) {} });
      } catch (_) {}
    }

    _refreshQpNav() {
      // Show each arrow only while there is content to scroll that way.
      // Called after every bar paint, on scroll, and on window resize.
      // Both arrows are static siblings of the scroll area, so showing one
      // can never cover a pill.
      try {
        const wrap = this._qpWrap || (this.shadowRoot && this.shadowRoot.getElementById("qp-bar-wrap"));
        const bar = this._qpBar;
        if (!wrap || !bar) return;
        const visible = bar.style.display !== "none" && bar.children.length > 0;
        const max = bar.scrollWidth - bar.clientWidth;
        const canLeft = visible && bar.scrollLeft > 4;
        const canRight = visible && max > 4 && bar.scrollLeft < max - 4;
        wrap.classList.toggle("qp-can-left", !!canLeft);
        wrap.classList.toggle("qp-can-right", !!canRight);
      } catch (_) {}
    }

    _qpSuggestPills() {
      // Suggestion pills only — the switch control lives outside the bar now,
      // so this is simply every pill in the scroll area.
      try {
        const bar = this._qpBar;
        if (!bar) return [];
        return Array.from(bar.children).filter(
          (el) => el.classList && el.classList.contains("qp-pill") && !el.classList.contains("qp-mode")
        );
      } catch (_) { return []; }
    }

    _qpScrollOne(dir) {
      // Scroll EXACTLY one suggestion pill per action. No wrap compensation
      // needed: the switch group is not inside the scroller, so the first
      // pill is always fully visible once the scroll area starts.
      try {
        const bar = this._qpBar;
        if (!bar) return;
        const barR = bar.getBoundingClientRect();
        const leftOf = (el) => el.getBoundingClientRect().left - barR.left + bar.scrollLeft;
        const pills = this._qpSuggestPills();
        if (!pills.length) return;
        const tol = 4;
        const edge = bar.scrollLeft + tol;
        let target = dir > 0 ? null : 0;
        if (dir > 0) {
          for (const p of pills) {
            if (leftOf(p) > edge) { target = leftOf(p); break; }
          }
          if (target === null) target = bar.scrollWidth;
        } else {
          for (let i = pills.length - 1; i >= 0; i--) {
            if (leftOf(pills[i]) < edge - tol) { target = leftOf(pills[i]); break; }
          }
        }
        try { bar.scrollTo({ left: Math.max(0, target), behavior: "smooth" }); }
        catch (_) { bar.scrollLeft = Math.max(0, target); }
      } catch (_) {}
    }

    _appendBookingSwitchPill() {
      // Persistent way back into guided booking from Ask AI. Rendered as
      // part of every ask-mode bar paint so follow-up re-renders (which
      // clear the bar) can never drop it.
      // It lives in #qp-switch-slot — a NON-scrolling sibling of the pill bar
      // — so the switch control can never be overlapped by a scrolled pill.
      try {
        const slot = this.shadowRoot.getElementById("qp-switch-slot");
        const wrap = this._qpWrap || this.shadowRoot.getElementById("qp-bar-wrap");
        if (!slot || !wrap) return;
        if (slot.querySelector('[data-switch="booking"]')) return;
        const group = document.createElement("span");
        group.className = "qp-switch-wrap";
        const btn = document.createElement("button");
        btn.className = "qp-pill qp-mode";
        btn.textContent = "📅 Start Booking";
        btn.setAttribute("aria-label", "Switch to guided booking");
        btn.dataset.switch = "booking";
        btn.onclick = () => this._switchSection("booking");
        group.appendChild(btn);
        slot.appendChild(group);
        // Divider so the switch control never reads as another suggestion.
        const sep = document.createElement("span");
        sep.className = "qp-sep";
        sep.setAttribute("aria-hidden", "true");
        slot.appendChild(sep);
        wrap.classList.add("qp-has-switch");
      } catch (_) {}
    }

    _renderQuickPrompts(location) {
      // Never suggest while a human owns the chat (Mando parity).
      // Pills live in the fixed bar above the input (Mando style), not in
      // the message flow.
      if (this._humanActive || !this._qpBar) return;
      const prompts = this._quickPromptsFor(location);
      this._clearQuickPrompts();
      prompts.forEach((p) => {
        const btn = document.createElement("button");
        btn.className = "qp-pill";
        btn.textContent = p.title;
        btn.setAttribute("aria-label", `Suggested: ${p.title}`);
        btn.onclick = () => {
          if (p.link) {
            try { window.open(p.link, "_blank", "noopener"); } catch (_) {}
            return;
          }
          this._clearQuickPrompts();
          this._handleSendManual(p.content);
        };
        this._qpBar.appendChild(btn);
      });
      // Ask AI always keeps its booking switch pill, even with zero admin
      // prompts configured (otherwise there is no way back to booking).
      if (this._chatMode === "ask") {
        this._appendBookingSwitchPill();
      }
      // The switch pill is pinned OUTSIDE the scroller, so the bar itself is
      // only shown when there is at least one suggestion to scroll.
      if (this._qpBar.children.length) {
        this._qpBar.style.display = "flex";
      }
      try { this._refreshQpNav(); } catch (_) {}
    }

    async _renderDirectiveUI(directive, fieldMeta, agentMessage) {
      // Single source of directive→card rendering: used by the dispatch
      // response path AND by session resume (section switch back / page
      // reload), so a re-joined booking renders the exact same card the
      // flow would have shown — never free text.
      if (directive === "REQUIRE_DATE_PICKER") {
        this._appendDatePicker(fieldMeta);
      } else if (directive === "REQUIRE_PROVIDER_PICKER") {
        this._appendEngineProviderPicker(fieldMeta);
      } else if (directive === "REQUIRE_TIME_SLOT_GRID") {
        await this._appendTimeSlotGrid(fieldMeta);
      } else if (directive === "REQUIRE_PHONE_INPUT") {
        this._appendPhoneInput(fieldMeta);
      } else if (directive === "REQUIRE_EMAIL_INPUT") {
        if (this._chatMode === "booking") {
          this._appendFieldInputCard(fieldMeta, "email");
        } else {
          this._clearDirectiveCards();
          this._updateInputMode(
            "email",
            fieldMeta?.placeholder || "Enter email address...",
          );
          this._renderSkipAction(fieldMeta);
        }
      } else if (directive === "REQUIRE_NUMBER_INPUT") {
        if (this._chatMode === "booking") {
          this._appendFieldInputCard(fieldMeta, "number");
        } else {
          this._clearDirectiveCards();
          this._updateInputMode(
            "number",
            fieldMeta?.placeholder || "Enter a number...",
          );
          this._renderSkipAction(fieldMeta);
        }
      } else if (directive === "REQUIRE_SELECT") {
        this._appendSelectInput(fieldMeta);
      } else if (directive === "REQUIRE_TEXTAREA") {
        if (this._chatMode === "booking") {
          this._appendFieldInputCard(fieldMeta, "textarea");
        } else {
          this._clearDirectiveCards();
          this._appendSystem(
            `${fieldMeta?.icon || "📄"} ${fieldMeta?.description || "Please provide details."}`,
          );
          this._renderSkipAction(fieldMeta);
        }
      } else if (directive === "TEXT_INPUT" && fieldMeta) {
        if (this._chatMode === "booking") {
          this._appendFieldInputCard(fieldMeta, "text");
        } else {
          this._clearDirectiveCards();
          this._updateInputMode(
            "text",
            fieldMeta.placeholder || "Type a message...",
          );
          this._renderSkipAction(fieldMeta);
        }
      } else if (directive === "REQUIRE_PAYMENT") {
        this._clearDirectiveCards();
        this._appendPaymentCard(fieldMeta || {});
        this._clearSkipAction();
      } else if (directive === "SHOW_REBOOK_BUTTON") {
        this._clearDirectiveCards();
        this._appendRebookButton(fieldMeta || {});
        this._clearSkipAction();
      } else if (directive === "REQUIRE_OTP_MODAL") {
        this._clearDirectiveCards();
        this._appendOTPInterface(fieldMeta || {});
        this._clearSkipAction();
      } else if (
        directive === "REQUEST_CONFIRMATION" ||
        directive === "BOOKING_SUMMARY_CARD"
      ) {
        this._clearDirectiveCards();
        this._appendSummaryCard(
          agentMessage || "Please review your booking details before confirming.",
        );
        this._clearSkipAction();
      } else if (directive === "BOOKING_CONFIRMED_CARD") {
        this._clearDirectiveCards();
        this._appendConfirmedCard(
          agentMessage || "Your booking has been confirmed.",
          fieldMeta || {},
        );
        this._clearSkipAction();
      } else if (directive === "HUMAN_ACTIVE") {
        this._clearDirectiveCards();
        if (!this._humanActive && !this._humanJoinedShownFor) {
          this._appendSystem("👨‍💼 Support agent joined the chat");
          this._humanJoinedShownFor = this.sessionId;
        }
        this._updateInputMode("text", "Type a message...");
        this._clearSkipAction();
        this._clearQuickPrompts();
        this._humanActive = true;
        try { this._setComposerVisible(true); } catch (_) {}
        this._startPolling();
      } else {
        // Reset input to default mode for unknown directives. In guided
        // booking mode the composer is hidden, so unknown turns get a
        // generic answer card instead of a dead end.
        if (this._chatMode === "booking") {
          this._appendFieldInputCard(
            { label: "Your answer", placeholder: "Type your answer...", required: true },
            "text"
          );
        } else {
          this._clearDirectiveCards();
          this._updateInputMode("text", "Type a message...");
          this._clearSkipAction();
        }
      }
      // Post-render pills + composer visibility.
      try {
        const _noPills = ["REQUIRE_OTP_MODAL", "BOOKING_CONFIRMED_CARD", "HUMAN_ACTIVE", "REQUIRE_PAYMENT"].includes(directive);
        if (this._humanActive || _noPills) {
          this._clearQuickPrompts();
        } else if (this._chatMode === "booking") {
          this._renderModeExit();
          try { this._applyComposerRule(); } catch (_) {}
        } else {
          try { this._applyComposerRule(); } catch (_) {}
          this._renderQuickPrompts("followup");
        }
      } catch (_) {}
    }

    async _resumePendingCard() {
      // Re-joining a guided-booking session: render the PENDING step as its
      // directive card. The booking section is fully card-driven — the
      // composer stays hidden and the backend's session-resume endpoint
      // supplies the pending directive computed from live session state.
      if (!this.sessionId || !CONFIG.widgetToken || this._chatMode !== "booking") return;
      try {
        const base = CONFIG.apiBase.replace(/\/api\/v1\/?$/, "") + "/api/v1";
        const url = `${base}/widget/session-resume?session_id=${encodeURIComponent(this.sessionId)}&widget_token=${encodeURIComponent(CONFIG.widgetToken)}`;
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        if (!data || !data.directive) return;
        // Section switched mid-fetch → never render this card here.
        if (this._chatMode !== "booking") return;
        this._lastDirective = data.directive;
        this._activeFieldMeta = data.field_meta || null;
        try { this._setInlineValidation(""); } catch (_) {}
        await this._renderDirectiveUI(data.directive, data.field_meta || null, data.message || null);
      } catch (_) {}
    }

    async _appendTimeSlotGrid(fieldMeta) {
      this._clearDirectiveCards();
      this._clearSkipAction();
      if (fieldMeta?.slot_unavailable) {
        this.selectedSlotId = null;
      }
      const selectedDate = this._extractLatestIsoDate();
      this.selectedDate = selectedDate || this.selectedDate;
      // Fast-reply: show checking state immediately so the 30s+ engine
      // roundtrip does not look frozen (Feature 1).
      const loadingEl = this._appendSystem("⏳ Checking live availability…");
      let options = [];
      let availabilityStatus = null;
      let totalAvailable = 0;
      if (selectedDate) {
        const availability = await this._fetchAvailabilityOptions(selectedDate);
        // Section switched mid-fetch → never render this grid here.
        if (this._chatMode !== "booking") {
          try { loadingEl?.remove?.(); } catch (_) {}
          return;
        }
        options = availability.options;
        availabilityStatus = availability.status;
        totalAvailable = availability.total ?? options.filter((o) => o.available !== false).length;
      }
      try { loadingEl?.remove?.(); } catch (_) {}
      if (!options.length && fieldMeta?.options?.length) {
        options = fieldMeta.options.map((label) => ({
          id: null,
          label,
          available: true,
          reason: null,
        }));
        availabilityStatus = fieldMeta?.availability_status || availabilityStatus;
      }
      if (!options.length) {
        const fallbackReason =
          fieldMeta?.timezone_fallback_reason || this.timezoneFallbackReason || null;
        this._appendSystem(
          `${fieldMeta?.icon || "⏰"} ${this._availabilityRecoveryMessage(availabilityStatus, fallbackReason)}`,
        );
        this.selectedSlotId = null;
        this.selectedDate = null;
        this._appendDatePicker({
          icon: "📅",
          label: "Choose another date",
          required: fieldMeta?.required,
        });
        return;
      }

      const div = document.createElement("div");
      div.className = "message agent field-input-card directive-card";

      const label = document.createElement("div");
      label.className = "card-header";
      label.textContent = `${fieldMeta.icon || "⏰"} ${fieldMeta.label || "Select a Time Slot"}`;
      div.appendChild(label);
      div.appendChild(this._buildTimezoneContextBanner(selectedDate));

      if (fieldMeta.description) {
        const desc = document.createElement("p");
        desc.className = "field-description";
        desc.textContent = fieldMeta.description;
        div.appendChild(desc);
      }
      if (fieldMeta?.validation_error) {
        const error = document.createElement("p");
        error.className = "field-inline-error";
        error.textContent = fieldMeta.validation_error;
        div.appendChild(error);
      }

      const optionsContainer = document.createElement("div");
      optionsContainer.className = "select-options-grid";

      // Suggestions-first: show 3 pills + "Show more (N)" instead of the
      // full grid. Single fetch, two-stage render — no second request.
      const _suggestLimit = Math.max(1, Number(fieldMeta?.suggestion_limit || 3) || 3);
      const _free = options.filter((o) => o.available !== false);
      const _hasMany = _free.length > _suggestLimit;
      const _initial = _hasMany ? _free.slice(0, _suggestLimit) : options;
      const _remaining = _hasMany ? _free.slice(_suggestLimit) : [];
      const _renderBtn = (opt) => {
        const btn = document.createElement("button");
        btn.className = "select-option-btn time-slot-btn";
        const localText = document.createElement("span");
        localText.className = "slot-local-time";
        localText.textContent = opt.label;
        btn.appendChild(localText);
        if (opt.userLabel) {
          const orgText = document.createElement("span");
          orgText.className = "slot-org-time";
          orgText.textContent = `Your time: ${opt.userLabel}`;
          btn.appendChild(orgText);
        }
        if (!opt.available) {
          btn.disabled = true;
          btn.classList.add("unavailable");
          if (opt.reason) btn.title = `Unavailable (${opt.reason})`;
        }
        btn.onclick = () => {
          if (!opt.available) return;
          optionsContainer.querySelectorAll(".time-slot-btn").forEach((b) => {
            b.disabled = true;
            b.classList.remove("selected");
          });
          btn.classList.add("selected");
          this.selectedSlotId = opt.id || null;
          this._handleSendManual(opt.label);
        };
        return btn;
      };
      _initial.forEach((opt) => optionsContainer.appendChild(_renderBtn(opt)));
      if (_hasMany) {
        const moreBtn = document.createElement("button");
        moreBtn.className = "select-option-btn time-slot-more-btn";
        moreBtn.textContent = `Show more times (${_remaining.length} more)`;
        moreBtn.setAttribute("aria-label", `Show ${_remaining.length} more time slots`);
        moreBtn.onclick = () => {
          moreBtn.remove();
          _remaining.forEach((opt) => optionsContainer.appendChild(_renderBtn(opt)));
          this._scrollToBottom();
        };
        optionsContainer.appendChild(moreBtn);
      }

      div.appendChild(optionsContainer);
      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    _extractLatestIsoDate() {
      if (this.selectedDate) return this.selectedDate;
      const bubbles = this._msgList.querySelectorAll(".message.user");
      for (let i = bubbles.length - 1; i >= 0; i--) {
        const text = (bubbles[i].textContent || "").trim();
        const match = text.match(/\d{4}-\d{2}-\d{2}/);
        if (match) {
          return match[0];
        }
      }
      return null;
    }

    async _fetchAvailabilityOptions(dateValue) {
      try {
        const response = await fetch(this._buildAvailabilityRequestUrl(dateValue));
        if (!response.ok) return { options: [], status: null };
        const payload = await response.json();
        this.orgTimezone = payload.org_timezone || this.orgTimezone;
        if (payload.user_timezone) {
          this.userTimezone = payload.user_timezone;
        }
        if (payload.timezone_source) {
          this.timezoneSource = payload.timezone_source;
        }
        this.timezoneFallbackUsed = !!payload.timezone_fallback_used;
        this.timezoneFallbackReason = payload.timezone_fallback_reason || null;
        this._persistTimezonePreference();
        this._showTimezoneFallbackNoticeIfNeeded();
        return {
          options: this._normalizeAvailabilitySlots(payload.slots),
          status: payload.availability_status || null,
          total: typeof payload.total_available === "number" ? payload.total_available : null,
          truncated: !!payload.truncated,
        };
      } catch (e) {
        return { options: [], status: null, total: null, truncated: false };
      }
    }

    _appendOTPInterface(fieldMeta = {}) {
      const channel = (fieldMeta?.channel || "email").toLowerCase();
      const isPhone = channel === "phone";
      const div = document.createElement("div");
      div.className = "message agent otp-card";
      div.setAttribute("role", "form");
      div.setAttribute("aria-label", isPhone ? "Phone verification" : "Email verification");

      const header = document.createElement("div");
      header.className = "card-header";
      header.textContent = isPhone ? "Phone Verification Required" : "Verification Required";

      const desc = document.createElement("p");
      desc.textContent = isPhone
        ? "Please enter the SMS verification code sent to your phone."
        : "Please enter the email verification code to continue booking.";

      const policy = {
        cooldownSeconds: Number(fieldMeta?.otp_cooldown_seconds || 30),
        resendCap: Number(fieldMeta?.resend_cap || 3),
        resendsLeft: Number(
          typeof fieldMeta?.resends_left === "number"
            ? fieldMeta.resends_left
            : fieldMeta?.resend_cap || 3,
        ),
      };

      const inputGroup = document.createElement("div");
      inputGroup.className = "otp-input-group";

      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 8;
      input.placeholder = "Enter code";
      input.id = "otp-field";
      input.setAttribute("aria-label", "Verification code");
      input.setAttribute("autocomplete", "one-time-code");

      const btn = document.createElement("button");
      btn.id = "verify-otp-btn";
      btn.textContent = "Verify";
      btn.setAttribute("aria-label", "Verify code");

      inputGroup.appendChild(input);
      inputGroup.appendChild(btn);

      const resendLink = document.createElement("button");
      resendLink.className = "otp-resend-link";
      resendLink.textContent = "Didn't receive it? Resend code";
      resendLink.setAttribute("aria-label", "Resend verification code");

      const statusMsg = document.createElement("div");
      statusMsg.className = "otp-status";
      statusMsg.setAttribute("aria-live", "polite");
      statusMsg.textContent = `Resend available in ${policy.cooldownSeconds}s • ${policy.resendsLeft} resends left`;

      const recoverBtn = document.createElement("button");
      recoverBtn.className = "otp-resend-link";
      recoverBtn.textContent = "Use a different email address";
      recoverBtn.setAttribute("aria-label", "Use different email address");

      div.appendChild(header);
      div.appendChild(desc);
      div.appendChild(inputGroup);
      div.appendChild(resendLink);
      div.appendChild(recoverBtn);
      div.appendChild(statusMsg);

      this._msgList.appendChild(div);
      this._scrollToBottom();

      // Disable main chat input during OTP — prevent codes being sent to chat
      this._input.disabled = true;
      this._sendBtn.disabled = true;
      this._input.placeholder = "Enter the verification code above...";
      this._input.style.opacity = "0.5";

      // Focus the OTP input
      setTimeout(() => input.focus(), 100);

      // Phone codes are generated by the backend and delivered via WhatsApp;
      // the modal below only collects the code and verifies it server-side.
      if (isPhone) {
        // Backend already sent the code via WhatsApp when the OTP modal was
        // issued. Just enable input and show status.
        setTimeout(() => {
          sendStatus = "Code sent via WhatsApp. Enter it above.";
          statusMsg.textContent = sendStatus;
          statusMsg.style.color = "#22c55e";
          updateResendUi();
          btn.disabled = false;
          input.disabled = false;
        }, 100);
      }

      let resendCount = Math.max(0, policy.resendCap - policy.resendsLeft);
      let cooldownRemaining = Math.max(0, policy.cooldownSeconds);
      let sendStatus = ""; // keep send status separate so cooldown doesn't clobber it
      const updateResendUi = () => {
        const resendsLeft = Math.max(0, policy.resendCap - resendCount);
        const cooldownText =
          cooldownRemaining > 0
            ? `Resend available in ${cooldownRemaining}s`
            : "You can resend now";
        // For phone, preserve send status and append cooldown; don't overwrite Code sent/Failed
        if (isPhone && sendStatus) {
          statusMsg.textContent = `${sendStatus} • ${cooldownText} • ${resendsLeft} resends left`;
        } else {
          statusMsg.textContent = `${cooldownText} • ${resendsLeft} resends left`;
        }
        resendLink.disabled =
          cooldownRemaining > 0 || resendCount >= policy.resendCap;
      };
      updateResendUi();
      const cooldownInterval = window.setInterval(() => {
        if (cooldownRemaining <= 0) {
          window.clearInterval(cooldownInterval);
          return;
        }
        cooldownRemaining -= 1;
        updateResendUi();
      }, 1000);

      btn.onclick = async () => {
        const code = (input.value || "").replace(/\s+/g, "");
        console.warn("[DotStark] Verify clicked. code length:", code.length, "session:", this.sessionId, "channel:", channel);
        _logToBackend("verify_clicked", `code_len:${code.length} channel:${channel}`);
        // Keep global session for backend logs
        try { window._dotstarkLastSessionId = this.sessionId; } catch (_) {}
        if (code.length < 4) {
          statusMsg.textContent = isPhone ? "Please enter the code sent to your phone." : "Please enter the code sent to your email.";
          statusMsg.style.color = "#ef4444";
          input.focus();
          return;
        }
        if (!this.sessionId) {
          statusMsg.textContent = "Session expired. Please refresh and start again.";
          statusMsg.style.color = "#ef4444";
          return;
        }

        btn.disabled = true;
        input.disabled = true;
        btn.textContent = "...";
        statusMsg.textContent = "";

        try {
          let res;
          if (isPhone) {
            // Phone codes are generated by the backend and delivered via
            // WhatsApp — verify the 6-digit code directly, no client SDK.
            try {
              const waPayload = { session_id: this.sessionId, channel: "phone", code };
              console.warn("[DotStark] Verifying WhatsApp phone code");
              _logToBackend("wa_verify_attempt", `code_len:${code.length}`);
              const waRes = await fetch(`${CONFIG.apiBase}/auth/security/otp/verify`, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Widget-Token": CONFIG.widgetToken || "", "X-Requested-With": "XMLHttpRequest" },
                body: JSON.stringify(waPayload)
              });
              if (waRes.ok) {
                console.warn("[DotStark] WhatsApp phone verify succeeded");
                _logToBackend("wa_verify_success", code.slice(0,2)+"***");
                res = waRes;
              } else {
                const t = await waRes.text().catch(()=> "");
                const detail = t.slice(0,120);
                console.warn("[DotStark] WhatsApp verify failed status", waRes.status, detail);
                _logToBackend("wa_verify_failed", `status:${waRes.status} ${detail.slice(0,60)}`);
                statusMsg.textContent = detail || "That code is incorrect or has expired. Please try again or resend a new code.";
                statusMsg.style.color = "#ef4444";
                btn.disabled = false;
                input.disabled = false;
                btn.textContent = "Verify";
                input.value = "";
                input.focus();
                return;
              }
            } catch (waErr) {
              console.warn("[DotStark] WhatsApp verify error", waErr);
              _logToBackend("wa_verify_error", String(waErr).slice(0,80));
              statusMsg.textContent = "Couldn't reach the server to verify. Please check your connection and try again.";
              statusMsg.style.color = "#ef4444";
              btn.disabled = false;
              input.disabled = false;
              btn.textContent = "Verify";
              return;
            }
          } else {
            res = await fetch(
              `${CONFIG.apiBase}/auth/security/otp/verify`,
              {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                  "X-Widget-Token": CONFIG.widgetToken || "",
                  "X-Requested-With": "XMLHttpRequest",
                },
                body: JSON.stringify({ session_id: this.sessionId, code, channel }),
              },
            );
          }

          if (res.ok) {
            div.textContent = "";
            const success = document.createElement("div");
            success.className = "success-msg";
            success.textContent = isPhone ? "✅ Phone Verified Successfully!" : "✅ Email Verified Successfully!";
            div.appendChild(success);
            this._input.disabled = false;
            this._sendBtn.disabled = false;
            this._input.placeholder = "Type a message...";
            this._input.style.opacity = "1";
            setTimeout(
              () =>
                this._handleSendManual(
                  "I've verified the code. Please proceed.",
                ),
              1000,
            );
          } else {
            let detail = "";
            try {
              const errData = await res.json();
              detail = errData && errData.detail ? String(errData.detail) : "";
            } catch (e) {}
            statusMsg.textContent =
              res.status === 429
                ? detail || "Too many attempts. Please request a new code."
                : detail ||
                  "That code is incorrect or has expired. Please try again or resend a new code.";
            statusMsg.style.color = "#ef4444";
            btn.disabled = false;
            input.disabled = false;
            btn.textContent = "Verify";
            input.value = "";
            input.focus();
          }
        } catch (e) {
          statusMsg.textContent =
            "Couldn't reach the server to verify. Please check your connection and try again.";
          statusMsg.style.color = "#ef4444";
          btn.disabled = false;
          input.disabled = false;
          btn.textContent = "Verify";
        }
      };

      input.onkeydown = (e) => {
        if (e.key === "Enter") btn.click();
      };

      resendLink.onclick = async () => {
        if (resendCount >= policy.resendCap) {
          statusMsg.textContent =
            "Maximum resend attempts reached. Please try again later.";
          statusMsg.style.color = "#ef4444";
          return;
        }
        if (cooldownRemaining > 0) {
          return;
        }
        resendLink.disabled = true;
        resendLink.textContent = "Sending...";
        statusMsg.textContent = "";

        try {
          // Resend via backend chat "resend phone" will create a new OTP card with updated resends_left.
          // Remove this stale card so user sees only the fresh one with correct count.
          const thisCard = div;
          if (isPhone) {
            try { await this._handleSendManual("resend phone"); } catch (_) {}
          } else {
            await this._handleSendManual("resend");
          }
          // Increment local counter and immediately update this card before it is replaced
          resendCount++;
          cooldownRemaining = policy.cooldownSeconds;
          // Update this card's display before it gets replaced by the new card from backend
          const left = Math.max(0, policy.resendCap - resendCount);
          if (!isPhone) {
            statusMsg.textContent = `Code resent! (${left} resends left)`;
            statusMsg.style.color = "#22c55e";
            sendStatus = statusMsg.textContent;
          } else {
            sendStatus = `Code resent! (${left} resends left)`;
            statusMsg.textContent = `${sendStatus} • Resend available in ${cooldownRemaining}s • ${left} resends left`;
            statusMsg.style.color = "#22c55e";
          }
          updateResendUi();
          // Hide this stale card after a short delay so the new card (with correct 2 left) is visible at bottom
          setTimeout(() => { try { thisCard.style.opacity = "0.5"; thisCard.style.pointerEvents = "none"; } catch (_) {} }, 300);
        } catch (e) {
          statusMsg.textContent = "Failed to resend. Please try again.";
          statusMsg.style.color = "#ef4444";
          sendStatus = statusMsg.textContent;
          updateResendUi();
        }

        resendLink.textContent = "Didn't receive it? Resend code";
      };

      recoverBtn.textContent = isPhone ? "Use a different phone number" : "Use a different email address";
      recoverBtn.setAttribute("aria-label", isPhone ? "Use different phone" : "Use different email address");
      recoverBtn.onclick = () => {
        this._input.disabled = false;
        this._sendBtn.disabled = false;
        this._input.placeholder = isPhone ? "Enter the correct phone number..." : "Enter the correct email address...";
        this._input.style.opacity = "1";
        this._input.focus();
        statusMsg.textContent = isPhone ? "No problem — provide the correct phone and we’ll resend there." : "No problem — provide the correct email and we’ll resend there.";
        statusMsg.style.color = "#64748b";
      };

      this._scrollToBottom();
      input.focus();
    }

    _appendSummaryCard(message) {
      const div = document.createElement("div");
      div.className = "message agent summary-card";

      const header = document.createElement("div");
      header.className = "card-header";
      header.textContent = "📋 Booking Summary";

      const content = document.createElement("div");
      content.className = "summary-content";
      this._renderMarkdown(content, message);

      const feeDisplay = this._selectedProviderFee();
      if (feeDisplay) {
        const feeLine = document.createElement("div");
        feeLine.className = "summary-fee-line";
        feeLine.textContent = `Fee: ${feeDisplay}`;
        content.appendChild(feeLine);
      }

      const actions = document.createElement("div");
      actions.className = "card-actions";

      const confirmBtn = document.createElement("button");
      confirmBtn.className = "confirm-btn";
      confirmBtn.textContent = "Confirm Booking";
      confirmBtn.setAttribute("aria-label", "Confirm booking");

      const editBtn = document.createElement("button");
      editBtn.className = "edit-btn";
      editBtn.textContent = "✎ Change Details";
      editBtn.setAttribute("aria-label", "Edit booking details");

      actions.appendChild(confirmBtn);
      actions.appendChild(editBtn);
      div.appendChild(header);
      div.appendChild(content);
      div.appendChild(actions);
      this._msgList.appendChild(div);

      confirmBtn.onclick = () => {
        confirmBtn.disabled = true;
        editBtn.disabled = true;
        this._handleSendManual("Yes, please confirm the booking.");
      };
      editBtn.onclick = () => {
        confirmBtn.disabled = true;
        editBtn.disabled = true;
        this._handleSendManual("I need to change some details.");
      };

      this._scrollToBottom();
    }

    async _loadRazorpay() {
      if (window.Razorpay) return true;
      try {
        await new Promise((resolve, reject) => {
          const s = document.createElement("script");
          s.src = "https://checkout.razorpay.com/v1/checkout.js";
          s.onload = resolve;
          s.onerror = () => reject(new Error("razorpay load failed"));
          document.head.appendChild(s);
        });
        return !!window.Razorpay;
      } catch { return false; }
    }

    async _loadCashfree() {
      if (window.Cashfree) return true;
      try {
        await new Promise((resolve, reject) => {
          const s = document.createElement("script");
          s.src = "https://sdk.cashfree.com/js/v3/cashfree.js";
          s.onload = resolve;
          s.onerror = () => reject(new Error("cashfree sdk load failed"));
          document.head.appendChild(s);
        });
        return !!window.Cashfree;
      } catch { return false; }
    }

    /** Poll order status until the webhook (or our reconcile call) marks it paid. */
    async _pollCashfreePaid(orderId, onTick) {
      const base = CONFIG.apiBase.replace(/\/api\/v1\/?$/, "") + "/api/v1";
      for (let i = 0; i < 40; i++) {
        try {
          const res = await fetch(
            `${base}/widget/payment/cashfree/status?order_id=${encodeURIComponent(orderId)}&session_id=${encodeURIComponent(this.sessionId)}`,
            { headers: { "X-Widget-Token": CONFIG.widgetToken || "" } }
          );
          if (res.ok) {
            const j = await res.json().catch(() => ({}));
            if (j.status === "paid") return true;
          }
        } catch { /* transient network error — keep polling */ }
        onTick && onTick(i);
        await new Promise((r) => setTimeout(r, 3000));
      }
      return false;
    }

    /** Single "start over" button after cancel/expiry — no free-text card. */
    _appendRebookButton(fieldMeta = {}) {
      const div = document.createElement("div");
      div.className = "message agent directive-card";
      div.setAttribute("role", "form");
      const btn = document.createElement("button");
      btn.textContent = fieldMeta.label || "Book a new appointment";
      btn.style.cssText = "margin-top:10px;padding:12px 20px;border:none;border-radius:10px;background:var(--primary);color:#fff;font-weight:700;width:100%;cursor:pointer;";
      const payload = fieldMeta.payload || "Book a new appointment";
      btn.onclick = () => {
        btn.disabled = true;
        this._handleSendManual(payload);
      };
      div.appendChild(btn);
      this._msgList.appendChild(div); this._scrollToBottom();
    }

    _appendCashfreePaymentCard(fieldMeta = {}) {
      const div = document.createElement("div");
      div.className = "message agent payment-card";
      div.setAttribute("role", "form");
      const amountPaise = Number(fieldMeta.amount_paise || 0);
      const amountDisplay = fieldMeta.amount_display || (amountPaise ? `₹${(amountPaise/100).toFixed(2)}` : "₹500.00");
      const currency = fieldMeta.currency || "INR";
      const h = document.createElement("div");
      h.className = "card-header";
      h.textContent = "Payment Required";
      const p = document.createElement("p");
      p.style.cssText = "font-size:13px;color:var(--text-sec);margin:8px 0;";
      p.textContent = `Your appointment total is ${amountDisplay} (${currency}). Please pay to confirm your booking. UPI, card, netbanking or wallet via Cashfree.`;
      const btn = document.createElement("button");
      btn.textContent = `Pay ${amountDisplay}`;
      btn.style.cssText = "margin-top:10px;padding:12px 20px;border:none;border-radius:10px;background:var(--primary);color:#fff;font-weight:700;width:100%;cursor:pointer;";
      const status = document.createElement("div");
      status.id = "payment-status";
      status.style.cssText = "font-size:12px;color:var(--text-sec);margin-top:8px;";
      const reset = (msg) => {
        btn.disabled = false;
        btn.textContent = `Pay ${amountDisplay}`;
        if (msg) status.textContent = msg;
      };
      btn.onclick = async () => {
        btn.disabled = true;
        btn.textContent = "Opening Cashfree…";
        status.textContent = "";
        try {
          const base = CONFIG.apiBase.replace(/\/api\/v1\/?$/, "") + "/api/v1";
          const orderRes = await fetch(`${base}/widget/payment/cashfree/order`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Widget-Token": CONFIG.widgetToken || "", "X-Requested-With": "XMLHttpRequest" },
            body: JSON.stringify({ session_id: this.sessionId, widget_token: CONFIG.widgetToken })
          });
          if (!orderRes.ok) {
            const e = await orderRes.json().catch(() => ({}));
            throw new Error(
              (e.detail && (e.detail.message || e.detail.code)) || e.detail || "Failed to create order"
            );
          }
          const order = await orderRes.json();
          if (!(await this._loadCashfree())) throw new Error("Failed to load Cashfree checkout");
          const cashfree = window.Cashfree({
            mode: order.environment === "production" ? "production" : "sandbox"
          });
          // _blank (new tab): bank/OTP pages refuse to load inside the
          // checkout modal iframe, which dead-ended card payments. The chat
          // tab stays alive and reconciles via status polling below.
          status.textContent = "Opening secure payment page in a new tab…";
          try { _logToBackend("cf_checkout_open", `${order.environment}:${order.order_id}`); } catch (_) {}
          const result = await cashfree.checkout({
            paymentSessionId: order.payment_session_id,
            redirectTarget: "_blank"
          });
          try {
            _logToBackend("cf_checkout_result", JSON.stringify({
              error: result && result.error,
              redirect: !!(result && result.redirect),
              hasDetails: !!(result && result.paymentDetails),
              order: order.order_id,
            }).slice(0, 200));
          } catch (_) {}
          if (result && result.error) {
            // New tab blocked or checkout errored — confirm real state anyway.
            status.textContent = "Checking payment status…";
          } else {
            status.textContent = "Payment page opened — complete the payment in the new tab…";
          }
          const paid = await this._pollCashfreePaid(order.order_id, () => {
            status.textContent = "Waiting for payment… complete it in the new tab.";
          });
          if (!paid) {
            reset("Payment was not completed. Your appointment has not been confirmed. You can retry.");
            return;
          }
          status.textContent = "Payment successful ✅";
          this._appendSystem("Payment successful ✅ Appointment confirmed ✅");
          this._input.value = "confirm";
          await this._handleSend();
        } catch (e) {
          try { _logToBackend("cf_checkout_error", String((e && e.message) || e).slice(0, 200)); } catch (_) {}
          reset("Error: " + (e.message || e));
        }
      };
      div.appendChild(h); div.appendChild(p); div.appendChild(btn); div.appendChild(status);
      this._msgList.appendChild(div); this._scrollToBottom();
    }

    _appendPaymentCard(fieldMeta = {}) {
      // Provider comes from the server (org's own collection rail).
      if ((fieldMeta.payment_provider || "razorpay") === "cashfree") {
        return this._appendCashfreePaymentCard(fieldMeta);
      }
      const div = document.createElement("div");
      div.className = "message agent payment-card";
      div.setAttribute("role", "form");
      const amountPaise = Number(fieldMeta.amount_paise || 0);
      const amountDisplay = fieldMeta.amount_display || (amountPaise ? `₹${(amountPaise/100).toFixed(2)}` : "₹500.00");
      const currency = fieldMeta.currency || "INR";
      const h = document.createElement("div");
      h.className = "card-header";
      h.textContent = "Payment Required";
      const p = document.createElement("p");
      p.style.cssText = "font-size:13px;color:var(--text-sec);margin:8px 0;";
      p.textContent = `Your appointment total is ${amountDisplay} (${currency}). Please pay to confirm your booking. UPI Intent on mobile, QR on desktop via Razorpay Checkout.`;
      const btn = document.createElement("button");
      btn.textContent = `Pay ${amountDisplay}`;
      btn.style.cssText = "margin-top:10px;padding:12px 20px;border:none;border-radius:10px;background:var(--primary);color:#fff;font-weight:700;width:100%;cursor:pointer;";
      const status = document.createElement("div");
      status.id = "payment-status";
      status.style.cssText = "font-size:12px;color:var(--text-sec);margin-top:8px;";
      btn.onclick = async () => {
        btn.disabled = true; btn.textContent = "Opening Razorpay…"; status.textContent = "";
        try {
          const ok = await this._loadRazorpay();
          if (!ok) throw new Error("Failed to load Razorpay");
          const base = CONFIG.apiBase.replace(/\/api\/v1\/?$/, "") + "/api/v1";
          const orderRes = await fetch(`${base}/widget/payment/order`, {
            method: "POST",
            headers: {"Content-Type":"application/json", "X-Widget-Token": CONFIG.widgetToken || "", "X-Requested-With": "XMLHttpRequest"},
            body: JSON.stringify({session_id: this.sessionId, widget_token: CONFIG.widgetToken})
          });
          if (!orderRes.ok) {
            const e = await orderRes.json().catch(()=>({}));
            throw new Error(e.detail || "Failed to create order");
          }
          const order = await orderRes.json();
          const options = {
            key: order.key,
            amount: order.amount_paise,
            currency: order.currency || "INR",
            name: this._agentConfig?.business_name || "Aurexa",
            description: "Appointment payment",
            order_id: order.order_id,
            handler: async (resp) => {
              status.textContent = "Verifying payment…";
              try {
                const vr = await fetch(`${base}/widget/payment/verify`, {
                  method: "POST",
                  headers: {"Content-Type":"application/json", "X-Widget-Token": CONFIG.widgetToken || "", "X-Requested-With": "XMLHttpRequest"},
                  body: JSON.stringify({
                    session_id: this.sessionId,
                    razorpay_order_id: resp.razorpay_order_id,
                    razorpay_payment_id: resp.razorpay_payment_id,
                    razorpay_signature: resp.razorpay_signature,
                    widget_token: CONFIG.widgetToken
                  })
                });
                const vj = await vr.json().catch(()=>({}));
                if (!vr.ok) throw new Error(vj.detail || "Verification failed");
                status.textContent = "Payment successful ✅";
                this._appendSystem("Payment successful ✅ Appointment confirmed ✅");
                // Trigger next step: ask backend to confirm booking (dispatch confirm)
                this._input.value = "confirm";
                await this._handleSend();
              } catch (ve) {
                status.textContent = "Payment verification failed: " + (ve.message || ve);
                btn.disabled = false; btn.textContent = `Pay ${amountDisplay}`;
              }
            },
            modal: { ondismiss: () => { btn.disabled = false; btn.textContent = `Pay ${amountDisplay}`; status.textContent = "Payment was not completed. Your appointment has not been confirmed. You can retry."; } },
            theme: {color: this._agentConfig?.brand_color || "#3399CC"}
          };
          const rzp = new window.Razorpay(options);
          rzp.on("payment.failed", (r) => { status.textContent = "Payment failed: " + (r.error?.description || "try again"); btn.disabled = false; btn.textContent = `Pay ${amountDisplay}`; });
          rzp.open();
        } catch (e) {
          status.textContent = "Error: " + (e.message || e);
          btn.disabled = false; btn.textContent = `Pay ${amountDisplay}`;
        }
      };
      div.appendChild(h); div.appendChild(p); div.appendChild(btn); div.appendChild(status);
      this._msgList.appendChild(div); this._scrollToBottom();
    }

    _appendConfirmedCard(message, fieldMeta = {}) {
      const div = document.createElement("div");
      div.className = "message agent confirmed-card";
      div.setAttribute("role", "alert");

      const icon = document.createElement("div");
      icon.className = "conf-icon";
      icon.textContent = "🎉";

      const heading = document.createElement("h3");
      heading.textContent = "Booking Confirmed!";
      heading.style.cssText =
        "font-size:16px;font-weight:700;color:var(--success);margin:8px 0;";

      const msg = document.createElement("p");
      msg.style.cssText =
        "font-size:13px;color:var(--text-sec);line-height:1.5;";
      this._renderMarkdown(msg, message);

      const footer = document.createElement("div");
      footer.className = "conf-footer";
      footer.textContent =
        fieldMeta?.email_retrying === true
          ? "📧 Booking confirmed. Confirmation email delivery is retrying in the background."
          : "📧 A confirmation email is on its way.";

      // Feature 3: "Add to Calendar (.ics)" — only when the backend
      // proved the booking (booking_id in fieldMeta). No id = no button,
      // so a false-confirmation can never offer a calendar file.
      try {
        const bid = fieldMeta && fieldMeta.booking_id;
        if (bid) {
          const icsBtn = document.createElement("a");
          const tok = (typeof CONFIG !== "undefined" && CONFIG.widgetToken) || "";
          icsBtn.href = `${CONFIG.apiBase}/widget/invite/${encodeURIComponent(String(bid))}?widget_token=${encodeURIComponent(tok)}`;
          icsBtn.textContent = "📅 Add to Calendar (.ics)";
          icsBtn.setAttribute("download", `booking-${String(bid)}.ics`);
          icsBtn.style.cssText = `
                        margin-top:12px;padding:10px 20px;border:1px solid var(--border);border-radius:8px;
                        background:transparent;color:var(--text);font-size:13px;font-weight:600;
                        cursor:pointer;width:100%;text-align:center;text-decoration:none;display:block;box-sizing:border-box;
                    `;
          div.appendChild(icsBtn);
        }
      } catch (_) {}

      // Reschedule + Cancel shortcuts for the confirmed booking.
      // Deterministic phrases the backend routes straight to date-pick
      // (reschedule, details kept) or cancel-OTP (cancel) — no re-asking.
      const _mkActionBtn = (label, text) => {
        const b = document.createElement("button");
        b.textContent = label;
        b.style.cssText = `
                    margin-top:8px;padding:10px 20px;border:1px solid var(--border);border-radius:8px;
                    background:transparent;color:var(--text);font-size:13px;font-weight:600;
                    cursor:pointer;width:100%;box-sizing:border-box;
                `;
        b.addEventListener("click", () => this._handleSendManual(text));
        return b;
      };
      div.appendChild(_mkActionBtn("📅 Reschedule", "reschedule my booking"));
      div.appendChild(_mkActionBtn("❌ Cancel Booking", "cancel my booking"));

      // "Start New Booking" button
      const newBtn = document.createElement("button");
      newBtn.textContent = "🔄 Start New Booking";
      newBtn.style.cssText = `
                margin-top:12px;padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;
            `;
      newBtn.addEventListener(
        "mouseover",
        () => (newBtn.style.opacity = "0.85"),
      );
      newBtn.addEventListener("mouseout", () => (newBtn.style.opacity = "1"));
      newBtn.addEventListener("click", () => this._startNewSession());

      div.appendChild(icon);
      div.appendChild(heading);
      div.appendChild(msg);
      div.appendChild(footer);
      div.appendChild(newBtn);
      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    async _handleSendManual(text) {
      this._input.value = text;
      await this._handleSend();
    }

    _disableInput(placeholder) {
      this._input.disabled = true;
      this._sendBtn.disabled = true;
      this._input.placeholder = placeholder || "Session ended.";
      this._input.style.opacity = "0.5";
      this._sendBtn.style.opacity = "0.3";
    }

    _enableInput(placeholder) {
      this._input.disabled = false;
      this._sendBtn.disabled = false;
      this._input.placeholder = placeholder || "Type a message...";
      this._input.style.opacity = "1";
      this._sendBtn.style.opacity = "1";
    }

    _setComposerVisible(visible) {
      // Show/hide the whole typing bar. Booking mode hides it (every step
      // is a card); Ask AI and legacy flows keep it visible.
      try {
        if (this._inputArea) {
          this._inputArea.style.display = visible ? "flex" : "none";
        }
      } catch (_) {}
    }

    _appendFieldInputCard(fieldMeta = {}, inputType = "text") {
      // Standalone input card for guided-booking mode (composer is hidden,
      // so each field carries its own input + submit). Mirrors the legacy
      // composer path: validation errors inline, Skip when optional.
      this._clearDirectiveCards();
      this._clearSkipAction();
      const div = document.createElement("div");
      div.className = "message agent field-input-card directive-card";
      const label = document.createElement("div");
      label.className = "card-header";
      label.textContent = `${fieldMeta.icon || "✏️"} ${fieldMeta.label || "Your answer"}`;
      div.appendChild(label);
      if (fieldMeta.description) {
        const desc = document.createElement("p");
        desc.className = "field-description";
        desc.textContent = fieldMeta.description;
        div.appendChild(desc);
      }
      if (fieldMeta?.validation_error) {
        const error = document.createElement("p");
        error.className = "field-inline-error";
        error.textContent = fieldMeta.validation_error;
        div.appendChild(error);
      }
      let input;
      if (inputType === "textarea") {
        input = document.createElement("textarea");
        input.rows = 3;
      } else {
        input = document.createElement("input");
        input.type = inputType === "email" ? "email" : inputType === "number" ? "number" : "text";
      }
      input.className = "widget-field-textinput";
      input.placeholder = fieldMeta.placeholder || `Enter ${fieldMeta.label || "your answer"}...`;
      input.setAttribute("aria-label", fieldMeta.label || "Your answer");
      div.appendChild(input);
      const row = document.createElement("div");
      row.className = "widget-field-actions";
      const submit = document.createElement("button");
      submit.className = "widget-field-btn";
      submit.textContent = "Submit";
      const doSubmit = () => {
        const val = (input.value || "").trim();
        if (!val && fieldMeta.required !== false) {
          let err = div.querySelector(".field-inline-error");
          if (!err) {
            err = document.createElement("p");
            err.className = "field-inline-error";
            div.insertBefore(err, input.nextSibling);
          }
          err.textContent = `Please enter ${fieldMeta.label || "your answer"}.`;
          try { input.focus(); } catch (_) {}
          return;
        }
        submit.disabled = true;
        input.disabled = true;
        this._handleSendManual(val || this._skipToken);
      };
      submit.onclick = doSubmit;
      input.onkeydown = (e) => {
        if (e.key === "Enter" && !e.shiftKey && inputType !== "textarea") {
          e.preventDefault();
          doSubmit();
        }
      };
      row.appendChild(submit);
      if (fieldMeta.required === false) {
        const skip = document.createElement("button");
        skip.className = "widget-field-btn widget-secondary-btn";
        skip.textContent = "Skip";
        skip.onclick = () => {
          skip.disabled = true;
          submit.disabled = true;
          this._handleSendManual(this._skipToken);
        };
        row.appendChild(skip);
      }
      div.appendChild(row);
      this._msgList.appendChild(div);
      this._scrollToBottom();
      setTimeout(() => { try { input.focus(); } catch (_) {} }, 100);
    }

    _startNewSession() {
      // Clear session, reset chat, start fresh
      this.sessionId = null;
      this._chatMode = null;
      this._lastDirective = null;
      try { sessionStorage.removeItem(this._modeStorageKey()); } catch (_) {}
      sessionStorage.removeItem(this._sessionStorageKey());
      try { sessionStorage.removeItem(this._sessionKeyFor("ask")); } catch (_) {}
      try { sessionStorage.removeItem(this._sessionKeyFor("booking")); } catch (_) {}
      sessionStorage.removeItem("dotstark_session_id");
      sessionStorage.removeItem("dotstark_user_timezone");
      sessionStorage.removeItem("dotstark_timezone_source");
      this.userTimezone = this._detectBrowserTimezone();
      this.timezoneSource = "detected";
      this.selectedSlotId = null;
      this.selectedDate = null;
      this.orgTimezone = null;
      this.timezoneFallbackUsed = false;
      this.timezoneFallbackReason = null;
      this._timezoneNoticeShown = false;
      this._directiveDrafts = {};
      this._setInlineValidation("");
      this._clearSkipAction();
      this._msgList.querySelectorAll(".message").forEach((m) => m.remove());
      try { this._clearQuickPrompts(); } catch (_) {}
      // Back to home slide: two big buttons, composer hidden.
      this._canGoHome = true;
      try { this._showHomeView(); } catch (_) {}
      try { this._setComposerVisible(false); } catch (_) {}
    }

    _appendNewBookingPrompt() {
      const div = document.createElement("div");
      div.className = "message agent";
      const btn = document.createElement("button");
      btn.textContent = "🔄 Start New Booking";
      btn.style.cssText = `
                padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;margin-top:4px;
            `;
      btn.addEventListener("mouseover", () => (btn.style.opacity = "0.85"));
      btn.addEventListener("mouseout", () => (btn.style.opacity = "1"));
      btn.addEventListener("click", () => this._startNewSession());
      div.appendChild(btn);
      this._msgList.appendChild(div);
      this._scrollToBottom();
    }

    _resolveAssistantText(rawValue) {
      if (rawValue && typeof rawValue === "object") {
        const preferred = rawValue.response_text || rawValue.message;
        return typeof preferred === "string" ? preferred.trim() : "";
      }
      if (typeof rawValue !== "string") return "";
      const text = rawValue.trim();
      if (!text) return "";
      if (text.startsWith("{") && text.endsWith("}")) {
        try {
          const parsed = JSON.parse(text);
          if (parsed && typeof parsed === "object") {
            const preferred = parsed.response_text || parsed.message;
            return typeof preferred === "string" ? preferred.trim() : "";
          }
        } catch (_) {}
      }
      return text;
    }

    _scrollToBottom(force = false) {
      requestAnimationFrame(() => {
        if (!force) {
          const distanceFromBottom = this._msgList.scrollHeight - this._msgList.scrollTop - this._msgList.clientHeight;
          if (distanceFromBottom > 120) return;
        }
        this._msgList.scrollTo({
          top: this._msgList.scrollHeight,
          behavior: "smooth",
        });
      });
    }

    /* ─────────── CSS ─────────── */
    _css() {
      return `
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

                :host {
                    --accent: var(--brand-color, #3b82f6);
                    --accent-hover: color-mix(in srgb, var(--accent) 80%, #000);
                    --accent-glow: color-mix(in srgb, var(--accent) 30%, transparent);
                    --accent-soft: color-mix(in srgb, var(--accent) 8%, transparent);
                    --bg-primary: #0a0a0a;
                    --bg-card: #121212;
                    --bg-input: #0e0e0e;
                    --border: #27272a;
                    --border-hover: #3f3f46;
                    --text-pri: #ffffff;
                    --text-sec: #a1a1aa;
                    --text-muted: #71717a;
                    --success: #22c55e;
                    --success-glow: rgba(34, 197, 94, 0.2);
                    --msg-agent-bg: #18181b;
                    --msg-user-bg: var(--accent);
                    position: fixed;
                    bottom: 24px;
                    right: 24px;
                    z-index: 9999;
                    font-family: 'Inter', system-ui, -apple-system, sans-serif;
                }

                *, *::before, *::after {
                    box-sizing: border-box;
                    margin: 0;
                    padding: 0;
                }

                /* ════════ LAUNCHER ════════ */
                .widget-launcher {
                    width: 56px;
                    height: 56px;
                    background: var(--accent);
                    border-radius: 16px;
                    box-shadow:
                        0 4px 20px var(--accent-glow),
                        0 2px 8px rgba(0,0,0,0.4);
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    transition: all 0.35s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                    border: none;
                    outline: none;
                    position: relative;
                    overflow: visible;
                }

                .widget-launcher::before {
                    content: '';
                    position: absolute;
                    inset: 0;
                    border-radius: 16px;
                    background: linear-gradient(135deg, rgba(255,255,255,0.18) 0%, transparent 50%);
                    pointer-events: none;
                    transition: opacity 0.3s;
                }

                .widget-launcher:hover {
                    transform: scale(1.1) rotate(-3deg);
                    box-shadow:
                        0 8px 32px var(--accent-glow),
                        0 4px 16px rgba(0,0,0,0.5);
                }

                .widget-launcher:active {
                    transform: scale(0.95);
                    transition-duration: 0.1s;
                }

                /* Launcher icon swap */
                .widget-launcher svg {
                    width: 26px;
                    height: 26px;
                    position: absolute;
                    transition: all 0.35s cubic-bezier(0.68, -0.55, 0.27, 1.55);
                }

                .widget-launcher .icon-chat {
                    fill: white;
                    stroke: none;
                    opacity: 1;
                    transform: scale(1) rotate(0deg);
                }

                .widget-launcher .icon-close {
                    stroke: white;
                    opacity: 0;
                    transform: scale(0.3) rotate(-90deg);
                }

                .widget-launcher.open .icon-chat {
                    opacity: 0;
                    transform: scale(0.3) rotate(90deg);
                }

                .widget-launcher.open .icon-close {
                    opacity: 1;
                    transform: scale(1) rotate(0deg);
                }

                .widget-launcher.open {
                    border-radius: 50%;
                    background: #27272a;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                }

                .widget-launcher.open:hover {
                    transform: scale(1.1) rotate(0deg);
                    background: #3f3f46;
                }

                /* Ripple ring on launcher */
                .ripple-ring {
                    position: absolute;
                    width: 56px;
                    height: 56px;
                    border-radius: 16px;
                    border: 2px solid var(--accent);
                    animation: rippleExpand 2.5s ease-out infinite;
                    pointer-events: none;
                }

                .widget-launcher.open .ripple-ring {
                    display: none;
                }

                @keyframes rippleExpand {
                    0% { transform: scale(1); opacity: 0.5; }
                    100% { transform: scale(1.7); opacity: 0; }
                }

                /* ════════ CHAT CONTAINER ════════ */
                .chat-container {
                    position: absolute;
                    bottom: 72px;
                    right: 0;
                    width: 400px;
                    height: 620px;
                    max-height: calc(100vh - 100px);
                    background: var(--bg-primary);
                    border-radius: 20px;
                    border: 1px solid var(--border);
                    box-shadow:
                        0 25px 60px rgba(0,0,0,0.6),
                        0 0 0 1px rgba(255,255,255,0.03),
                        0 0 80px -20px var(--accent-glow);
                    display: none;
                    flex-direction: column;
                    overflow: hidden;
                    transform-origin: bottom right;
                }

                .chat-container.visible {
                    display: flex;
                    animation: containerIn 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
                }

                .chat-container.hiding {
                    animation: containerOut 0.2s ease-in forwards;
                }

                @keyframes containerIn {
                    from { opacity: 0; transform: scale(0.88) translateY(12px); }
                    to   { opacity: 1; transform: scale(1) translateY(0); }
                }

                @keyframes containerOut {
                    from { opacity: 1; transform: scale(1) translateY(0); }
                    to   { opacity: 0; transform: scale(0.88) translateY(12px); }
                }

                /* ════════ HEADER ════════ */
                .header {
                    background: var(--bg-card);
                    padding: 16px 20px;
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    border-bottom: 1px solid var(--border);
                    min-height: 64px;
                    position: relative;
                    overflow: hidden;
                }

                .header::after {
                    content: '';
                    position: absolute;
                    bottom: 0;
                    left: 0;
                    right: 0;
                    height: 1px;
                    background: linear-gradient(90deg, transparent, var(--accent-glow), transparent);
                }

                .header-avatar {
                    width: 38px;
                    height: 38px;
                    background: linear-gradient(135deg, var(--accent) 0%, var(--accent-hover) 100%);
                    border-radius: 12px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    flex-shrink: 0;
                    position: relative;
                    overflow: hidden;
                    animation: avatarPop 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) 0.15s both;
                    box-shadow: 0 2px 8px var(--accent-glow);
                }

                @keyframes avatarPop {
                    from { transform: scale(0); }
                    to   { transform: scale(1); }
                }

                .header-avatar::after {
                    content: '';
                    position: absolute;
                    inset: 0;
                    border-radius: 12px;
                    background: linear-gradient(135deg, rgba(255,255,255,0.2) 0%, transparent 50%);
                }

                .header-info {
                    flex: 1;
                }

                .header-info h3 {
                    font-size: 14px;
                    font-weight: 600;
                    color: var(--text-pri);
                    letter-spacing: -0.3px;
                    line-height: 1.2;
                }

                .header-info p {
                    font-size: 11px;
                    color: var(--text-sec);
                    display: flex;
                    align-items: center;
                    gap: 6px;
                    margin-top: 3px;
                }

                .status-dot {
                    width: 7px;
                    height: 7px;
                    background: var(--success);
                    border-radius: 50%;
                    display: inline-block;
                    position: relative;
                    flex-shrink: 0;
                }

                .status-dot::after {
                    content: '';
                    position: absolute;
                    inset: -3px;
                    border-radius: 50%;
                    border: 1.5px solid var(--success);
                    animation: statusPing 2s ease-in-out infinite;
                }

                @keyframes statusPing {
                    0%, 100% { transform: scale(1); opacity: 0.5; }
                    50% { transform: scale(1.4); opacity: 0; }
                }

                .header-close {
                    width: 44px;
                    height: 44px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    border: none;
                    background: transparent;
                    color: var(--text-muted);
                    border-radius: 8px;
                    transition: all 0.2s;
                    line-height: 1;
                }

                .header-close:hover {
                    background: rgba(255,255,255,0.06);
                    color: var(--text-pri);
                    transform: rotate(90deg);
                }

                .header-close:active {
                    transform: rotate(90deg) scale(0.9);
                }

                .header-back {
                    width: 44px;
                    height: 44px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    border: none;
                    background: transparent;
                    color: var(--text-muted);
                    border-radius: 8px;
                    transition: all 0.2s;
                    line-height: 1;
                    flex-shrink: 0;
                }

                .header-back:hover {
                    background: rgba(255,255,255,0.06);
                    color: var(--text-pri);
                }

                /* ════════ HOME SLIDE (mode select, below header) ════════ */
                .home-view {
                    display: none;
                    flex: 1;
                    flex-direction: column;
                    align-items: stretch;
                    justify-content: center;
                    gap: 12px;
                    padding: 28px 24px;
                    background: var(--bg-primary);
                    overflow-y: auto;
                }
                .home-view.slide-out {
                    animation: homeOut 0.28s ease-in forwards;
                }
                .home-view.slide-in {
                    animation: homeIn 0.28s ease-out forwards;
                }
                @keyframes homeOut {
                    from { opacity: 1; transform: translateX(0); }
                    to   { opacity: 0; transform: translateX(-40px); }
                }
                @keyframes homeIn {
                    from { opacity: 0; transform: translateX(40px); }
                    to   { opacity: 1; transform: translateX(0); }
                }
                .home-avatar {
                    width: 56px;
                    height: 56px;
                    border-radius: 16px;
                    background: linear-gradient(135deg, var(--accent) 0%, var(--accent-hover) 100%);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 26px;
                    font-weight: 800;
                    color: #fff;
                    margin: 0 auto 4px;
                    overflow: hidden;
                }
                .home-avatar img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                    display: block;
                }
                .home-title {
                    margin: 0;
                    text-align: center;
                    font-size: 19px;
                    font-weight: 800;
                    color: var(--text-pri);
                }
                .home-tagline {
                    margin: 0 0 8px;
                    text-align: center;
                    font-size: 13px;
                    color: var(--text-sec);
                }
                .home-btn {
                    display: flex;
                    align-items: center;
                    gap: 14px;
                    width: 100%;
                    padding: 16px;
                    border: none;
                    border-radius: 16px;
                    background: var(--accent);
                    color: #fff;
                    cursor: pointer;
                    text-align: left;
                    font-family: 'Inter', system-ui, sans-serif;
                    box-shadow: 0 6px 24px var(--accent-glow);
                    transition: transform 0.15s, box-shadow 0.2s, opacity 0.2s;
                }
                .home-btn:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 10px 28px var(--accent-glow);
                    opacity: 0.95;
                }
                .home-btn:active {
                    transform: scale(0.98);
                }
                .home-btn-icon {
                    font-size: 26px;
                    line-height: 1;
                    flex-shrink: 0;
                }
                .home-btn-text {
                    display: flex;
                    flex-direction: column;
                    gap: 2px;
                    min-width: 0;
                }
                .home-btn-title {
                    font-size: 16px;
                    font-weight: 800;
                }
                .home-btn-desc {
                    font-size: 12px;
                    font-weight: 500;
                    opacity: 0.85;
                }


                .header-business {
                    font-size: 11.5px !important;
                    font-weight: 600;
                    color: var(--text-sec) !important;
                    opacity: 0.95;
                    margin-top: 2px !important;
                    display: block !important;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    max-width: 190px;
                    letter-spacing: 0.1px;
                }

                .header-business.marquee {
                    text-overflow: clip;
                }

                .header-business .marquee-track {
                    display: inline-flex;
                    white-space: nowrap;
                    will-change: transform;
                    animation: ds-header-marquee var(--marquee-duration, 14s) linear infinite;
                }

                .header-business .marquee-track > span {
                    padding-right: 32px;
                }

                @keyframes ds-header-marquee {
                    from { transform: translateX(0); }
                    to   { transform: translateX(-50%); }
                }

                /* ════════ MESSAGES ════════ */
                .messages {
                    flex: 1;
                    padding: 20px;
                    overflow-y: auto;
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                    background: var(--bg-primary);
                    scrollbar-width: thin;
                    scrollbar-color: var(--border) transparent;
                }

                .messages::-webkit-scrollbar { width: 4px; }
                .messages::-webkit-scrollbar-track { background: transparent; }
                .messages::-webkit-scrollbar-thumb {
                    background: var(--border);
                    border-radius: 4px;
                }

                /* Message bubbles */
                .message {
                    max-width: 82%;
                    padding: 10px 14px;
                    border-radius: 14px;
                    font-size: 13px;
                    line-height: 1.55;
                    word-wrap: break-word;
                    position: relative;
                    transition: transform 0.15s ease, box-shadow 0.15s ease;
                    animation: msgSlideIn 0.3s cubic-bezier(0.22, 0.61, 0.36, 1) both;
                }

                @keyframes msgSlideIn {
                    from { opacity: 0; transform: translateY(10px) scale(0.96); }
                    to   { opacity: 1; transform: translateY(0) scale(1); }
                }

                .message:hover {
                    transform: translateY(-1px);
                }

                .message:hover .msg-time {
                    opacity: 1;
                    transform: translateY(0);
                }

                .message.agent {
                    align-self: flex-start;
                    background: var(--msg-agent-bg);
                    color: var(--text-pri);
                    border: 1px solid var(--border);
                    border-bottom-left-radius: 4px;
                }

                .message.agent:hover {
                    border-color: var(--border-hover);
                    box-shadow: 0 2px 12px rgba(0,0,0,0.2);
                }

                .message.user {
                    align-self: flex-end;
                    background: var(--msg-user-bg);
                    color: white;
                    border-bottom-right-radius: 4px;
                    border: none;
                    box-shadow: 0 2px 8px color-mix(in srgb, var(--accent) 20%, transparent);
                }

                .message.user:hover {
                    box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 30%, transparent);
                }

                .message.system {
                    align-self: center;
                    background: var(--accent-soft);
                    color: var(--text-sec);
                    font-size: 12px;
                    text-align: center;
                    padding: 8px 16px;
                    max-width: 90%;
                    border: 1px solid color-mix(in srgb, var(--accent) 12%, transparent);
                    border-radius: 20px;
                }

                /* Timestamp */
                .msg-time {
                    display: block;
                    font-size: 10px;
                    color: var(--text-muted);
                    margin-top: 4px;
                    opacity: 0.8;
                    transform: translateY(0);
                    transition: all 0.2s ease;
                }

                .message.user .msg-time {
                    text-align: right;
                    color: rgba(255,255,255,0.7);
                }

                /* ════════ INPUT AREA ════════ */
                .input-area {
                    padding: 14px 16px;
                    border-top: 1px solid var(--border);
                    display: flex;
                    gap: 8px;
                    background: var(--bg-card);
                    align-items: center;
                }

                .qp-bar-wrap {
                    position: relative;
                    display: flex;
                    align-items: center;
                    box-sizing: border-box;
                    /* Outer padding lives here (NOT on the scroll area) so the
                       scrollbar/pills always start after the pinned switch group. */
                    padding: 10px 16px 4px;
                    background: var(--bg-card);
                }
                /* Pinned section-switch group. Lives OUTSIDE .quick-prompts-bar,
                   so it has no scroll container to bleed through — the earlier
                   sticky-inside-the-scroller approach kept letting a sliver of a
                   scrolled pill show at the left edge. */
                .qp-switch-slot {
                    display: none;
                    flex-shrink: 0;
                    align-items: center;
                    gap: 8px;
                    padding-right: 10px;
                }
                .qp-bar-wrap.qp-has-switch .qp-switch-slot { display: flex; }
                /* Scroll arrows for the suggestions bar (laptop/desktop have
                   no touch swipe and the bar hides its scrollbar). Static flex
                   siblings — never overlayed on the pills. */
                .qp-nav {
                    flex-shrink: 0;
                    align-self: center;
                    width: 32px;
                    height: 32px;
                    border-radius: 50%;
                    border: 1px solid var(--border);
                    background: var(--bg-card, #121212);
                    color: #fff;
                    font-size: 15px;
                    font-weight: 700;
                    line-height: 1;
                    padding: 0 0 2px;
                    cursor: pointer;
                    display: none;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.45);
                }
                .qp-nav:hover {
                    border-color: var(--accent);
                    color: var(--accent);
                }
                .qp-bar-wrap.qp-can-left > #qp-nav-left { display: block; margin: 0 8px 0 4px; }
                .qp-bar-wrap.qp-can-right > #qp-nav-right { display: block; margin: 0 0 0 8px; }
                /* ════════ QUICK PROMPTS BAR (Mando-style pills above input) ════════ */
                .quick-prompts-bar {
                    display: none;
                    flex: 1 1 auto;
                    min-width: 0;
                    gap: 8px;
                    padding: 0;
                    overflow-x: auto;
                    overflow-y: hidden;
                    background: var(--bg-card);
                    scrollbar-width: none;
                }
                .quick-prompts-bar::-webkit-scrollbar {
                    display: none;
                }
                .qp-pill {
                    flex-shrink: 0;
                    padding: 8px 16px;
                    border-radius: 20px;
                    border: 1px solid var(--border);
                    background: #000;
                    color: #fff;
                    font-size: 13px;
                    font-weight: 600;
                    font-family: 'Inter', system-ui, sans-serif;
                    cursor: pointer;
                    transition: all 0.2s;
                    white-space: nowrap;
                }
                .qp-pill:hover {
                    border-color: var(--accent);
                    box-shadow: 0 2px 12px var(--accent-glow);
                    transform: translateY(-1px);
                }
                .qp-pill:active {
                    transform: scale(0.96);
                }
                /* Mode-switch pills (Start Booking / Ask AI instead) must NOT
                   look like suggestion pills — users confused the two. Switch
                   pills are accent-outlined navigation controls, separated
                   from suggestion pills by a divider. */
                .qp-pill.qp-mode {
                    background: transparent;
                    border: 1.5px solid var(--accent);
                    color: var(--accent);
                }
                .qp-pill.qp-mode:hover {
                    background: var(--accent-glow);
                    box-shadow: none;
                    transform: translateY(-1px);
                }
                .qp-sep {
                    flex-shrink: 0;
                    width: 1px;
                    align-self: stretch;
                    margin: 4px 2px;
                    background: var(--border);
                }
                /* The switch-pill group is NOT inside the scroller — see
                   .qp-switch-slot. Kept as a rule so nothing inherits a
                   background/position that could let pills bleed through. */
                .qp-switch-wrap {
                    flex-shrink: 0;
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    background: transparent;
                }

                .input-area input {
                    flex: 1;
                    padding: 11px 14px;
                    border: 1px solid var(--border);
                    border-radius: 12px;
                    outline: none;
                    background: var(--bg-input);
                    color: var(--text-pri);
                    font-size: 13px;
                    font-family: 'Inter', system-ui, sans-serif;
                    transition: all 0.25s ease;
                }

                .input-area input::placeholder {
                    color: var(--text-muted);
                    transition: color 0.2s;
                }

                .input-area input:focus {
                    border-color: var(--accent);
                    box-shadow: 0 0 0 3px var(--accent-glow);
                    background: #111;
                }

                .input-area input:focus::placeholder {
                    color: var(--text-sec);
                }

                .input-area input:disabled {
                    opacity: 0.4;
                    cursor: not-allowed;
                }

                /* Send button */
                .send-btn {
                    width: 40px;
                    height: 40px;
                    background: var(--accent);
                    color: white;
                    border: none;
                    border-radius: 12px;
                    cursor: pointer;
                    font-family: 'Inter', system-ui, sans-serif;
                    transition: all 0.2s;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    flex-shrink: 0;
                    position: relative;
                    overflow: hidden;
                }

                .send-btn::before {
                    content: '';
                    position: absolute;
                    inset: 0;
                    border-radius: 12px;
                    background: linear-gradient(135deg, rgba(255,255,255,0.15) 0%, transparent 50%);
                    pointer-events: none;
                }

                .send-btn:hover {
                    background: var(--accent-hover);
                    box-shadow: 0 4px 16px var(--accent-glow);
                    transform: scale(1.05);
                }

                .send-btn:active {
                    transform: scale(0.92);
                    transition-duration: 0.08s;
                }

                .send-btn:disabled {
                    opacity: 0.3;
                    cursor: not-allowed;
                    box-shadow: none;
                    transform: none;
                }

                .send-btn .send-icon {
                    transition: transform 0.35s cubic-bezier(0.68, -0.55, 0.27, 1.55);
                }

                .send-btn.sending .send-icon {
                    transform: translate(20px, -20px) scale(0.3);
                }

                .widget-inline-validation {
                    display: none;
                    padding: 0 16px 10px;
                    font-size: 12px;
                    color: #f97316;
                    background: var(--bg-card);
                }

                .widget-skip-btn {
                    margin-left: 8px;
                    padding: 8px 12px;
                    border: 1px solid var(--border);
                    background: transparent;
                    color: var(--text-sec);
                    border-radius: 10px;
                    font-size: 12px;
                    cursor: pointer;
                }

                .widget-skip-btn:hover {
                    border-color: var(--accent);
                    color: var(--text-pri);
                }

                /* ════════ TYPING INDICATOR ════════ */
                .typing-dots {
                    display: flex;
                    gap: 5px;
                    align-items: center;
                    padding: 4px 2px;
                }

                .typing-dots span {
                    width: 7px;
                    height: 7px;
                    background: var(--accent);
                    border-radius: 50%;
                    animation: dotWave 1.4s ease-in-out infinite;
                }

                .typing-dots span:nth-child(1) { animation-delay: 0s; }
                .typing-dots span:nth-child(2) { animation-delay: 0.15s; }
                .typing-dots span:nth-child(3) { animation-delay: 0.3s; }

                @keyframes dotWave {
                    0%, 60%, 100% {
                        transform: translateY(0) scale(1);
                        opacity: 0.35;
                    }
                    30% {
                        transform: translateY(-8px) scale(1.15);
                        opacity: 1;
                    }
                }

                .typing-msg {
                    animation: msgSlideIn 0.3s ease both, typingPulse 2s ease-in-out infinite;
                }

                @keyframes typingPulse {
                    0%, 100% { border-color: var(--border); }
                    50% { border-color: color-mix(in srgb, var(--accent) 30%, transparent); }
                }

                /* ════════ POWERED-BY FOOTER ════════ */
                .powered-by {
                    text-align: center;
                    padding: 8px;
                    font-size: 10px;
                    color: var(--text-muted);
                    background: var(--bg-card);
                    border-top: 1px solid var(--border);
                    letter-spacing: 0.3px;
                }

                .powered-by a {
                    color: var(--accent);
                    text-decoration: none;
                    font-weight: 500;
                    transition: color 0.2s;
                }

                .powered-by a:hover {
                    color: var(--accent-hover);
                    text-decoration: underline;
                }

                /* ════════ SPECIAL CARDS ════════ */
                .otp-card, .summary-card, .confirmed-card, .field-input-card {
                    background: var(--bg-card);
                    border: 1px solid var(--border);
                    padding: 18px !important;
                    border-radius: 16px !important;
                    width: 95% !important;
                    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
                    margin: 10px 0;
                }

                .card-header {
                    font-weight: 700;
                    font-size: 14px;
                    color: var(--accent);
                    margin-bottom: 12px;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                }

                .field-description {
                    font-size: 12px;
                    color: var(--text-sec);
                    margin-bottom: 12px;
                }

                .widget-date-input {
                    width: 100%;
                    background: var(--bg-input);
                    border: 1px solid var(--border);
                    border-radius: 8px;
                    padding: 10px 12px;
                    color: var(--text-pri);
                    font-size: 14px;
                    outline: none;
                    margin-bottom: 10px;
                    color-scheme: dark;
                }
                .widget-date-input:focus {
                    border-color: var(--accent);
                }
                .widget-field-textinput {
                    width: 100%;
                    box-sizing: border-box;
                    background: var(--bg-input);
                    border: 1px solid var(--border);
                    border-radius: 8px;
                    padding: 10px 12px;
                    color: var(--text-pri);
                    font-size: 14px;
                    outline: none;
                    margin-bottom: 4px;
                    font-family: 'Inter', system-ui, sans-serif;
                }
                .widget-field-textinput:focus {
                    border-color: var(--accent);
                }

                .widget-field-btn {
                    display: block;
                    width: 100%;
                    padding: 10px;
                    border: none;
                    border-radius: 8px;
                    background: var(--accent);
                    color: white;
                    font-weight: 600;
                    font-size: 13px;
                    cursor: pointer;
                    transition: opacity 0.2s;
                }
                .widget-field-btn:hover { opacity: 0.85; }
                .widget-field-btn:disabled { opacity: 0.5; cursor: default; }
                .widget-secondary-btn {
                    margin-top: 8px;
                    background: transparent;
                    border: 1px solid var(--border);
                    color: var(--text-sec);
                }

                .widget-phone-row {
                    display: grid;
                    grid-template-columns: 1fr 2fr;
                    gap: 8px;
                    margin-bottom: 10px;
                }

                .widget-phone-country,
                .widget-phone-local {
                    width: 100%;
                    background: var(--bg-input);
                    border: 1px solid var(--border);
                    border-radius: 8px;
                    padding: 10px;
                    color: var(--text-pri);
                    font-size: 13px;
                }

                .widget-dept-select {
                    width: 100%;
                    box-sizing: border-box;
                    background: var(--bg-input);
                    border: 1px solid var(--border);
                    border-radius: 8px;
                    padding: 10px 12px;
                    color: var(--text-pri);
                    font-size: 14px;
                    outline: none;
                    margin-bottom: 4px;
                    font-family: 'Inter', system-ui, sans-serif;
                }
                .widget-dept-select:focus {
                    border-color: var(--accent);
                }

                /* Department listbox: native <select size> renders inline (no
                   OS popup), so the options can never escape the widget. */
                .widget-dept-select[size] {
                    overflow-y: auto;
                    scrollbar-width: thin;
                    padding: 4px;
                }
                .widget-dept-select[size] option {
                    padding: 9px 10px;
                    border-radius: 6px;
                }
                .widget-dept-select[size] option:checked {
                    background: var(--accent-glow);
                    color: var(--accent);
                    font-weight: 600;
                }

                .widget-field-actions {
                    margin-top: 8px;
                }

                .field-inline-error {
                    margin-top: 8px;
                    font-size: 12px;
                    color: #f97316;
                    line-height: 1.4;
                }

                .select-options-grid {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 8px;
                    margin-top: 8px;
                }

                .timezone-context-banner {
                    margin: 8px 0 10px;
                    padding: 8px;
                    border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
                    border-radius: 10px;
                    background: color-mix(in srgb, var(--accent) 8%, transparent);
                    display: flex;
                    flex-direction: column;
                    gap: 8px;
                }
                .timezone-context-text {
                    font-size: 12px;
                    color: #bfdbfe;
                    line-height: 1.4;
                }
                .timezone-override-row {
                    display: flex;
                    flex-direction: column;
                    gap: 4px;
                }
                .timezone-override-label {
                    font-size: 11px;
                    color: var(--text-sec);
                }
                .timezone-override-select {
                    min-height: 44px;
                    background: var(--bg-input);
                    border: 1px solid var(--border);
                    border-radius: 8px;
                    padding: 8px;
                    color: var(--text-pri);
                    font-size: 12px;
                }

                .select-option-btn {
                    padding: 8px 16px;
                    border-radius: 8px;
                    border: 1px solid var(--border);
                    background: var(--bg-input);
                    color: var(--text-pri);
                    font-size: 13px;
                    font-weight: 500;
                    cursor: pointer;
                    transition: all 0.2s;
                }
                .select-option-btn:hover {
                    border-color: var(--accent);
                    background: rgba(255,107,0,0.1);
                }
                .select-option-btn.selected {
                    background: var(--accent);
                    color: white;
                    border-color: var(--accent);
                }
                .select-option-btn:disabled {
                    opacity: 0.5;
                    cursor: default;
                }
                .select-option-btn .select-option-name {
                    display: block;
                    font-weight: 600;
                }
                .select-option-btn .select-option-desc {
                    display: block;
                    font-size: 11px;
                    font-weight: 400;
                    opacity: 0.7;
                    margin-top: 2px;
                }

                /* Time slot pills - green accent for available slots */
                .time-slot-btn {
                    border-radius: 20px;
                    padding: 10px 20px;
                    font-weight: 600;
                    border: 1px solid var(--success);
                    background: rgba(34, 197, 94, 0.1);
                    color: var(--success);
                    display: flex;
                    flex-direction: column;
                    align-items: flex-start;
                    gap: 2px;
                }
                .slot-local-time { font-size: 13px; line-height: 1.2; }
                .slot-org-time { font-size: 11px; line-height: 1.2; color: var(--text-sec); }
                .time-slot-btn:hover {
                    background: var(--success);
                    color: white;
                    border-color: var(--success);
                    box-shadow: 0 4px 12px rgba(34, 197, 94, 0.3);
                }
                .time-slot-btn.selected {
                    background: var(--success);
                    color: white;
                    border-color: var(--success);
                    box-shadow: 0 4px 16px rgba(34, 197, 94, 0.4);
                }
                .time-slot-btn:disabled {
                    opacity: 0.5;
                    cursor: not-allowed;
                    background: var(--bg-input);
                    color: var(--text-muted);
                    border-color: var(--border);
                    box-shadow: none;
                }
                .time-slot-btn.unavailable {
                    opacity: 0.55;
                    background: #27272a;
                    color: #a1a1aa;
                    border-color: #3f3f46;
                }

                .otp-input-group {
                    display: flex;
                    gap: 8px;
                    margin-top: 12px;
                    flex-direction: column;
                }

                .otp-input-group input {
                    width: 100%;
                    background: var(--bg-input);
                    border: 1px solid var(--border);
                    border-radius: 8px;
                    padding: 10px 12px;
                    color: var(--text-pri);
                    font-size: 14px;
                    letter-spacing: 2px;
                    font-weight: 600;
                    outline: none;
                    text-align: center;
                }
                .otp-input-group input:focus { border-color: var(--accent); }

                .otp-input-group button {
                    width: 100%;
                    padding: 10px 16px;
                    border-radius: 8px;
                    border: none;
                    background: var(--accent);
                    color: white;
                    font-weight: 600;
                    cursor: pointer;
                    transition: opacity 0.2s;
                }

                .otp-input-group input {
                    flex: 1;
                    background: var(--bg-input);
                    border: 1px solid var(--border);
                    border-radius: 8px;
                    padding: 10px;
                    color: white;
                    text-align: center;
                    font-size: 18px;
                    letter-spacing: 4px;
                    font-weight: 700;
                    outline: none;
                }

                .otp-input-group button, .card-actions button {
                    padding: 8px 16px;
                    border-radius: 8px;
                    border: none;
                    font-weight: 600;
                    cursor: pointer;
                    transition: all 0.2s;
                }

                #verify-otp-btn, .confirm-btn {
                    background: var(--accent);
                    color: white;
                }

                .edit-btn {
                    background: var(--border);
                    color: var(--text-sec);
                    margin-left: 8px;
                }

                .otp-resend-link {
                    display: block;
                    background: none;
                    border: none;
                    color: var(--accent);
                    font-size: 12px;
                    margin-top: 10px;
                    cursor: pointer;
                    text-decoration: underline;
                    padding: 0;
                    opacity: 0.8;
                    transition: opacity 0.2s;
                }
                .otp-resend-link:hover { opacity: 1; }
                .otp-resend-link:disabled { opacity: 0.4; cursor: not-allowed; }

                .otp-status {
                    font-size: 12px;
                    margin-top: 6px;
                    min-height: 16px;
                }

                .summary-content {
                    font-size: 13px;
                    line-height: 1.6;
                    color: var(--text-pri);
                    background: rgba(255,255,255,0.03);
                    padding: 12px;
                    border-radius: 8px;
                    margin-bottom: 15px;
                }

                .confirmed-card {
                    text-align: center;
                    border-color: var(--success);
                    background: rgba(34, 197, 94, 0.05);
                }

                .conf-icon {
                    font-size: 40px;
                    margin-bottom: 10px;
                    animation: confPop 0.6s cubic-bezier(0.175, 0.885, 0.32, 1.275);
                }

                @keyframes confPop {
                    0% { transform: scale(0) rotate(-20deg); }
                    70% { transform: scale(1.2) rotate(10deg); }
                    100% { transform: scale(1) rotate(0); }
                }

                .success-msg {
                    color: var(--success);
                    font-weight: 600;
                    text-align: center;
                    padding: 10px;
                }

                /* ════════ RESPONSIVE ════════ */
                @media (max-width: 480px) {
                    :host {
                        bottom: 16px;
                        right: 16px;
                    }
                    .chat-container {
                        position: fixed;
                        bottom: 0;
                        right: 0;
                        left: 0;
                        width: 100%;
                        height: 100vh;
                        height: 100dvh;
                        max-height: 100vh;
                        max-height: 100dvh;
                        border-radius: 0;
                        border: none;
                        z-index: 10000;
                    }
                    .chat-container.visible {
                        animation: mobileSlideIn 0.3s ease forwards;
                    }
                    .chat-container.hiding {
                        animation: mobileSlideOut 0.25s ease forwards;
                    }
                    .header {
                        padding: 16px 20px;
                        padding-top: max(16px, env(safe-area-inset-top));
                    }
                    .messages {
                        padding: 16px;
                    }
                    .input-area {
                        padding: 12px 16px;
                        padding-bottom: max(12px, env(safe-area-inset-bottom));
                    }
                    .send-btn {
                        width: 44px;
                        height: 44px;
                    }
                    .input-area input {
                        font-size: 16px; /* prevents iOS zoom on focus */
                        padding: 12px 14px;
                    }
                    .widget-launcher {
                        width: 52px;
                        height: 52px;
                    }
                    .otp-card, .summary-card, .confirmed-card, .field-input-card {
                        width: 100% !important;
                    }
                }

                @keyframes mobileSlideIn {
                    from { opacity: 0; transform: translateY(100%); }
                    to   { opacity: 1; transform: translateY(0); }
                }
                @keyframes mobileSlideOut {
                    from { opacity: 1; transform: translateY(0); }
                    to   { opacity: 0; transform: translateY(100%); }
                }

                /* ════════ MARKDOWN STYLES ════════ */
                .message strong {
                    font-weight: 700;
                    color: var(--text-pri);
                }
                .message em {
                    font-style: italic;
                    color: var(--text-sec);
                }
                .message .md-bullet {
                    display: block;
                    padding-left: 16px;
                    position: relative;
                    margin: 2px 0;
                }
                .message .md-bullet::before {
                    content: '•';
                    position: absolute;
                    left: 4px;
                    color: var(--accent);
                    font-weight: 700;
                }
                .message.user strong {
                    color: white;
                }

                /* ════════ ACCESSIBILITY ════════ */
                .sr-only {
                    position: absolute;
                    width: 1px;
                    height: 1px;
                    padding: 0;
                    margin: -1px;
                    overflow: hidden;
                    clip: rect(0, 0, 0, 0);
                    white-space: nowrap;
                    border: 0;
                }

                /* ════════ CARD ACTIONS ════════ */
                .card-actions {
                    display: flex;
                    gap: 8px;
                    margin-top: 4px;
                }
                .card-actions button {
                    flex: 1;
                    min-height: 40px;
                    font-size: 13px;
                }
                .confirm-btn:hover {
                    opacity: 0.85;
                    box-shadow: 0 2px 12px var(--accent-glow);
                }
                .edit-btn:hover {
                    background: var(--border-hover);
                    color: var(--text-pri);
                }

                .conf-footer {
                    margin-top: 12px;
                    font-size: 12px;
                    color: var(--text-sec);
                }

                /* ── HUMAN AGENT MESSAGE (Phase 6/9) ───────────────── */
                .message.human-agent {
                    background: linear-gradient(135deg, rgba(245, 158, 11, 0.12) 0%, rgba(245, 158, 11, 0.04) 100%);
                    border-left: 3px solid #f59e0b;
                    border-radius: 10px;
                    padding: 10px 12px;
                    margin: 6px 0;
                    max-width: 88%;
                    align-self: flex-start;
                    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
                }
                .message.human-agent .human-label {
                    font-size: 11px;
                    font-weight: 700;
                    color: #b45309;
                    margin-bottom: 4px;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                    display: flex;
                    align-items: center;
                    gap: 4px;
                }
                .message.human-agent .human-label::before {
                    content: "👨‍💼";
                    font-size: 12px;
                }
                .message.human-agent .human-text {
                    font-size: 14px;
                    line-height: 1.45;
                    color: var(--text-pri);
                    white-space: pre-wrap;
                    word-wrap: break-word;
                }
                .message.human-agent .msg-time {
                    display: block;
                    margin-top: 6px;
                    font-size: 10px;
                    color: var(--text-muted);
                    text-align: right;
                }
            `;
    }
  }

  customElements.define("dotstark-booking-widget", BookingWidget);

  // Build stamp: proves EXACTLY which bundle a page runs (console + window).
  // When reporting a visual bug, include this tag so we never chase ghosts
  // in a stale cached bundle again.
  try {
    window._dotstarkWidgetBuild = "2026-10-12-qpslot";
    console.info("[DotStark] widget build:", window._dotstarkWidgetBuild);
  } catch (_) {}

  const el = document.createElement("dotstark-booking-widget");
  document.body.appendChild(el);
})();
