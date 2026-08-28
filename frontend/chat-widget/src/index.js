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

    async connectedCallback() {
      try {
        const sessionKey = this._sessionStorageKey();
        if (CONFIG.forceNewSession === true) {
          sessionStorage.removeItem(sessionKey);
          sessionStorage.removeItem("dotstark_session_id");
          this.sessionId = null;
        } else {
          this.sessionId = sessionStorage.getItem(sessionKey) || null;
        }
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
        }
      } catch (e) {
        /* config fetch failed — use defaults */
      }
    }

    async _loadHistory(sinceTimestamp = null) {
      if (!this.sessionId || !CONFIG.widgetToken) return false;
      try {
        const base = CONFIG.apiBase.replace(/\/api\/v1\/?$/, "") + "/api/v1";
        let url = `${base}/widget/history?session_id=${encodeURIComponent(this.sessionId)}&widget_token=${encodeURIComponent(CONFIG.widgetToken)}`;
        if (sinceTimestamp) {
          url += `&since=${encodeURIComponent(sinceTimestamp)}`;
        }
        const res = await fetch(url);
        if (!res.ok) return false;
        const messages = await res.json();
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
      switch (event.type) {
        case "connected":
          // Server confirmed registration; nothing else to do.
          break;
        case "human_joined":
          this._updateHumanStatus(event.admin_name || "Support agent", true);
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
                            <h3 id="agent-name-title">${this._agentConfig?.agent_name || "Booking Assistant"}</h3>
                            ${(() => { const c = this._agentConfig || {}; const b = [c.business_name, c.business_tagline].filter(Boolean).join(" - "); if (!b) return ""; const esc = b.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); const dur = Math.max(8, Math.min(30, Math.round(b.length * 0.35))); return `<p class="header-business marquee" id="header-business" style="--marquee-duration:${dur}s"><span class="marquee-track"><span>${esc}</span><span>${esc}</span></span></p>`; })()}
                            <p><span class="status-dot"></span> <span id="status-text">Online \u2014 Ready to help</span></p>
                        </div>
                        <button class="header-close" id="close-btn" title="Close chat">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                    </div>
                    <div class="messages" id="message-list" role="log" aria-label="Chat messages" aria-live="polite"></div>
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

      // Add initial greeting OR restore history from a previous session.
      // When a session exists, also start polling so the widget picks up
      // human takeover events and human-sent messages in real time.
      if (this.sessionId) {
        this._loadHistory().then((restored) => {
          if (!restored) {
            const greeting =
              this._agentConfig?.greeting_message ||
              "Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?";
            this._appendBubble("agent", greeting);
          }
          // Phase 4: open WebSocket first (real-time channel); polling acts
          // as a safety net that auto-disables when WS is connected.
          this._connectWebSocket();
          this._startPolling();
        });
      } else {
        const greeting =
          this._agentConfig?.greeting_message ||
          "Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?";
        this._appendBubble("agent", greeting);
      }

      // Apply brand color if configured
      const brandColor = this._agentConfig?.brand_color || CONFIG.primaryColor;
      if (brandColor) {
        this.style.setProperty("--brand-color", brandColor);
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
      return `${CONFIG.apiBase}/widget/availability?${params.toString()}`;
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
        setTimeout(() => { this._input.focus(); this._applyHeaderMarquee(); }, 200);
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

      try {
        const payload = { message: text };
        if (this.sessionId) payload.session_id = this.sessionId;
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
        if (data.session_id) {
          this.sessionId = data.session_id;
          try {
            sessionStorage.setItem(this._sessionStorageKey(), data.session_id);
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
        const rendersCardOnly =
          directive === "REQUEST_CONFIRMATION" ||
          directive === "BOOKING_SUMMARY_CARD" ||
          directive === "BOOKING_CONFIRMED_CARD";

        thinkingEl.remove();

        if (!rendersCardOnly) {
          if (agentMessage) {
            this._appendBubble("agent", agentMessage);
            this._pendingRefreshSkips.push({ role: "agent", content: agentMessage });
          } else {
            this._appendBubble(
              "agent",
              "I received your message, but something went wrong with the reply.",
            );
          }
        }

        const fieldMeta = data.action?.field_meta;
        this._activeFieldMeta = fieldMeta || null;
        this._setInlineValidation(fieldMeta?.validation_error || "");

        if (directive === "REQUIRE_DATE_PICKER") {
          this._appendDatePicker(fieldMeta);
        } else if (directive === "REQUIRE_TIME_SLOT_GRID") {
          await this._appendTimeSlotGrid(fieldMeta);
        } else if (directive === "REQUIRE_PHONE_INPUT") {
          this._appendPhoneInput(fieldMeta);
        } else if (directive === "REQUIRE_EMAIL_INPUT") {
          this._clearDirectiveCards();
          this._updateInputMode(
            "email",
            fieldMeta?.placeholder || "Enter email address...",
          );
          this._renderSkipAction(fieldMeta);
        } else if (directive === "REQUIRE_NUMBER_INPUT") {
          this._clearDirectiveCards();
          this._updateInputMode(
            "number",
            fieldMeta?.placeholder || "Enter a number...",
          );
          this._renderSkipAction(fieldMeta);
        } else if (directive === "REQUIRE_SELECT") {
          this._appendSelectInput(fieldMeta);
        } else if (directive === "REQUIRE_TEXTAREA") {
          this._clearDirectiveCards();
          this._appendSystem(
            `${fieldMeta?.icon || "📄"} ${fieldMeta?.description || "Please provide details."}`,
          );
          this._renderSkipAction(fieldMeta);
        } else if (directive === "TEXT_INPUT" && fieldMeta) {
          this._clearDirectiveCards();
          this._updateInputMode(
            "text",
            fieldMeta.placeholder || "Type a message...",
          );
          this._renderSkipAction(fieldMeta);
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
          // Human agent has taken over - show notification and start polling
          this._clearDirectiveCards();
          this._appendSystem("👨‍💼 Support agent joined the chat");
          this._updateInputMode("text", "Type a message...");
          this._clearSkipAction();
          this._humanActive = true;
          this._startPolling();
        } else {
          // Reset input to default mode for unknown directives
          this._clearDirectiveCards();
          this._updateInputMode("text", "Type a message...");
          this._clearSkipAction();
        }
      } catch (err) {
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
        this._input.focus();
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
        { code: "US", dial: "+1" },
        { code: "GB", dial: "+44" },
        { code: "IN", dial: "+91" },
        { code: "AU", dial: "+61" },
        { code: "CA", dial: "+1" },
      ];

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
      localInput.placeholder = fieldMeta?.placeholder || "Enter local phone number";
      localInput.value = localNumber;

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
      if (this.selectedDate) {
        input.value = this.selectedDate;
      } else {
        input.value = input.min;
      }
      div.appendChild(input);

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

    async _appendTimeSlotGrid(fieldMeta) {
      this._clearDirectiveCards();
      this._clearSkipAction();
      if (fieldMeta?.slot_unavailable) {
        this.selectedSlotId = null;
      }
      const selectedDate = this._extractLatestIsoDate();
      this.selectedDate = selectedDate || this.selectedDate;
      let options = [];
      let availabilityStatus = null;
      if (selectedDate) {
        const availability = await this._fetchAvailabilityOptions(selectedDate);
        options = availability.options;
        availabilityStatus = availability.status;
      }
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

      options.forEach((opt) => {
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
        optionsContainer.appendChild(btn);
      });

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
        };
      } catch (e) {
        return { options: [], status: null };
      }
    }

    _appendOTPInterface(fieldMeta = {}) {
      const div = document.createElement("div");
      div.className = "message agent otp-card";
      div.setAttribute("role", "form");
      div.setAttribute("aria-label", "Email verification");

      const header = document.createElement("div");
      header.className = "card-header";
      header.textContent = "Verification Required";

      const desc = document.createElement("p");
      desc.textContent =
        "Please enter the email verification code to continue booking.";

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

      let resendCount = Math.max(0, policy.resendCap - policy.resendsLeft);
      let cooldownRemaining = Math.max(0, policy.cooldownSeconds);
      const updateResendUi = () => {
        const resendsLeft = Math.max(0, policy.resendCap - resendCount);
        const cooldownText =
          cooldownRemaining > 0
            ? `Resend available in ${cooldownRemaining}s`
            : "You can resend now";
        statusMsg.textContent = `${cooldownText} • ${resendsLeft} resends left`;
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
        console.log("[DotStark] Verify clicked. code length:", code.length, "session:", this.sessionId);
        if (code.length < 4) {
          statusMsg.textContent = "Please enter the code sent to your email.";
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
          const res = await fetch(
            `${CONFIG.apiBase}/auth/security/otp/verify`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Widget-Token": CONFIG.widgetToken || "",
                "X-Requested-With": "XMLHttpRequest",
              },
              body: JSON.stringify({ session_id: this.sessionId, code }),
            },
          );

          if (res.ok) {
            div.textContent = "";
            const success = document.createElement("div");
            success.className = "success-msg";
            success.textContent = "✅ Email Verified Successfully!";
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
        resendLink.disabled = true;
        resendLink.textContent = "Sending...";
        statusMsg.textContent = "";

        try {
          await this._handleSendManual("resend");
          resendCount++;
          cooldownRemaining = policy.cooldownSeconds;
          statusMsg.textContent = `Code resent! (${Math.max(0, policy.resendCap - resendCount)} resends left)`;
          statusMsg.style.color = "#22c55e";
        } catch (e) {
          statusMsg.textContent = "Failed to resend. Please try again.";
          statusMsg.style.color = "#ef4444";
        }

        updateResendUi();
        resendLink.textContent = "Didn't receive it? Resend code";
      };

      recoverBtn.onclick = () => {
        this._input.disabled = false;
        this._sendBtn.disabled = false;
        this._input.placeholder = "Enter the correct email address...";
        this._input.style.opacity = "1";
        this._input.focus();
        statusMsg.textContent = "No problem — provide the correct email and we’ll resend there.";
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

    _startNewSession() {
      // Clear session, reset chat, start fresh
      this.sessionId = null;
      sessionStorage.removeItem(this._sessionStorageKey());
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
      this._enableInput("Type a message...");
      this._appendBubble(
        "agent",
        "Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?",
      );
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

    _scrollToBottom() {
      requestAnimationFrame(() => {
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
                    width: 32px;
                    height: 32px;
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
                        height: 100%;
                        max-height: 100vh;
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

  const el = document.createElement("dotstark-booking-widget");
  document.body.appendChild(el);
})();
