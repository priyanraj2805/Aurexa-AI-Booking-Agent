(function(){"use strict";(function(){console.warn("[DotStark] widget.js loaded, build:","2026-09-26-whatsapp-only-otp","href:",window.location.href),console.warn("[DotStark] If you see no logs after Verify, ensure console filter is set to All/Verbose, not Errors only");function C(w,e){try{const t=typeof m<"u"&&m.apiBase?m.apiBase:"/api/v1",s=typeof m<"u"&&m.widgetToken&&m.widgetToken||"";let n="";try{n=window._dotstarkLastSessionId||""}catch{}try{const l=document.querySelector("booking-widget");l&&l.sessionId&&(n=l.sessionId)}catch{}const i={event:String(w||"unknown").slice(0,40),session_id:String(n||"").slice(0,36),channel:"phone",detail:String(e||"").slice(0,200),phone_masked:""};fetch(`${t}/widget/client-log`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":s,"X-Requested-With":"XMLHttpRequest"},body:JSON.stringify(i),keepalive:!0}).catch(()=>{})}catch{}}function S(w){try{return w&&w.src?`${new URL(w.src,window.location.href).origin}/api/v1`:`${window.location.origin}/api/v1`}catch{return"/api/v1"}}function I(){const w=document.querySelectorAll("script[data-widget-token]");if(!w.length){const s=document.querySelectorAll("script[data-api-key]");if(!s.length)return null;const n=s[s.length-1],i=S(n);return{widgetToken:n.getAttribute("data-api-key"),apiBase:n.getAttribute("data-api-base")||i,primaryColor:n.getAttribute("data-color")||null}}const e=w[w.length-1],t=S(e);return{widgetToken:e.getAttribute("data-widget-token"),apiBase:e.getAttribute("data-api-base")||t,primaryColor:e.getAttribute("data-color")||null}}const m=I()||window.DotStarkBookingConfig||{widgetToken:null,apiBase:S(document.currentScript)};class T extends HTMLElement{constructor(){super(),this.attachShadow({mode:"open"}),this.isOpen=!1,this.sessionId=null,this.isLoading=!1,this._messages=[],this._agentConfig=null,this.selectedUnitId=null,this.selectedProviderId=null,this._catalogPickerShown=!1,this.userTimezone=this._detectBrowserTimezone(),this.timezoneSource="detected",this.selectedSlotId=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this.orgTimezone=null,this.selectedDate=null,this._directiveDrafts={},this._activeFieldMeta=null,this._skipToken="__DOTSTARK_SKIP__",this._lastMessageTimestamp=null,this._pollInterval=null,this._humanActive=!1,this._chatMode=null,this._lastDirective=null,this._logoSrc=null,this._ws=null,this._wsReconnectTimer=null,this._wsReconnectAttempts=0,this._wsConnected=!1,this._renderedMessageIds=new Set,this._pendingRefreshSkips=[]}_sessionStorageKey(){const e=(m.widgetToken||"anonymous").trim()||"anonymous",t=(m.apiBase||"").trim()||"default";return`dotstark_session_id:${e}:${t}`}_modeStorageKey(){return`${this._sessionStorageKey()}:mode`}_sessionKeyFor(e){return`${this._sessionStorageKey()}:${e==="ask"?"ask":"booking"}`}_loadChatMode(){try{const e=sessionStorage.getItem(this._modeStorageKey());this._chatMode=e==="ask"||e==="booking"?e:null}catch{this._chatMode=null}}_saveChatMode(){try{this._chatMode?sessionStorage.setItem(this._modeStorageKey(),this._chatMode):sessionStorage.removeItem(this._modeStorageKey())}catch{}}async connectedCallback(){try{const e=this._sessionStorageKey();if(m.forceNewSession===!0){sessionStorage.removeItem(e),sessionStorage.removeItem("dotstark_session_id");try{sessionStorage.removeItem(this._sessionKeyFor("ask"))}catch{}try{sessionStorage.removeItem(this._sessionKeyFor("booking"))}catch{}this.sessionId=null}else{const n=sessionStorage.getItem(this._modeStorageKey()),i=n==="ask"||n==="booking"?n:null;this.sessionId=i&&sessionStorage.getItem(this._sessionKeyFor(i))||sessionStorage.getItem(e)||null}try{const n=sessionStorage.getItem(`${e}:mode`);this._chatMode=n==="ask"||n==="booking"?n:null}catch{}const t=sessionStorage.getItem("dotstark_user_timezone"),s=sessionStorage.getItem("dotstark_timezone_source");t&&(this.userTimezone=t,this.timezoneSource=s==="user_selected"?"user_selected":"detected")}catch{}await this._fetchAgentConfig(),this._buildShell(),this._startConfigPolling();try{this._tzVisibilityHandler=()=>{document.visibilityState==="visible"&&this._refreshTimezone(!0)},document.addEventListener("visibilitychange",this._tzVisibilityHandler),this._tzFocusHandler=()=>this._refreshTimezone(!0),window.addEventListener("focus",this._tzFocusHandler)}catch{}}disconnectedCallback(){try{this._configPollInterval&&clearInterval(this._configPollInterval),this._configPollInterval=null,this._tzVisibilityHandler&&(document.removeEventListener("visibilitychange",this._tzVisibilityHandler),this._tzVisibilityHandler=null),this._tzFocusHandler&&(window.removeEventListener("focus",this._tzFocusHandler),this._tzFocusHandler=null)}catch{}}_startConfigPolling(){try{this._configPollInterval&&clearInterval(this._configPollInterval),this._configPollInterval=setInterval(async()=>{var i,l,a,o;const e=(i=this._agentConfig)==null?void 0:i.meeting_mode,t=JSON.stringify(((l=this._agentConfig)==null?void 0:l.available_meeting_types)||[]);await this._fetchAgentConfig();const s=(a=this._agentConfig)==null?void 0:a.meeting_mode,n=JSON.stringify(((o=this._agentConfig)==null?void 0:o.available_meeting_types)||[]);(e!==s||t!==n)&&(this._updateMeetingModeBadge(),console.log("[DotStark] meeting mode updated via poll:",e,"->",s))},3e4)}catch{}}_updateMeetingModeBadge(){var e;try{const t=this.shadowRoot&&this.shadowRoot.getElementById("meeting-mode-badge");if(!t)return;const s=this._agentConfig||{},n=(s.meeting_mode||"").toString(),i=Array.isArray(s.available_meeting_types)?s.available_meeting_types:[];let l="",a=!0;if(n==="ONLINE_ONLY"||i.length===1&&i[0]==="ONLINE"?l="Online • "+(s.meeting_provider_default&&s.meeting_provider_default!=="NONE"?"Google Meet":"Online"):n==="OFFLINE_ONLY"||i.length===1&&i[0]==="OFFLINE"?l="Offline • In-person":n==="HYBRID"||i.length===2?l="Hybrid • Online + Offline":a=!1,a&&l){t.textContent=l,t.style.display="inline-block";try{const o=((e=this._agentConfig)==null?void 0:e.brand_color)||typeof m<"u"&&m.primaryColor||this.style.getPropertyValue("--brand-color")||"#3B82F6";t.style.color="white",t.style.borderColor=o||"#3B82F6",t.style.borderWidth="1.5px",t.style.borderStyle="solid",t.style.background="rgba(255,255,255,0.10)"}catch{t.style.color="white"}}else t.style.display="none"}catch{}}async _fetchAgentConfig(){if(m.widgetToken)try{const e=m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1",t=await fetch(`${e}/widget/config?widget_token=${encodeURIComponent(m.widgetToken)}`);t.ok&&(this._agentConfig=await t.json(),this._updateMeetingModeBadge())}catch{}}async _loadHistory(e=null){if(!this.sessionId||!m.widgetToken)return!1;const t=this.sessionId;try{let n=`${m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1"}/widget/history?session_id=${encodeURIComponent(this.sessionId)}&widget_token=${encodeURIComponent(m.widgetToken)}`;e&&(n+=`&since=${encodeURIComponent(e)}`);const i=await fetch(n);if(!i.ok)return!1;const l=await i.json();if(this.sessionId!==t||!l||l.length===0)return!1;for(const a of l){const o=a.metadata&&a.metadata.human_joined,r=a.metadata&&a.metadata.human_left,d=a.role==="agent"?this._resolveAssistantText(a.content):String(a.content||"");if(!o&&!r&&d){if(a.role==="agent"&&a.metadata&&a.metadata.directive){a.id&&this._renderedMessageIds.add(a.id);continue}const p=!!(a.id&&this._renderedMessageIds.has(a.id)),c=this._pendingRefreshSkips.some(g=>g.role===a.role&&g.content===d);if(!p&&!c){const g=a.sender_type==="human"?{admin_name:a.human_agent_name}:{};a.id&&this._renderedMessageIds.add(a.id),this._appendBubble(a.role,d,g)}}if(a.created_at&&(this._lastMessageTimestamp=a.created_at),a.metadata&&a.metadata.human_left&&this._onHumanLeft(),a.metadata&&a.metadata.human_joined){const p=a.metadata.admin_name||"Support agent";this._updateHumanStatus(p,!0),this._startFastPolling()}a.sender_type==="human"&&this._startFastPolling()}return this._pendingRefreshSkips=[],!0}catch{return!1}}_startPolling(){if(this._pollInterval)return;const e=this._wsConnected?3e4:this._humanActive?3e3:1e4;this._pollInterval=setInterval(()=>{this.isOpen&&this.sessionId&&this._loadHistory(this._lastMessageTimestamp)},e)}_stopPolling(e=!1){this._pollInterval&&(clearInterval(this._pollInterval),this._pollInterval=null),e||(this._humanActive=!1)}_startFastPolling(){this._humanActive||(this._humanActive=!0,this._stopPolling(!0),this._startPolling())}_onHumanLeft(){this._updateHumanStatus(null,!1),this._stopPolling()}_updateHumanStatus(e,t){const s=this.shadowRoot.getElementById("status-text"),n=this.shadowRoot.querySelector(".status-dot");s&&(t&&e?(s.textContent=`👨‍💼 ${e} is now assisting you`,n&&(n.style.background="#f59e0b")):(s.textContent="Online — Ready to help",n&&(n.style.background="#22c55e")))}_wsBaseUrl(){const e=(m.apiBase||"").replace(/\/+$/,"");return e.startsWith("https://")?"wss://"+e.slice(8):e.startsWith("http://")?"ws://"+e.slice(7):e}_connectWebSocket(){if(!(!this.sessionId||!m.widgetToken)&&!(this._ws&&this._ws.readyState<=1))try{const e=`${this._wsBaseUrl()}/ws/widget?widget_token=${encodeURIComponent(m.widgetToken)}&session_id=${encodeURIComponent(this.sessionId)}`,t=new WebSocket(e);this._ws=t,t.onopen=()=>{this._wsConnected=!0,this._wsReconnectAttempts=0,this._pollInterval&&(this._stopPolling(!0),this._startPolling())},t.onmessage=s=>{let n;try{n=JSON.parse(s.data)}catch{return}this._handleWsEvent(n)},t.onclose=()=>{this._wsConnected=!1,this._ws=null;const s=Math.min(3e4,1e3*Math.pow(2,this._wsReconnectAttempts));this._wsReconnectAttempts++,this._wsReconnectTimer&&clearTimeout(this._wsReconnectTimer),this._wsReconnectTimer=setTimeout(()=>this._connectWebSocket(),s),this._pollInterval&&(this._stopPolling(!0),this._startPolling())},t.onerror=()=>{}}catch{this._wsConnected=!1,this._wsReconnectTimer&&clearTimeout(this._wsReconnectTimer),this._wsReconnectTimer=setTimeout(()=>this._connectWebSocket(),3e3)}}_handleWsEvent(e){try{if(e&&e.session_id&&this.sessionId&&e.session_id!==this.sessionId)return}catch{}switch(e.type){case"connected":break;case"human_joined":this._updateHumanStatus(e.admin_name||"Support agent",!0);try{this._clearQuickPrompts()}catch{}try{this._setComposerVisible(!0)}catch{}this._startFastPolling(),this._humanJoinedShownFor||(this._humanJoinedShownFor=e.session_id,this._appendSystem(`👨‍💼 ${e.admin_name||"Support agent"} joined the chat`));break;case"human_left":this._updateHumanStatus(null,!1),this._humanJoinedShownFor=null,this._appendSystem("👨‍💼 Support agent left. AI assistant resumed."),this._stopPolling();try{this._applyComposerRule()}catch{}this._startPolling();break;case"human_message":if(e.message_id&&this._renderedMessageIds.has(e.message_id))return;this._appendHumanBubble(e),e.message_id&&(this._renderedMessageIds.add(e.message_id),this._lastMessageTimestamp=e.created_at||this._lastMessageTimestamp);break}}_buildShell(){var i,l,a;const e=((i=this._agentConfig)==null?void 0:i.logo_url)||null;let t=null;if(e)try{t=e.startsWith("http")?e:new URL(e,m.apiBase).href}catch{t=e}this._logoSrc=t||null;const s=t?`<img src="${t}" alt="logo" style="width:100%;height:100%;object-fit:cover;border-radius:12px;display:block;" onerror="this.style.display='none';this.nextElementSibling&& (this.nextElementSibling.style.display='block')"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" style="display:none"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10H5a2 2 0 0 0-2 2v1a7 7 0 0 0 14 0v-1a2 2 0 0 0-2-2z"/><line x1="12" y1="18" x2="12" y2="22"/></svg>`:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10H5a2 2 0 0 0-2 2v1a7 7 0 0 0 14 0v-1a2 2 0 0 0-2-2z"/><line x1="12" y1="18" x2="12" y2="22"/></svg>';this.shadowRoot.innerHTML=`
                <style>${this._css()}</style>

                <div class="chat-container" id="chat-box" role="dialog" aria-label="Booking Assistant Chat" aria-hidden="true">
                    <div class="header">
                        <div class="header-avatar" id="avatar" aria-hidden="true">
                            ${s}
                        </div>
                        <div class="header-info">
                            <div style="display:flex; align-items:center; gap:8px; min-width:0;">
                                <h3 id="agent-name-title" style="margin:0;">${((l=this._agentConfig)==null?void 0:l.agent_name)||"Booking Assistant"}</h3>
                                <span id="meeting-mode-badge" class="meeting-mode-badge" style="display:none; font-size:10px; font-weight:700; padding:2px 7px; border-radius:20px; background:rgba(255,255,255,0.10); border:1.5px solid var(--brand-color, #3B82F6); color:white; white-space:nowrap; flex-shrink:0;"></span>
                            </div>
                            ${(()=>{const o=this._agentConfig||{},r=[o.business_name,o.business_tagline].filter(Boolean).join(" - ");if(!r)return"";const d=r.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");return`<p class="header-business marquee" id="header-business" style="--marquee-duration:${Math.max(8,Math.min(30,Math.round(r.length*.35)))}s"><span class="marquee-track"><span>${d}</span><span>${d}</span></span></p>`})()}
                            <p><span class="status-dot"></span> <span id="status-text">Online — Ready to help</span></p>
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
            `,this._launcher=this.shadowRoot.getElementById("launcher"),this._chatBox=this.shadowRoot.getElementById("chat-box"),this._input=this.shadowRoot.getElementById("user-input"),this._sendBtn=this.shadowRoot.getElementById("send-trigger"),this._msgList=this.shadowRoot.getElementById("message-list"),this._inputArea=this.shadowRoot.querySelector(".input-area"),this._qpBar=this.shadowRoot.getElementById("quick-prompts-bar");try{this._bindQpNav()}catch{}this._homeView=this.shadowRoot.getElementById("home-view"),this._backBtn=this.shadowRoot.getElementById("back-btn"),this._canGoHome=!1,this._inlineValidation=this.shadowRoot.getElementById("widget-inline-validation"),this._launcher.onclick=()=>this._toggle(),this._launcher.onkeydown=o=>{(o.key==="Enter"||o.key===" ")&&(o.preventDefault(),this._toggle())},this.shadowRoot.getElementById("close-btn").onclick=()=>this._toggle(),this._backBtn&&(this._backBtn.onclick=()=>this._showHomeView()),this.shadowRoot.getElementById("home-ask")&&(this.shadowRoot.getElementById("home-ask").onclick=()=>this._switchSection("ask")),this.shadowRoot.getElementById("home-book")&&(this.shadowRoot.getElementById("home-book").onclick=()=>this._switchSection("booking")),this._sendBtn.onclick=()=>this._handleSend(),this._input.onkeydown=o=>{o.key==="Enter"&&!o.shiftKey&&(o.preventDefault(),this._handleSend()),o.key==="Escape"&&this._toggle()},this._chatBox.onkeydown=o=>{o.key==="Escape"&&this.isOpen&&this._toggle()};try{this._fillHomeView()}catch{}if(this.sessionId)this._loadHistory().then(o=>{if(o){this._canGoHome=!1;try{this._updateBackBtn()}catch{}try{this._applyComposerRule()}catch{}if(this._chatMode==="booking")try{this._resumePendingCard()}catch{}}else{this._canGoHome=!0;try{this._showHomeView()}catch{}try{this._setComposerVisible(!1)}catch{}}this._connectWebSocket(),this._startPolling()});else{this._canGoHome=!0;try{this._showHomeView()}catch{}try{this._setComposerVisible(!1)}catch{}}const n=((a=this._agentConfig)==null?void 0:a.brand_color)||m.primaryColor;n&&this.style.setProperty("--brand-color",n),this.style.setProperty("--primary",n||"#ea580c");try{this._applyWidgetTheme()}catch{}this._updateMeetingModeBadge()}_applyWidgetTheme(){const e=this._agentConfig&&this._agentConfig.ui_theme||{},t=e.position||"bottom-right",s=Math.max(0,Math.min(28,Number(e.border_radius??16)||16)),n=e.dark_mode!==!1;t==="bottom-left"?(this.style.setProperty("right","auto"),this.style.setProperty("left","24px")):t==="center"?(this.style.setProperty("right","auto"),this.style.setProperty("left","50%"),this.style.setProperty("transform","translateX(-50%)")):(this.style.setProperty("left","auto"),this.style.setProperty("right","24px"),this.style.setProperty("transform",""));try{const i=this.shadowRoot&&this.shadowRoot.getElementById("chat-box");i&&(i.style.borderRadius=`${s+4}px`,t==="bottom-left"?(i.style.right="auto",i.style.left="0",i.style.transformOrigin="bottom left"):t==="center"&&(i.style.right="auto",i.style.left="50%",i.style.transform="translateX(-50%)",i.style.transformOrigin="bottom center"));const l=this.shadowRoot&&this.shadowRoot.getElementById("launcher");l&&(l.style.borderRadius=`${s}px`)}catch{}n||(this.style.setProperty("--bg-primary","#ffffff"),this.style.setProperty("--bg-secondary","#f4f4f5"),this.style.setProperty("--border","#e4e4e7"))}_detectBrowserTimezone(){try{return Intl.DateTimeFormat().resolvedOptions().timeZone||null}catch{return null}}_persistTimezonePreference(){try{this.userTimezone?(sessionStorage.setItem("dotstark_user_timezone",this.userTimezone),sessionStorage.setItem("dotstark_timezone_source",this.timezoneSource)):(sessionStorage.removeItem("dotstark_user_timezone"),sessionStorage.removeItem("dotstark_timezone_source"))}catch{}}_refreshTimezone(e=!1){try{if(this.timezoneSource==="user_selected")return!1;const t=this._detectBrowserTimezone();if(t&&t!==this.userTimezone){const s=this.userTimezone;return this.userTimezone=t,this.timezoneSource="detected",this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this._persistTimezonePreference(),console.log("[DotStark] timezone updated:",s,"->",t),e&&this._msgList&&this._appendSystem(`Timezone updated to ${this._formatTimezoneDisplayName(t)} — upcoming times will use the new zone.`),!0}}catch{}return!1}_maxDateISO(e=null,t=60){const s=this._currentDateISO(e);try{const n=String(s).split("-").map(Number),i=new Date(Date.UTC(n[0],n[1]-1,n[2]));i.setUTCDate(i.getUTCDate()+Math.max(0,Number(t)||0));const l=i.getUTCFullYear(),a=String(i.getUTCMonth()+1).padStart(2,"0"),o=String(i.getUTCDate()).padStart(2,"0");return`${l}-${a}-${o}`}catch{return s}}_horizonDays(){var s;const e=Number((s=this._agentConfig)==null?void 0:s.max_booking_days_ahead),t=(()=>{const n=new Date,i=o=>o%4===0&&(o%100!==0||o%400===0),l=new Date(n.getFullYear(),n.getMonth(),n.getDate()),a=new Date(l.getTime()+366*864e5);for(let o=l.getFullYear();o<=a.getFullYear();o++){if(!i(o))continue;const r=new Date(o,1,29);if(r>=l&&r<=a)return 366}return 365})();return Number.isFinite(e)?Math.min(t,Math.max(1,Math.round(e))):60}_buildAvailabilityRequestUrl(e){const t=new URLSearchParams;return t.set("date",e),m.widgetToken&&t.set("widget_token",m.widgetToken),this.userTimezone&&t.set("tz",this.userTimezone),this.timezoneSource&&t.set("timezone_source",this.timezoneSource),this.selectedUnitId&&t.set("unit_id",this.selectedUnitId),this.selectedProviderId&&t.set("provider_id",this.selectedProviderId),`${m.apiBase}/widget/availability?${t.toString()}`}_getCatalogUnits(){var t;const e=(t=this._agentConfig)==null?void 0:t.catalog;return!e||!Array.isArray(e.units)?[]:e.units.filter(s=>s&&s.is_active!==!1)}_getCatalogProviders(e){const t=this._getCatalogUnits();if(!e){const n=[];return t.forEach(i=>(i.providers||[]).forEach(l=>n.push(l))),n.filter(i=>i&&i.is_active!==!1)}const s=t.find(n=>String(n.id)===String(e));return(s&&s.providers||[]).filter(n=>n&&n.is_active!==!1)}_maybeShowCatalogPicker(){if(this._catalogPickerShown)return;const e=this._getCatalogUnits();e.length&&(this.selectedUnitId&&this.selectedProviderId||(this._catalogPickerShown=!0,this._appendUnitPicker(e)))}_appendUnitPicker(e){this._clearDirectiveCards();const t=document.createElement("div");t.className="message agent field-input-card directive-card";const s=document.createElement("div");s.className="card-header",s.textContent="🏢 Choose department",t.appendChild(s);const n=document.createElement("select");n.className="widget-dept-select",n.setAttribute("aria-label","Choose department"),n.size=Math.max(3,Math.min(6,e.length));const i=document.createElement("option");i.value="",i.textContent=`Select department (${e.length} available)…`,n.appendChild(i),e.forEach(o=>{const r=document.createElement("option");r.value=String(o.id),r.textContent=o.name,n.appendChild(r)}),t.appendChild(n);const l=document.createElement("p");l.className="field-description",l.textContent="Pick the department for your visit.",t.appendChild(l),n.onchange=()=>{const o=e.find(r=>String(r.id)===n.value);l.textContent=o&&o.description||"Pick the department for your visit."};const a=document.createElement("button");a.className="widget-field-btn",a.textContent="Continue",a.onclick=()=>{const o=e.find(r=>String(r.id)===n.value);if(!o){l.textContent="Please select a department first.";try{n.focus()}catch{}return}a.disabled=!0,n.disabled=!0,this.selectedUnitId=String(o.id),this.selectedProviderId=null,this._clearDirectiveCards(),this._handleSendManual(`I want to book in ${o.name}`)},t.appendChild(a),this._msgList.appendChild(t),this._scrollToBottom()}_selectedProviderFee(){if(!this.selectedProviderId)return null;for(const e of this._getCatalogUnits())for(const t of e.providers||[])if(String(t.id)===String(this.selectedProviderId)&&t.fee_display)return t.fee_display;return null}_appendProviderPicker(e){this._renderProviderButtons(e,t=>{this.selectedProviderId=String(t.id),this._appendBubble("user",t.full_name),this._clearDirectiveCards();const s=t.fee_display?` (Fee: ${t.fee_display})`:"";this._appendBubble("agent",`Great — ${t.full_name}${s}. What date works for you?`)})}_appendEngineProviderPicker(e={}){const t=Array.isArray(e==null?void 0:e.providers)?e.providers:[];t.length&&this._renderProviderButtons(t,s=>{this.selectedProviderId=String(s.id),this._handleSendManual(s.full_name)})}_renderProviderButtons(e,t){this._clearDirectiveCards();const s=document.createElement("div");s.className="message agent field-input-card directive-card";const n=document.createElement("div");n.className="card-header",n.textContent="👤 Choose team member",s.appendChild(n);const i=document.createElement("div");i.className="select-options-grid",e.forEach(l=>{const a=document.createElement("button");a.className="select-option-btn";const o=document.createElement("span");if(o.className="select-option-name",o.textContent=l.full_name,a.appendChild(o),l.fee_display){const r=document.createElement("span");r.className="select-option-desc",r.textContent=l.fee_display,a.appendChild(r)}l.role_label&&(a.title=l.role_label),a.onclick=()=>{i.querySelectorAll(".select-option-btn").forEach(r=>{r.disabled=!0}),a.classList.add("selected"),t(l)},i.appendChild(a)}),s.appendChild(i),this._msgList.appendChild(s),this._scrollToBottom()}_formatTimezoneSource(e){return e==="user_selected"?"you selected":e==="fallback_org"?"business fallback":"browser detected"}_formatTimezoneDisplayName(e){var s;if(!e)return"UTC";const t=(s=e.split("/").pop())==null?void 0:s.replace(/_/g," ");return t?`${e} (${t})`:e}_isValidTimezone(e){if(!e||typeof e!="string")return!1;try{return new Intl.DateTimeFormat("en-US",{timeZone:e}).format(new Date),!0}catch{return!1}}_formatClockTime(e,t=null){const s={hour:"numeric",minute:"2-digit",hour12:!0};t&&this._isValidTimezone(t)&&(s.timeZone=t);try{return new Intl.DateTimeFormat([],s).format(e)}catch{return e.toLocaleTimeString([],{hour:"numeric",minute:"2-digit",hour12:!0})}}_currentDateISO(e=null){var l,a,o;const t=new Date;if(e&&this._isValidTimezone(e))try{const r=new Intl.DateTimeFormat("en-US",{timeZone:e,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(t),d=(l=r.find(g=>g.type==="year"))==null?void 0:l.value,p=(a=r.find(g=>g.type==="month"))==null?void 0:a.value,c=(o=r.find(g=>g.type==="day"))==null?void 0:o.value;if(d&&p&&c)return`${d}-${p}-${c}`}catch{}const s=t.getFullYear(),n=String(t.getMonth()+1).padStart(2,"0"),i=String(t.getDate()).padStart(2,"0");return`${s}-${n}-${i}`}_getTimezoneOptions(){let e=[];try{const t=Intl.supportedValuesOf("timeZone");Array.isArray(t)&&t.length>0&&(e=[...t].sort())}catch{}return e.length===0&&(e=["UTC","Africa/Abidjan","Africa/Cairo","Africa/Casablanca","Africa/Johannesburg","Africa/Lagos","Africa/Nairobi","America/Argentina/Buenos_Aires","America/Bogota","America/Caracas","America/Chicago","America/Denver","America/Halifax","America/Lima","America/Los_Angeles","America/Mexico_City","America/New_York","America/Phoenix","America/Santiago","America/Sao_Paulo","America/Toronto","America/Vancouver","Asia/Almaty","Asia/Baghdad","Asia/Bangkok","Asia/Beirut","Asia/Dhaka","Asia/Dubai","Asia/Hong_Kong","Asia/Istanbul","Asia/Jakarta","Asia/Jerusalem","Asia/Karachi","Asia/Kathmandu","Asia/Kolkata","Asia/Kuala_Lumpur","Asia/Manila","Asia/Riyadh","Asia/Seoul","Asia/Shanghai","Asia/Singapore","Asia/Taipei","Asia/Tashkent","Asia/Tehran","Asia/Tokyo","Asia/Yangon","Australia/Adelaide","Australia/Brisbane","Australia/Melbourne","Australia/Perth","Australia/Sydney","Europe/Amsterdam","Europe/Athens","Europe/Berlin","Europe/Brussels","Europe/Bucharest","Europe/Budapest","Europe/Copenhagen","Europe/Dublin","Europe/Helsinki","Europe/Lisbon","Europe/London","Europe/Madrid","Europe/Moscow","Europe/Oslo","Europe/Paris","Europe/Prague","Europe/Rome","Europe/Stockholm","Europe/Vienna","Europe/Warsaw","Europe/Zurich","Pacific/Auckland","Pacific/Fiji","Pacific/Honolulu"]),Array.from(new Set([this.userTimezone,this.orgTimezone,this._detectBrowserTimezone(),...e].filter(Boolean)))}_buildTimezoneContextBanner(e=null){const t=document.createElement("div");t.className="timezone-context-banner";const s=document.createElement("div");s.className="timezone-context-text",s.textContent=`Times shown in ${this._formatTimezoneDisplayName(this.userTimezone)} (${this._formatTimezoneSource(this.timezoneSource)})`,t.appendChild(s);const n=document.createElement("div");n.className="timezone-override-row";const i=document.createElement("label");i.className="timezone-override-label",i.textContent="Timezone",n.appendChild(i);const l=document.createElement("select");return l.className="timezone-override-select",this._getTimezoneOptions().forEach(a=>{const o=document.createElement("option");o.value=a,o.textContent=a,a===this.userTimezone&&(o.selected=!0),l.appendChild(o)}),l.onchange=async()=>{const a=l.value;!a||a===this.userTimezone||(this.selectedSlotId=null,this._isValidTimezone(a)?(this.userTimezone=a,this.timezoneSource="user_selected",this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null):(this.userTimezone=this.orgTimezone||this.userTimezone,this.timezoneSource="fallback_org",this.timezoneFallbackUsed=!0,this.timezoneFallbackReason="invalid_tz"),this._persistTimezonePreference(),this._showTimezoneFallbackNoticeIfNeeded(),e&&await this._appendTimeSlotGrid({icon:"⏰",label:"Select a Time Slot",options:[]}))},n.appendChild(l),t.appendChild(n),t}_normalizeAvailabilitySlots(e){return Array.isArray(e)?e.filter(t=>t&&t.slot_id&&t.label_user_tz).map(t=>{const s=t.start_user_tz?new Date(t.start_user_tz):null,n=t.start_org_tz?new Date(t.start_org_tz):null;return{id:t.slot_id,label:n?this._formatClockTime(n,this.orgTimezone):t.label_user_tz,userLabel:s?this._formatClockTime(s,this.userTimezone):null,sortKey:s?s.getTime():Number.MAX_SAFE_INTEGER,available:t.available!==!1,reason:t.reason||null,raw:t}}).sort((t,s)=>t.sortKey-s.sortKey):[]}_availabilityStatusMessage(e){return e==="closed_day"||e==="exception_closed"?"No availability on this date (closed day/holiday). Please choose another date.":e==="fully_booked"?"This date is fully booked. Please choose another date.":e==="no_schedule"||e==="no_slots_in_window"?"No configured availability for this date yet. Please choose another date.":"No available time slots for this date. Please try another date."}_availabilityRecoveryMessage(e,t=null){const s=this._availabilityStatusMessage(e),n=t||this.timezoneFallbackReason;if(!n)return s;const i=this._formatTimezoneDisplayName(this.orgTimezone||this.userTimezone);return n==="invalid_tz"?`${s} We are using ${i} while you pick another date.`:n==="missing_tz"?`${s} We could not detect your timezone, so we are using ${i} while you pick another date.`:`${s} Please pick another date.`}_showTimezoneFallbackNoticeIfNeeded(){!this.timezoneFallbackUsed||this._timezoneNoticeShown||(this._timezoneNoticeShown=!0,this._appendSystem("We couldn't detect your timezone, so times are shown in the business timezone for now."))}_toggle(){this.isOpen=!this.isOpen,this._launcher.classList.toggle("open",this.isOpen),this._launcher.setAttribute("aria-expanded",String(this.isOpen)),this._launcher.setAttribute("aria-label",this.isOpen?"Close chat":"Open booking chat"),this._chatBox.setAttribute("aria-hidden",String(!this.isOpen)),this.isOpen?(this._chatBox.classList.add("visible"),this._chatBox.classList.remove("hiding"),setTimeout(()=>{try{this._input.focus()}catch{}try{typeof this._applyHeaderMarquee=="function"&&this._applyHeaderMarquee()}catch{}},200)):(this._chatBox.classList.add("hiding"),setTimeout(()=>{this._chatBox.classList.remove("visible","hiding")},250))}async _handleSend(){var s,n,i,l,a;const e=this._input.value.trim();if(!e||this.isLoading)return;this.isLoading=!0,this._canGoHome=!1;try{this._updateBackBtn()}catch{}this._clearSkipAction(),this._sendBtn.classList.add("sending"),setTimeout(()=>this._sendBtn.classList.remove("sending"),400),this._appendBubble("user",e),this._input.value="",this._input.disabled=!0,this._sendBtn.disabled=!0,this.isLoading=!0,this._pendingRefreshSkips=[{role:"user",content:e}];const t=this._appendTyping();this._refreshTimezone();try{const o={message:e,message_id:`${Date.now()}-${Math.random().toString(36).slice(2,10)}`};this.selectedUnitId&&(o.unit_id=this.selectedUnitId),this.selectedProviderId&&(o.provider_id=this.selectedProviderId),this.sessionId&&(o.session_id=this.sessionId),(this._chatMode==="ask"||this._chatMode==="booking")&&(o.mode=this._chatMode);const r=this.sessionId||null,d=this._chatMode||null;try{(n=(s=this._sendAbort)==null?void 0:s.abort)==null||n.call(s)}catch{}const p=new AbortController;this._sendAbort=p,this.sessionId||(this.selectedSlotId=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1),this.selectedSlotId&&(o.selected_slot_id=this.selectedSlotId),this.userTimezone&&(o.user_timezone=this.userTimezone),this.timezoneSource&&(o.timezone_source=this.timezoneSource),this.timezoneFallbackUsed&&(o.timezone_fallback_used=!0,this.timezoneFallbackReason&&(o.timezone_fallback_reason=this.timezoneFallbackReason));const c=await fetch(`${m.apiBase}/chat/message/dispatch`,{method:"POST",signal:p.signal,headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||""},body:JSON.stringify(o)});if(!c.ok){const h=await c.json().catch(()=>({}));throw new Error(h.detail||`Server error ${c.status}`)}const g=await c.json();if(this._chatMode!==d||r&&this.sessionId!==r)return;if(g.session_id){this.sessionId=g.session_id;try{sessionStorage.setItem(this._sessionStorageKey(),g.session_id),(this._chatMode==="ask"||this._chatMode==="booking")&&sessionStorage.setItem(this._sessionKeyFor(this._chatMode),g.session_id),sessionStorage.removeItem("dotstark_session_id")}catch{}this._connectWebSocket(),this._startPolling()}const f=this._resolveAssistantText(((i=g.action)==null?void 0:i.message)||g.agent_response),u=(l=g.action)==null?void 0:l.widget_directive;this._lastDirective=u||null;const y=u==="REQUEST_CONFIRMATION"||u==="BOOKING_SUMMARY_CARD"||u==="BOOKING_CONFIRMED_CARD";t.remove(),y||(f?(this._appendBubble("agent",f),this._pendingRefreshSkips.push({role:"agent",content:f})):u!=="HUMAN_ACTIVE"&&this._appendBubble("agent","I received your message, but something went wrong with the reply."));const b=(a=g.action)==null?void 0:a.field_meta;this._activeFieldMeta=b||null,this._setInlineValidation((b==null?void 0:b.validation_error)||""),await this._renderDirectiveUI(u,b,f)}catch(o){if(o&&o.name==="AbortError")return;console.error("Widget Error:",o),t.remove(),this._appendBubble("agent",`Couldn't reach the server. Make sure the backend is running on ${m.apiBase}`)}finally{this.isLoading=!1,this._input.disabled=!1,this._sendBtn.disabled=!1;try{this._applyComposerRule()}catch{}try{this._inputArea&&this._inputArea.style.display!=="none"&&this._input.focus()}catch{}this.sessionId&&this._loadHistory(this._lastMessageTimestamp)}}_appendBubble(e,t){const s=document.createElement("div");s.className=`message ${e}`,s.setAttribute("role","log"),e==="agent"?this._renderMarkdown(s,t):s.textContent=t;const n=document.createElement("span");n.className="msg-time";const i=new Date;return n.textContent=i.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}),s.appendChild(n),this._msgList.appendChild(s),this._scrollToBottom(),this._announceToSR(e==="agent"?"Agent: "+t:""),s}_appendHumanBubble(e){const t=document.createElement("div");t.className="message human-agent",t.setAttribute("role","log");const s=document.createElement("div");s.className="human-label",s.textContent=e.admin_name||"Support agent",t.appendChild(s);const n=document.createElement("div");n.className="human-text",n.textContent=e.content||"",t.appendChild(n);const i=document.createElement("span");i.className="msg-time";try{const l=e.created_at?new Date(e.created_at):new Date;i.textContent=l.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}catch{i.textContent=new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}return t.appendChild(i),this._msgList.appendChild(t),this._scrollToBottom(),this._announceToSR(`Support agent: ${e.content||""}`),t}_renderMarkdown(e,t){t.split(`
`).forEach((n,i)=>{i>0&&e.appendChild(document.createElement("br"));const l=n.trim();if(l){if(/^[-•]\s/.test(l)){const a=document.createElement("span");a.className="md-bullet",this._parseInline(a,l.replace(/^[-•]\s*/,"")),e.appendChild(a);return}this._parseInline(e,l)}})}_parseInline(e,t){t.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).forEach(n=>{if(/^\*\*(.+)\*\*$/.test(n)){const i=document.createElement("strong");i.textContent=n.slice(2,-2),e.appendChild(i)}else if(/^\*(.+)\*$/.test(n)){const i=document.createElement("em");i.textContent=n.slice(1,-1),e.appendChild(i)}else n&&e.appendChild(document.createTextNode(n))})}_announceToSR(e){if(!e)return;let t=this.shadowRoot.getElementById("sr-live");t||(t=document.createElement("div"),t.id="sr-live",t.setAttribute("role","status"),t.setAttribute("aria-live","polite"),t.className="sr-only",this.shadowRoot.appendChild(t)),t.textContent=e}_appendTyping(){const e=document.createElement("div");return e.className="message agent typing-msg",e.innerHTML=`
                <div class="typing-dots">
                    <span></span><span></span><span></span>
                </div>
            `,this._msgList.appendChild(e),this._scrollToBottom(),e}_appendSystem(e){const t=document.createElement("div");t.className="message system";const s=document.createElement("span");return s.textContent=e,t.appendChild(s),this._msgList.appendChild(t),this._scrollToBottom(),t}_updateInputMode(e,t){this._input&&(this._input.type=e||"text",this._input.placeholder=t||"Type a message...")}_setInlineValidation(e){this._inlineValidation&&(this._inlineValidation.textContent=e||"",this._inlineValidation.style.display=e?"block":"none")}_clearDirectiveCards(){this._msgList.querySelectorAll(".directive-card").forEach(e=>e.remove())}_clearSkipAction(){if(!this._sendBtn)return;const e=this.shadowRoot.getElementById("widget-skip-btn");e&&e.remove()}_renderSkipAction(e){var s;if(this._clearSkipAction(),!e||e.required!==!1)return;const t=document.createElement("button");t.id="widget-skip-btn",t.className="widget-skip-btn",t.textContent="Skip",t.onclick=()=>{t.disabled=!0,this._handleSendManual(this._skipToken)},(s=this._sendBtn.parentElement)==null||s.appendChild(t)}_defaultCountryCode(){var t;try{const n=(t=(Intl.DateTimeFormat().resolvedOptions().locale||"").split("-").pop())==null?void 0:t.toUpperCase();if(n&&n.length===2)return n}catch{}const e=this.userTimezone||"";return e.includes("London")?"GB":e.includes("Kolkata")?"IN":e.includes("Sydney")?"AU":"US"}_appendPhoneInput(e){this._clearDirectiveCards(),this._updateInputMode("text","Type a message..."),this._clearSkipAction();const t=(e==null?void 0:e.name)||"phone",s=this._directiveDrafts[t]||{},n=((e==null?void 0:e.country_code)||s.countryCode||this._defaultCountryCode()).toUpperCase(),i=(e==null?void 0:e.raw_input)||s.localNumber||"",l=[{code:"US",dial:"+1",example:"(555) 000-0000"},{code:"GB",dial:"+44",example:"7700 900000"},{code:"IN",dial:"+91",example:"98765 43210"},{code:"AU",dial:"+61",example:"400 000 000"},{code:"CA",dial:"+1",example:"(555) 000-0000"}],a=u=>{var y;return((y=l.find(b=>b.code===u))==null?void 0:y.example)||"98765 43210"},o=document.createElement("div");o.className="message agent field-input-card directive-card";const r=document.createElement("div");r.className="card-header",r.textContent=`${(e==null?void 0:e.icon)||"📞"} ${(e==null?void 0:e.label)||"Phone Number"}`,o.appendChild(r);const d=document.createElement("div");d.className="widget-phone-row";const p=document.createElement("select");p.className="widget-phone-country",l.forEach(u=>{const y=document.createElement("option");y.value=u.code,y.textContent=`${u.code} (${u.dial})`,u.code===n&&(y.selected=!0),p.appendChild(y)});const c=document.createElement("input");if(c.type="tel",c.className="widget-phone-local",c.placeholder=a(n),c.value=i,p.onchange=()=>{c.placeholder=a(p.value)},d.appendChild(p),d.appendChild(c),o.appendChild(d),e!=null&&e.validation_error){const u=document.createElement("div");u.className="field-inline-error",u.textContent=e.validation_error,o.appendChild(u)}const g=document.createElement("div");g.className="widget-field-actions";const f=document.createElement("button");if(f.className="widget-field-btn",f.textContent="Submit Phone",f.onclick=()=>{var h;const u=p.value||this._defaultCountryCode(),b=`${((h=l.find(_=>_.code===u))==null?void 0:h.dial)||"+1"} ${c.value.trim()}`.trim();this._directiveDrafts[t]={countryCode:u,localNumber:c.value.trim()},f.disabled=!0,c.disabled=!0,p.disabled=!0,this._handleSendManual(b)},g.appendChild(f),(e==null?void 0:e.required)===!1){const u=document.createElement("button");u.className="widget-field-btn widget-secondary-btn",u.textContent="Skip",u.onclick=()=>{u.disabled=!0,f.disabled=!0,this._handleSendManual(this._skipToken)},g.appendChild(u)}o.appendChild(g),this._msgList.appendChild(o),this._scrollToBottom()}_appendDatePicker(e){var l;this._clearDirectiveCards(),this._clearSkipAction();const t=document.createElement("div");t.className="message agent field-input-card directive-card";const s=document.createElement("div");s.className="card-header",s.textContent=`${(e==null?void 0:e.icon)||"📅"} ${(e==null?void 0:e.label)||"Select a Date"}`,t.appendChild(s),t.appendChild(this._buildTimezoneContextBanner(null));const n=document.createElement("input");n.type="date",n.className="widget-date-input",n.min=this._currentDateISO(this.userTimezone),n.max=this._maxDateISO(this.userTimezone,this._horizonDays()),this.selectedDate?n.value=this.selectedDate:n.value=n.min,t.appendChild(n);{const a=Array.isArray((l=this._agentConfig)==null?void 0:l.closed_weekdays)?this._agentConfig.closed_weekdays:[],o=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],r=a.map(p=>o[p]||"").filter(Boolean).join(", "),d=document.createElement("p");d.className="field-description",d.textContent=`Book within ${this._horizonDays()} days ahead`+(r?` · Closed: ${r}`:"")+".",t.appendChild(d)}const i=document.createElement("button");if(i.className="widget-field-btn",i.textContent="Confirm Date",i.onclick=()=>{n.value&&(i.disabled=!0,n.disabled=!0,this.selectedSlotId=null,this.selectedDate=n.value,this._handleSendManual(n.value))},t.appendChild(i),(e==null?void 0:e.required)===!1){const a=document.createElement("button");a.className="widget-field-btn widget-secondary-btn",a.textContent="Skip",a.onclick=()=>this._handleSendManual(this._skipToken),t.appendChild(a)}this._msgList.appendChild(t),this._scrollToBottom()}_appendSelectInput(e){if(this._clearDirectiveCards(),this._clearSkipAction(),!e||!e.options||e.options.length===0){this._appendSystem(`Please type your choice for: ${(e==null?void 0:e.label)||"selection"}`);return}const t=document.createElement("div");t.className="message agent field-input-card directive-card";const s=document.createElement("div");if(s.className="card-header",s.textContent=`${e.icon||"📋"} ${e.label||"Choose an option"}`,t.appendChild(s),e.description){const i=document.createElement("p");i.className="field-description",i.textContent=e.description,t.appendChild(i)}const n=document.createElement("div");if(n.className="select-options-grid",e.options.forEach(i=>{const l=document.createElement("button");l.className="select-option-btn",l.textContent=i,l.onclick=()=>{n.querySelectorAll(".select-option-btn").forEach(a=>{a.disabled=!0,a.classList.remove("selected")}),l.classList.add("selected"),this._handleSendManual(i)},n.appendChild(l)}),t.appendChild(n),(e==null?void 0:e.required)===!1){const i=document.createElement("button");i.className="widget-field-btn widget-secondary-btn",i.textContent="Skip",i.onclick=()=>this._handleSendManual(this._skipToken),t.appendChild(i)}this._msgList.appendChild(t),this._scrollToBottom()}_quickPromptsFor(e){return(this._agentConfig&&Array.isArray(this._agentConfig.quick_prompts)?this._agentConfig.quick_prompts:[]).filter(s=>{if(!s||typeof s.title!="string"||!s.title.trim()||!s.content&&!s.link)return!1;const n=(s.show_at||"both").toLowerCase();return e==="start"?n==="start"||n==="both":n==="followup"||n==="both"})}_fillHomeView(){try{const e=this._agentConfig||{},t=this.shadowRoot.getElementById("home-title"),s=this.shadowRoot.getElementById("home-tagline"),n=this.shadowRoot.getElementById("home-avatar");if(t&&(t.textContent=e.agent_name||"Booking Assistant"),s&&(s.textContent=[e.business_name,e.business_tagline].filter(Boolean).join(" · ")||"How can we help you today?"),n)if(this._logoSrc){n.innerHTML="";const i=document.createElement("img");i.src=this._logoSrc,i.alt="logo",i.onerror=()=>{try{n.textContent=String(e.agent_name||"B").trim()[0]||"B"}catch{}},n.appendChild(i)}else{const i=String(e.agent_name||"B");n.textContent=(i.trim()[0]||"B").toUpperCase()}}catch{}}_applyComposerRule(){if(this._humanActive){this._setComposerVisible(!0);return}if(this._chatMode==="booking"){this._setComposerVisible(!1);return}this._setComposerVisible(!0)}_setSessionContentVisible(e){try{e?(this._msgList&&(this._msgList.style.display=""),this._inputArea&&(this._inputArea.style.display=""),this._inlineValidation&&(this._inlineValidation.style.display="")):(this._msgList&&(this._msgList.style.display="none"),this._qpBar&&(this._qpBar.style.display="none"),this._inputArea&&(this._inputArea.style.display="none"),this._inlineValidation&&(this._inlineValidation.style.display="none"))}catch{}}_updateBackBtn(){try{if(!this._backBtn)return;const e=!!this._homeView&&this._homeView.style.display!=="none";this._backBtn.style.display=this._canGoHome&&!e?"flex":"none"}catch{}}_showHomeView(){try{if(!this._homeView)return;this._fillHomeView(),this._setSessionContentVisible(!1),this._homeView.classList.remove("slide-out"),this._homeView.style.display="flex",this._homeView.offsetWidth,this._homeView.classList.add("slide-in")}catch{}try{this._updateBackBtn()}catch{}}_showSessionView(){try{if(!this._homeView)return;this._setSessionContentVisible(!0);try{this._applyComposerRule()}catch{}if(this._homeView.style.display==="none"){try{this._updateBackBtn()}catch{}return}this._homeView.classList.remove("slide-in"),this._homeView.classList.add("slide-out"),window.setTimeout(()=>{try{this._homeView.style.display="none"}catch{}},280)}catch{}try{this._updateBackBtn()}catch{}}_renderModeExit(){if(this._humanActive||!this._qpBar)return;this._clearQuickPrompts();const e=document.createElement("button");e.className="qp-pill qp-mode",e.textContent="💬 Ask AI instead",e.setAttribute("aria-label","Switch to Ask AI"),e.onclick=()=>this._switchSection("ask"),this._qpBar.appendChild(e),this._qpBar.style.display="flex";try{this._refreshQpNav()}catch{}}_switchSection(e){var s,n;if(this._chatMode===e&&this.sessionId){try{this._showSessionView()}catch{}return}let t=null;try{(this._chatMode==="ask"||this._chatMode==="booking")&&this.sessionId&&sessionStorage.setItem(this._sessionKeyFor(this._chatMode),this.sessionId),t=sessionStorage.getItem(this._sessionKeyFor(e)),this.sessionId=t||null,this._lastDirective=null,this._msgList.querySelectorAll(".message").forEach(i=>i.remove());try{this._renderedMessageIds=new Set}catch{}this._pendingRefreshSkips=[],this._lastMessageTimestamp=null;try{this._clearQuickPrompts()}catch{}this.selectedSlotId=null,this.selectedDate=null,this.selectedUnitId=null,this.selectedProviderId=null,this._catalogPickerShown=!1,this._humanActive=!1,this._humanJoinedShownFor=null;try{this._ws&&(this._ws.onclose=null,this._ws.close())}catch{}this._ws=null,this._wsConnected=!1;try{(n=(s=this._sendAbort)==null?void 0:s.abort)==null||n.call(s)}catch{}this._sendAbort=null}catch{}this._setMode(e,{silent:!!t}),t&&(async()=>{try{await this._loadHistory()}catch{}try{this._connectWebSocket()}catch{}try{this._startPolling()}catch{}if(e==="booking"){try{this._appendSystem("Welcome back! Your booking details so far are saved — continue below.")}catch{}await this._resumePendingCard()}else try{this._appendBubble("agent","Welcome back! What would you like to ask?")}catch{}})()}_setMode(e,t={}){var s;if(this._chatMode===e&&this.sessionId){try{this._showSessionView()}catch{}return}this._chatMode=e,this._saveChatMode(),this._clearQuickPrompts();try{this._showSessionView()}catch{}if(e==="ask"){try{this._setComposerVisible(!0),this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder="Type a message...",this._input.style.opacity="1"}catch{}if(!t.silent){const n=((s=this._agentConfig)==null?void 0:s.ask_greeting_message)||"Hello! 👋 I'm here to answer your questions. What would you like to know?";this._appendBubble("agent",n)}try{this._renderQuickPrompts("start")}catch{}}else if(e==="booking"&&!t.silent){try{this._input.disabled=!0,this._sendBtn.disabled=!0,this._input.placeholder="Please choose an option above ☝️",this._input.style.opacity="0.5"}catch{}try{this._renderModeExit()}catch{}let n=!1;try{this._getCatalogUnits().length>0&&(this._maybeShowCatalogPicker(),n=this._catalogPickerShown===!0)}catch{}n||this._handleSendManual("I want to book an appointment")}}_clearQuickPrompts(){try{this._qpBar&&(this._qpBar.innerHTML="",this._qpBar.style.display="none");const e=this.shadowRoot&&this.shadowRoot.getElementById("qp-switch-slot");e&&(e.innerHTML="");const t=this._qpWrap||this.shadowRoot&&this.shadowRoot.getElementById("qp-bar-wrap");t&&t.classList.remove("qp-has-switch"),this._msgList.querySelectorAll(".quick-prompts").forEach(s=>s.remove())}catch{}try{this._refreshQpNav()}catch{}}_bindQpNav(){if(!this._qpNavBound){this._qpNavBound=!0;try{const e=this.shadowRoot.getElementById("qp-bar-wrap"),t=this._qpBar,s=this.shadowRoot.getElementById("qp-nav-left"),n=this.shadowRoot.getElementById("qp-nav-right");if(!e||!t||!s||!n)return;this._qpWrap=e,s.addEventListener("click",()=>{try{this._qpScrollOne(-1)}catch{}}),n.addEventListener("click",()=>{try{this._qpScrollOne(1)}catch{}}),t.addEventListener("scroll",()=>{try{this._refreshQpNav()}catch{}},{passive:!0}),t.addEventListener("wheel",i=>{try{const l=t.scrollWidth-t.clientWidth;if(l<=2)return;const a=(i.deltaX||0)+(i.deltaY||0);if(!a)return;const o=Date.now();if(o-(this._qpWheelLock||0)<350){i.preventDefault();return}(a>0&&t.scrollLeft<l-1||a<0&&t.scrollLeft>1)&&(i.preventDefault(),this._qpWheelLock=o,this._qpScrollOne(a>0?1:-1))}catch{}},{passive:!1}),window.addEventListener("resize",()=>{try{this._refreshQpNav()}catch{}})}catch{}}}_refreshQpNav(){try{const e=this._qpWrap||this.shadowRoot&&this.shadowRoot.getElementById("qp-bar-wrap"),t=this._qpBar;if(!e||!t)return;const s=t.style.display!=="none"&&t.children.length>0,n=t.scrollWidth-t.clientWidth,i=s&&t.scrollLeft>4,l=s&&n>4&&t.scrollLeft<n-4;e.classList.toggle("qp-can-left",!!i),e.classList.toggle("qp-can-right",!!l)}catch{}}_qpSuggestPills(){try{const e=this._qpBar;return e?Array.from(e.children).filter(t=>t.classList&&t.classList.contains("qp-pill")&&!t.classList.contains("qp-mode")):[]}catch{return[]}}_qpScrollOne(e){try{const t=this._qpBar;if(!t)return;const s=t.getBoundingClientRect(),n=r=>r.getBoundingClientRect().left-s.left+t.scrollLeft,i=this._qpSuggestPills();if(!i.length)return;const l=4,a=t.scrollLeft+l;let o=e>0?null:0;if(e>0){for(const r of i)if(n(r)>a){o=n(r);break}o===null&&(o=t.scrollWidth)}else for(let r=i.length-1;r>=0;r--)if(n(i[r])<a-l){o=n(i[r]);break}try{t.scrollTo({left:Math.max(0,o),behavior:"smooth"})}catch{t.scrollLeft=Math.max(0,o)}}catch{}}_appendBookingSwitchPill(){try{const e=this.shadowRoot.getElementById("qp-switch-slot"),t=this._qpWrap||this.shadowRoot.getElementById("qp-bar-wrap");if(!e||!t||e.querySelector('[data-switch="booking"]'))return;const s=document.createElement("span");s.className="qp-switch-wrap";const n=document.createElement("button");n.className="qp-pill qp-mode",n.textContent="📅 Start Booking",n.setAttribute("aria-label","Switch to guided booking"),n.dataset.switch="booking",n.onclick=()=>this._switchSection("booking"),s.appendChild(n),e.appendChild(s);const i=document.createElement("span");i.className="qp-sep",i.setAttribute("aria-hidden","true"),e.appendChild(i),t.classList.add("qp-has-switch")}catch{}}_renderQuickPrompts(e){if(this._humanActive||!this._qpBar)return;const t=this._quickPromptsFor(e);this._clearQuickPrompts(),t.forEach(s=>{const n=document.createElement("button");n.className="qp-pill",n.textContent=s.title,n.setAttribute("aria-label",`Suggested: ${s.title}`),n.onclick=()=>{if(s.link){try{window.open(s.link,"_blank","noopener")}catch{}return}this._clearQuickPrompts(),this._handleSendManual(s.content)},this._qpBar.appendChild(n)}),this._chatMode==="ask"&&this._appendBookingSwitchPill(),this._qpBar.children.length&&(this._qpBar.style.display="flex");try{this._refreshQpNav()}catch{}}async _renderDirectiveUI(e,t,s){if(e==="REQUIRE_DATE_PICKER")this._appendDatePicker(t);else if(e==="REQUIRE_PROVIDER_PICKER")this._appendEngineProviderPicker(t);else if(e==="REQUIRE_TIME_SLOT_GRID")await this._appendTimeSlotGrid(t);else if(e==="REQUIRE_PHONE_INPUT")this._appendPhoneInput(t);else if(e==="REQUIRE_EMAIL_INPUT")this._chatMode==="booking"?this._appendFieldInputCard(t,"email"):(this._clearDirectiveCards(),this._updateInputMode("email",(t==null?void 0:t.placeholder)||"Enter email address..."),this._renderSkipAction(t));else if(e==="REQUIRE_NUMBER_INPUT")this._chatMode==="booking"?this._appendFieldInputCard(t,"number"):(this._clearDirectiveCards(),this._updateInputMode("number",(t==null?void 0:t.placeholder)||"Enter a number..."),this._renderSkipAction(t));else if(e==="REQUIRE_SELECT")this._appendSelectInput(t);else if(e==="REQUIRE_TEXTAREA")this._chatMode==="booking"?this._appendFieldInputCard(t,"textarea"):(this._clearDirectiveCards(),this._appendSystem(`${(t==null?void 0:t.icon)||"📄"} ${(t==null?void 0:t.description)||"Please provide details."}`),this._renderSkipAction(t));else if(e==="TEXT_INPUT"&&t)this._chatMode==="booking"?this._appendFieldInputCard(t,"text"):(this._clearDirectiveCards(),this._updateInputMode("text",t.placeholder||"Type a message..."),this._renderSkipAction(t));else if(e==="REQUIRE_PAYMENT")this._clearDirectiveCards(),this._appendPaymentCard(t||{}),this._clearSkipAction();else if(e==="SHOW_REBOOK_BUTTON")this._clearDirectiveCards(),this._appendRebookButton(t||{}),this._clearSkipAction();else if(e==="REQUIRE_OTP_MODAL")this._clearDirectiveCards(),this._appendOTPInterface(t||{}),this._clearSkipAction();else if(e==="REQUEST_CONFIRMATION"||e==="BOOKING_SUMMARY_CARD")this._clearDirectiveCards(),this._appendSummaryCard(s||"Please review your booking details before confirming."),this._clearSkipAction();else if(e==="BOOKING_CONFIRMED_CARD")this._clearDirectiveCards(),this._appendConfirmedCard(s||"Your booking has been confirmed.",t||{}),this._clearSkipAction();else if(e==="HUMAN_ACTIVE"){this._clearDirectiveCards(),!this._humanActive&&!this._humanJoinedShownFor&&(this._appendSystem("👨‍💼 Support agent joined the chat"),this._humanJoinedShownFor=this.sessionId),this._updateInputMode("text","Type a message..."),this._clearSkipAction(),this._clearQuickPrompts(),this._humanActive=!0;try{this._setComposerVisible(!0)}catch{}this._startPolling()}else this._chatMode==="booking"?this._appendFieldInputCard({label:"Your answer",placeholder:"Type your answer...",required:!0},"text"):(this._clearDirectiveCards(),this._updateInputMode("text","Type a message..."),this._clearSkipAction());try{const n=["REQUIRE_OTP_MODAL","BOOKING_CONFIRMED_CARD","HUMAN_ACTIVE","REQUIRE_PAYMENT"].includes(e);if(this._humanActive||n)this._clearQuickPrompts();else if(this._chatMode==="booking"){this._renderModeExit();try{this._applyComposerRule()}catch{}}else{try{this._applyComposerRule()}catch{}this._renderQuickPrompts("followup")}}catch{}}async _resumePendingCard(){if(!(!this.sessionId||!m.widgetToken||this._chatMode!=="booking"))try{const t=`${m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1"}/widget/session-resume?session_id=${encodeURIComponent(this.sessionId)}&widget_token=${encodeURIComponent(m.widgetToken)}`,s=await fetch(t);if(!s.ok)return;const n=await s.json();if(!n||!n.directive||this._chatMode!=="booking")return;this._lastDirective=n.directive,this._activeFieldMeta=n.field_meta||null;try{this._setInlineValidation("")}catch{}await this._renderDirectiveUI(n.directive,n.field_meta||null,n.message||null)}catch{}}async _appendTimeSlotGrid(e){var u,y,b;this._clearDirectiveCards(),this._clearSkipAction(),e!=null&&e.slot_unavailable&&(this.selectedSlotId=null);const t=this._extractLatestIsoDate();this.selectedDate=t||this.selectedDate;const s=this._appendSystem("⏳ Checking live availability…");let n=[],i=null;if(t){const h=await this._fetchAvailabilityOptions(t);if(this._chatMode!=="booking"){try{(u=s==null?void 0:s.remove)==null||u.call(s)}catch{}return}n=h.options,i=h.status,h.total??n.filter(_=>_.available!==!1).length}try{(y=s==null?void 0:s.remove)==null||y.call(s)}catch{}if(!n.length&&((b=e==null?void 0:e.options)!=null&&b.length)&&(n=e.options.map(h=>({id:null,label:h,available:!0,reason:null})),i=(e==null?void 0:e.availability_status)||i),!n.length){const h=(e==null?void 0:e.timezone_fallback_reason)||this.timezoneFallbackReason||null;this._appendSystem(`${(e==null?void 0:e.icon)||"⏰"} ${this._availabilityRecoveryMessage(i,h)}`),this.selectedSlotId=null,this.selectedDate=null,this._appendDatePicker({icon:"📅",label:"Choose another date",required:e==null?void 0:e.required});return}const l=document.createElement("div");l.className="message agent field-input-card directive-card";const a=document.createElement("div");if(a.className="card-header",a.textContent=`${e.icon||"⏰"} ${e.label||"Select a Time Slot"}`,l.appendChild(a),l.appendChild(this._buildTimezoneContextBanner(t)),e.description){const h=document.createElement("p");h.className="field-description",h.textContent=e.description,l.appendChild(h)}if(e!=null&&e.validation_error){const h=document.createElement("p");h.className="field-inline-error",h.textContent=e.validation_error,l.appendChild(h)}const o=document.createElement("div");o.className="select-options-grid";const r=Math.max(1,Number((e==null?void 0:e.suggestion_limit)||3)||3),d=n.filter(h=>h.available!==!1),p=d.length>r,c=p?d.slice(0,r):n,g=p?d.slice(r):[],f=h=>{const _=document.createElement("button");_.className="select-option-btn time-slot-btn";const v=document.createElement("span");if(v.className="slot-local-time",v.textContent=h.label,_.appendChild(v),h.userLabel){const x=document.createElement("span");x.className="slot-org-time",x.textContent=`Your time: ${h.userLabel}`,_.appendChild(x)}return h.available||(_.disabled=!0,_.classList.add("unavailable"),h.reason&&(_.title=`Unavailable (${h.reason})`)),_.onclick=()=>{h.available&&(o.querySelectorAll(".time-slot-btn").forEach(x=>{x.disabled=!0,x.classList.remove("selected")}),_.classList.add("selected"),this.selectedSlotId=h.id||null,this._handleSendManual(h.label))},_};if(c.forEach(h=>o.appendChild(f(h))),p){const h=document.createElement("button");h.className="select-option-btn time-slot-more-btn",h.textContent=`Show more times (${g.length} more)`,h.setAttribute("aria-label",`Show ${g.length} more time slots`),h.onclick=()=>{h.remove(),g.forEach(_=>o.appendChild(f(_))),this._scrollToBottom()},o.appendChild(h)}l.appendChild(o),this._msgList.appendChild(l),this._scrollToBottom()}_extractLatestIsoDate(){if(this.selectedDate)return this.selectedDate;const e=this._msgList.querySelectorAll(".message.user");for(let t=e.length-1;t>=0;t--){const n=(e[t].textContent||"").trim().match(/\d{4}-\d{2}-\d{2}/);if(n)return n[0]}return null}async _fetchAvailabilityOptions(e){try{const t=await fetch(this._buildAvailabilityRequestUrl(e));if(!t.ok)return{options:[],status:null};const s=await t.json();return this.orgTimezone=s.org_timezone||this.orgTimezone,s.user_timezone&&(this.userTimezone=s.user_timezone),s.timezone_source&&(this.timezoneSource=s.timezone_source),this.timezoneFallbackUsed=!!s.timezone_fallback_used,this.timezoneFallbackReason=s.timezone_fallback_reason||null,this._persistTimezonePreference(),this._showTimezoneFallbackNoticeIfNeeded(),{options:this._normalizeAvailabilitySlots(s.slots),status:s.availability_status||null,total:typeof s.total_available=="number"?s.total_available:null,truncated:!!s.truncated}}catch{return{options:[],status:null,total:null,truncated:!1}}}_appendOTPInterface(e={}){const t=((e==null?void 0:e.channel)||"email").toLowerCase(),s=t==="phone",n=document.createElement("div");n.className="message agent otp-card",n.setAttribute("role","form"),n.setAttribute("aria-label",s?"Phone verification":"Email verification");const i=document.createElement("div");i.className="card-header",i.textContent=s?"Phone Verification Required":"Verification Required";const l=document.createElement("p");l.textContent=s?"Please enter the SMS verification code sent to your phone.":"Please enter the email verification code to continue booking.";const a={cooldownSeconds:Number((e==null?void 0:e.otp_cooldown_seconds)||30),resendCap:Number((e==null?void 0:e.resend_cap)||3),resendsLeft:Number(typeof(e==null?void 0:e.resends_left)=="number"?e.resends_left:(e==null?void 0:e.resend_cap)||3)},o=document.createElement("div");o.className="otp-input-group";const r=document.createElement("input");r.type="text",r.maxLength=8,r.placeholder="Enter code",r.id="otp-field",r.setAttribute("aria-label","Verification code"),r.setAttribute("autocomplete","one-time-code");const d=document.createElement("button");d.id="verify-otp-btn",d.textContent="Verify",d.setAttribute("aria-label","Verify code"),o.appendChild(r),o.appendChild(d);const p=document.createElement("button");p.className="otp-resend-link",p.textContent="Didn't receive it? Resend code",p.setAttribute("aria-label","Resend verification code");const c=document.createElement("div");c.className="otp-status",c.setAttribute("aria-live","polite"),c.textContent=`Resend available in ${a.cooldownSeconds}s • ${a.resendsLeft} resends left`;const g=document.createElement("button");g.className="otp-resend-link",g.textContent="Use a different email address",g.setAttribute("aria-label","Use different email address"),n.appendChild(i),n.appendChild(l),n.appendChild(o),n.appendChild(p),n.appendChild(g),n.appendChild(c),this._msgList.appendChild(n),this._scrollToBottom(),this._input.disabled=!0,this._sendBtn.disabled=!0,this._input.placeholder="Enter the verification code above...",this._input.style.opacity="0.5",setTimeout(()=>r.focus(),100),s&&setTimeout(()=>{y="Code sent via WhatsApp. Enter it above.",c.textContent=y,c.style.color="#22c55e",b(),d.disabled=!1,r.disabled=!1},100);let f=Math.max(0,a.resendCap-a.resendsLeft),u=Math.max(0,a.cooldownSeconds),y="";const b=()=>{const _=Math.max(0,a.resendCap-f),v=u>0?`Resend available in ${u}s`:"You can resend now";s&&y?c.textContent=`${y} • ${v} • ${_} resends left`:c.textContent=`${v} • ${_} resends left`,p.disabled=u>0||f>=a.resendCap};b();const h=window.setInterval(()=>{if(u<=0){window.clearInterval(h);return}u-=1,b()},1e3);d.onclick=async()=>{const _=(r.value||"").replace(/\s+/g,"");console.warn("[DotStark] Verify clicked. code length:",_.length,"session:",this.sessionId,"channel:",t),C("verify_clicked",`code_len:${_.length} channel:${t}`);try{window._dotstarkLastSessionId=this.sessionId}catch{}if(_.length<4){c.textContent=s?"Please enter the code sent to your phone.":"Please enter the code sent to your email.",c.style.color="#ef4444",r.focus();return}if(!this.sessionId){c.textContent="Session expired. Please refresh and start again.",c.style.color="#ef4444";return}d.disabled=!0,r.disabled=!0,d.textContent="...",c.textContent="";try{let v;if(s)try{const x={session_id:this.sessionId,channel:"phone",code:_};console.warn("[DotStark] Verifying WhatsApp phone code"),C("wa_verify_attempt",`code_len:${_.length}`);const k=await fetch(`${m.apiBase}/auth/security/otp/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify(x)});if(k.ok)console.warn("[DotStark] WhatsApp phone verify succeeded"),C("wa_verify_success",_.slice(0,2)+"***"),v=k;else{const E=(await k.text().catch(()=>"")).slice(0,120);console.warn("[DotStark] WhatsApp verify failed status",k.status,E),C("wa_verify_failed",`status:${k.status} ${E.slice(0,60)}`),c.textContent=E||"That code is incorrect or has expired. Please try again or resend a new code.",c.style.color="#ef4444",d.disabled=!1,r.disabled=!1,d.textContent="Verify",r.value="",r.focus();return}}catch(x){console.warn("[DotStark] WhatsApp verify error",x),C("wa_verify_error",String(x).slice(0,80)),c.textContent="Couldn't reach the server to verify. Please check your connection and try again.",c.style.color="#ef4444",d.disabled=!1,r.disabled=!1,d.textContent="Verify";return}else v=await fetch(`${m.apiBase}/auth/security/otp/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({session_id:this.sessionId,code:_,channel:t})});if(v.ok){n.textContent="";const x=document.createElement("div");x.className="success-msg",x.textContent=s?"✅ Phone Verified Successfully!":"✅ Email Verified Successfully!",n.appendChild(x),this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder="Type a message...",this._input.style.opacity="1",setTimeout(()=>this._handleSendManual("I've verified the code. Please proceed."),1e3)}else{let x="";try{const k=await v.json();x=k&&k.detail?String(k.detail):""}catch{}c.textContent=v.status===429?x||"Too many attempts. Please request a new code.":x||"That code is incorrect or has expired. Please try again or resend a new code.",c.style.color="#ef4444",d.disabled=!1,r.disabled=!1,d.textContent="Verify",r.value="",r.focus()}}catch{c.textContent="Couldn't reach the server to verify. Please check your connection and try again.",c.style.color="#ef4444",d.disabled=!1,r.disabled=!1,d.textContent="Verify"}},r.onkeydown=_=>{_.key==="Enter"&&d.click()},p.onclick=async()=>{if(f>=a.resendCap){c.textContent="Maximum resend attempts reached. Please try again later.",c.style.color="#ef4444";return}if(!(u>0)){p.disabled=!0,p.textContent="Sending...",c.textContent="";try{const _=n;if(s)try{await this._handleSendManual("resend phone")}catch{}else await this._handleSendManual("resend");f++,u=a.cooldownSeconds;const v=Math.max(0,a.resendCap-f);s?(y=`Code resent! (${v} resends left)`,c.textContent=`${y} • Resend available in ${u}s • ${v} resends left`,c.style.color="#22c55e"):(c.textContent=`Code resent! (${v} resends left)`,c.style.color="#22c55e",y=c.textContent),b(),setTimeout(()=>{try{_.style.opacity="0.5",_.style.pointerEvents="none"}catch{}},300)}catch{c.textContent="Failed to resend. Please try again.",c.style.color="#ef4444",y=c.textContent,b()}p.textContent="Didn't receive it? Resend code"}},g.textContent=s?"Use a different phone number":"Use a different email address",g.setAttribute("aria-label",s?"Use different phone":"Use different email address"),g.onclick=()=>{this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder=s?"Enter the correct phone number...":"Enter the correct email address...",this._input.style.opacity="1",this._input.focus(),c.textContent=s?"No problem — provide the correct phone and we’ll resend there.":"No problem — provide the correct email and we’ll resend there.",c.style.color="#64748b"},this._scrollToBottom(),r.focus()}_appendSummaryCard(e){const t=document.createElement("div");t.className="message agent summary-card";const s=document.createElement("div");s.className="card-header",s.textContent="📋 Booking Summary";const n=document.createElement("div");n.className="summary-content",this._renderMarkdown(n,e);const i=this._selectedProviderFee();if(i){const r=document.createElement("div");r.className="summary-fee-line",r.textContent=`Fee: ${i}`,n.appendChild(r)}const l=document.createElement("div");l.className="card-actions";const a=document.createElement("button");a.className="confirm-btn",a.textContent="Confirm Booking",a.setAttribute("aria-label","Confirm booking");const o=document.createElement("button");o.className="edit-btn",o.textContent="✎ Change Details",o.setAttribute("aria-label","Edit booking details"),l.appendChild(a),l.appendChild(o),t.appendChild(s),t.appendChild(n),t.appendChild(l),this._msgList.appendChild(t),a.onclick=()=>{a.disabled=!0,o.disabled=!0,this._handleSendManual("Yes, please confirm the booking.")},o.onclick=()=>{a.disabled=!0,o.disabled=!0,this._handleSendManual("I need to change some details.")},this._scrollToBottom()}async _loadRazorpay(){if(window.Razorpay)return!0;try{return await new Promise((e,t)=>{const s=document.createElement("script");s.src="https://checkout.razorpay.com/v1/checkout.js",s.onload=e,s.onerror=()=>t(new Error("razorpay load failed")),document.head.appendChild(s)}),!!window.Razorpay}catch{return!1}}async _loadCashfree(){if(window.Cashfree)return!0;try{return await new Promise((e,t)=>{const s=document.createElement("script");s.src="https://sdk.cashfree.com/js/v3/cashfree.js",s.onload=e,s.onerror=()=>t(new Error("cashfree sdk load failed")),document.head.appendChild(s)}),!!window.Cashfree}catch{return!1}}async _pollCashfreePaid(e,t){const s=m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1";for(let n=0;n<40;n++){try{const i=await fetch(`${s}/widget/payment/cashfree/status?order_id=${encodeURIComponent(e)}&session_id=${encodeURIComponent(this.sessionId)}`,{headers:{"X-Widget-Token":m.widgetToken||""}});if(i.ok&&(await i.json().catch(()=>({}))).status==="paid")return!0}catch{}t&&t(n),await new Promise(i=>setTimeout(i,3e3))}return!1}_appendRebookButton(e={}){const t=document.createElement("div");t.className="message agent directive-card",t.setAttribute("role","form");const s=document.createElement("button");s.textContent=e.label||"Book a new appointment",s.style.cssText="margin-top:10px;padding:12px 20px;border:none;border-radius:10px;background:var(--primary);color:#fff;font-weight:700;width:100%;cursor:pointer;";const n=e.payload||"Book a new appointment";s.onclick=()=>{s.disabled=!0,this._handleSendManual(n)},t.appendChild(s),this._msgList.appendChild(t),this._scrollToBottom()}_appendCashfreePaymentCard(e={}){const t=document.createElement("div");t.className="message agent payment-card",t.setAttribute("role","form");const s=Number(e.amount_paise||0),n=e.amount_display||(s?`₹${(s/100).toFixed(2)}`:"₹500.00"),i=e.currency||"INR",l=document.createElement("div");l.className="card-header",l.textContent="Payment Required";const a=document.createElement("p");a.style.cssText="font-size:13px;color:var(--text-sec);margin:8px 0;",a.textContent=`Your appointment total is ${n} (${i}). Please pay to confirm your booking. UPI, card, netbanking or wallet via Cashfree.`;const o=document.createElement("button");o.textContent=`Pay ${n}`,o.style.cssText="margin-top:10px;padding:12px 20px;border:none;border-radius:10px;background:var(--primary);color:#fff;font-weight:700;width:100%;cursor:pointer;";const r=document.createElement("div");r.id="payment-status",r.style.cssText="font-size:12px;color:var(--text-sec);margin-top:8px;";const d=p=>{o.disabled=!1,o.textContent=`Pay ${n}`,p&&(r.textContent=p)};o.onclick=async()=>{o.disabled=!0,o.textContent="Opening Cashfree…",r.textContent="";try{const p=m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1",c=await fetch(`${p}/widget/payment/cashfree/order`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({session_id:this.sessionId,widget_token:m.widgetToken})});if(!c.ok){const b=await c.json().catch(()=>({}));throw new Error(b.detail&&(b.detail.message||b.detail.code)||b.detail||"Failed to create order")}const g=await c.json();if(!await this._loadCashfree())throw new Error("Failed to load Cashfree checkout");const f=window.Cashfree({mode:g.environment==="production"?"production":"sandbox"});r.textContent="Opening secure payment page in a new tab…";try{C("cf_checkout_open",`${g.environment}:${g.order_id}`)}catch{}const u=await f.checkout({paymentSessionId:g.payment_session_id,redirectTarget:"_blank"});try{C("cf_checkout_result",JSON.stringify({error:u&&u.error,redirect:!!(u&&u.redirect),hasDetails:!!(u&&u.paymentDetails),order:g.order_id}).slice(0,200))}catch{}if(u&&u.error?r.textContent="Checking payment status…":r.textContent="Payment page opened — complete the payment in the new tab…",!await this._pollCashfreePaid(g.order_id,()=>{r.textContent="Waiting for payment… complete it in the new tab."})){d("Payment was not completed. Your appointment has not been confirmed. You can retry.");return}r.textContent="Payment successful ✅",this._appendSystem("Payment successful ✅ Appointment confirmed ✅"),this._input.value="confirm",await this._handleSend()}catch(p){try{C("cf_checkout_error",String(p&&p.message||p).slice(0,200))}catch{}d("Error: "+(p.message||p))}},t.appendChild(l),t.appendChild(a),t.appendChild(o),t.appendChild(r),this._msgList.appendChild(t),this._scrollToBottom()}_appendPaymentCard(e={}){if((e.payment_provider||"razorpay")==="cashfree")return this._appendCashfreePaymentCard(e);const t=document.createElement("div");t.className="message agent payment-card",t.setAttribute("role","form");const s=Number(e.amount_paise||0),n=e.amount_display||(s?`₹${(s/100).toFixed(2)}`:"₹500.00"),i=e.currency||"INR",l=document.createElement("div");l.className="card-header",l.textContent="Payment Required";const a=document.createElement("p");a.style.cssText="font-size:13px;color:var(--text-sec);margin:8px 0;",a.textContent=`Your appointment total is ${n} (${i}). Please pay to confirm your booking. UPI Intent on mobile, QR on desktop via Razorpay Checkout.`;const o=document.createElement("button");o.textContent=`Pay ${n}`,o.style.cssText="margin-top:10px;padding:12px 20px;border:none;border-radius:10px;background:var(--primary);color:#fff;font-weight:700;width:100%;cursor:pointer;";const r=document.createElement("div");r.id="payment-status",r.style.cssText="font-size:12px;color:var(--text-sec);margin-top:8px;",o.onclick=async()=>{var d,p;o.disabled=!0,o.textContent="Opening Razorpay…",r.textContent="";try{if(!await this._loadRazorpay())throw new Error("Failed to load Razorpay");const g=m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1",f=await fetch(`${g}/widget/payment/order`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({session_id:this.sessionId,widget_token:m.widgetToken})});if(!f.ok){const h=await f.json().catch(()=>({}));throw new Error(h.detail||"Failed to create order")}const u=await f.json(),y={key:u.key,amount:u.amount_paise,currency:u.currency||"INR",name:((d=this._agentConfig)==null?void 0:d.business_name)||"Aurexa",description:"Appointment payment",order_id:u.order_id,handler:async h=>{r.textContent="Verifying payment…";try{const _=await fetch(`${g}/widget/payment/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({session_id:this.sessionId,razorpay_order_id:h.razorpay_order_id,razorpay_payment_id:h.razorpay_payment_id,razorpay_signature:h.razorpay_signature,widget_token:m.widgetToken})}),v=await _.json().catch(()=>({}));if(!_.ok)throw new Error(v.detail||"Verification failed");r.textContent="Payment successful ✅",this._appendSystem("Payment successful ✅ Appointment confirmed ✅"),this._input.value="confirm",await this._handleSend()}catch(_){r.textContent="Payment verification failed: "+(_.message||_),o.disabled=!1,o.textContent=`Pay ${n}`}},modal:{ondismiss:()=>{o.disabled=!1,o.textContent=`Pay ${n}`,r.textContent="Payment was not completed. Your appointment has not been confirmed. You can retry."}},theme:{color:((p=this._agentConfig)==null?void 0:p.brand_color)||"#3399CC"}},b=new window.Razorpay(y);b.on("payment.failed",h=>{var _;r.textContent="Payment failed: "+(((_=h.error)==null?void 0:_.description)||"try again"),o.disabled=!1,o.textContent=`Pay ${n}`}),b.open()}catch(c){r.textContent="Error: "+(c.message||c),o.disabled=!1,o.textContent=`Pay ${n}`}},t.appendChild(l),t.appendChild(a),t.appendChild(o),t.appendChild(r),this._msgList.appendChild(t),this._scrollToBottom()}_appendConfirmedCard(e,t={}){const s=document.createElement("div");s.className="message agent confirmed-card",s.setAttribute("role","alert");const n=document.createElement("div");n.className="conf-icon",n.textContent="🎉";const i=document.createElement("h3");i.textContent="Booking Confirmed!",i.style.cssText="font-size:16px;font-weight:700;color:var(--success);margin:8px 0;";const l=document.createElement("p");l.style.cssText="font-size:13px;color:var(--text-sec);line-height:1.5;",this._renderMarkdown(l,e);const a=document.createElement("div");a.className="conf-footer",a.textContent=(t==null?void 0:t.email_retrying)===!0?"📧 Booking confirmed. Confirmation email delivery is retrying in the background.":"📧 A confirmation email is on its way.";try{const d=t&&t.booking_id;if(d){const p=document.createElement("a"),c=typeof m<"u"&&m.widgetToken||"";p.href=`${m.apiBase}/widget/invite/${encodeURIComponent(String(d))}?widget_token=${encodeURIComponent(c)}`,p.textContent="📅 Add to Calendar (.ics)",p.setAttribute("download",`booking-${String(d)}.ics`),p.style.cssText=`
                        margin-top:12px;padding:10px 20px;border:1px solid var(--border);border-radius:8px;
                        background:transparent;color:var(--text);font-size:13px;font-weight:600;
                        cursor:pointer;width:100%;text-align:center;text-decoration:none;display:block;box-sizing:border-box;
                    `,s.appendChild(p)}}catch{}const o=(d,p)=>{const c=document.createElement("button");return c.textContent=d,c.style.cssText=`
                    margin-top:8px;padding:10px 20px;border:1px solid var(--border);border-radius:8px;
                    background:transparent;color:var(--text);font-size:13px;font-weight:600;
                    cursor:pointer;width:100%;box-sizing:border-box;
                `,c.addEventListener("click",()=>this._handleSendManual(p)),c};s.appendChild(o("📅 Reschedule","reschedule my booking")),s.appendChild(o("❌ Cancel Booking","cancel my booking"));const r=document.createElement("button");r.textContent="🔄 Start New Booking",r.style.cssText=`
                margin-top:12px;padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;
            `,r.addEventListener("mouseover",()=>r.style.opacity="0.85"),r.addEventListener("mouseout",()=>r.style.opacity="1"),r.addEventListener("click",()=>this._startNewSession()),s.appendChild(n),s.appendChild(i),s.appendChild(l),s.appendChild(a),s.appendChild(r),this._msgList.appendChild(s),this._scrollToBottom()}async _handleSendManual(e){this._input.value=e,await this._handleSend()}_disableInput(e){this._input.disabled=!0,this._sendBtn.disabled=!0,this._input.placeholder=e||"Session ended.",this._input.style.opacity="0.5",this._sendBtn.style.opacity="0.3"}_enableInput(e){this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder=e||"Type a message...",this._input.style.opacity="1",this._sendBtn.style.opacity="1"}_setComposerVisible(e){try{this._inputArea&&(this._inputArea.style.display=e?"flex":"none")}catch{}}_appendFieldInputCard(e={},t="text"){this._clearDirectiveCards(),this._clearSkipAction();const s=document.createElement("div");s.className="message agent field-input-card directive-card";const n=document.createElement("div");if(n.className="card-header",n.textContent=`${e.icon||"✏️"} ${e.label||"Your answer"}`,s.appendChild(n),e.description){const r=document.createElement("p");r.className="field-description",r.textContent=e.description,s.appendChild(r)}if(e!=null&&e.validation_error){const r=document.createElement("p");r.className="field-inline-error",r.textContent=e.validation_error,s.appendChild(r)}let i;t==="textarea"?(i=document.createElement("textarea"),i.rows=3):(i=document.createElement("input"),i.type=t==="email"?"email":t==="number"?"number":"text"),i.className="widget-field-textinput",i.placeholder=e.placeholder||`Enter ${e.label||"your answer"}...`,i.setAttribute("aria-label",e.label||"Your answer"),s.appendChild(i);const l=document.createElement("div");l.className="widget-field-actions";const a=document.createElement("button");a.className="widget-field-btn",a.textContent="Submit";const o=()=>{const r=(i.value||"").trim();if(!r&&e.required!==!1){let d=s.querySelector(".field-inline-error");d||(d=document.createElement("p"),d.className="field-inline-error",s.insertBefore(d,i.nextSibling)),d.textContent=`Please enter ${e.label||"your answer"}.`;try{i.focus()}catch{}return}a.disabled=!0,i.disabled=!0,this._handleSendManual(r||this._skipToken)};if(a.onclick=o,i.onkeydown=r=>{r.key==="Enter"&&!r.shiftKey&&t!=="textarea"&&(r.preventDefault(),o())},l.appendChild(a),e.required===!1){const r=document.createElement("button");r.className="widget-field-btn widget-secondary-btn",r.textContent="Skip",r.onclick=()=>{r.disabled=!0,a.disabled=!0,this._handleSendManual(this._skipToken)},l.appendChild(r)}s.appendChild(l),this._msgList.appendChild(s),this._scrollToBottom(),setTimeout(()=>{try{i.focus()}catch{}},100)}_startNewSession(){this.sessionId=null,this._chatMode=null,this._lastDirective=null;try{sessionStorage.removeItem(this._modeStorageKey())}catch{}sessionStorage.removeItem(this._sessionStorageKey());try{sessionStorage.removeItem(this._sessionKeyFor("ask"))}catch{}try{sessionStorage.removeItem(this._sessionKeyFor("booking"))}catch{}sessionStorage.removeItem("dotstark_session_id"),sessionStorage.removeItem("dotstark_user_timezone"),sessionStorage.removeItem("dotstark_timezone_source"),this.userTimezone=this._detectBrowserTimezone(),this.timezoneSource="detected",this.selectedSlotId=null,this.selectedDate=null,this.orgTimezone=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this._directiveDrafts={},this._setInlineValidation(""),this._clearSkipAction(),this._msgList.querySelectorAll(".message").forEach(e=>e.remove());try{this._clearQuickPrompts()}catch{}this._canGoHome=!0;try{this._showHomeView()}catch{}try{this._setComposerVisible(!1)}catch{}}_appendNewBookingPrompt(){const e=document.createElement("div");e.className="message agent";const t=document.createElement("button");t.textContent="🔄 Start New Booking",t.style.cssText=`
                padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;margin-top:4px;
            `,t.addEventListener("mouseover",()=>t.style.opacity="0.85"),t.addEventListener("mouseout",()=>t.style.opacity="1"),t.addEventListener("click",()=>this._startNewSession()),e.appendChild(t),this._msgList.appendChild(e),this._scrollToBottom()}_resolveAssistantText(e){if(e&&typeof e=="object"){const s=e.response_text||e.message;return typeof s=="string"?s.trim():""}if(typeof e!="string")return"";const t=e.trim();if(!t)return"";if(t.startsWith("{")&&t.endsWith("}"))try{const s=JSON.parse(t);if(s&&typeof s=="object"){const n=s.response_text||s.message;return typeof n=="string"?n.trim():""}}catch{}return t}_scrollToBottom(e=!1){requestAnimationFrame(()=>{!e&&this._msgList.scrollHeight-this._msgList.scrollTop-this._msgList.clientHeight>120||this._msgList.scrollTo({top:this._msgList.scrollHeight,behavior:"smooth"})})}_css(){return`
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

                .header-back {
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
                    width: 22px;
                    height: 22px;
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
            `}}customElements.define("dotstark-booking-widget",T);try{window._dotstarkWidgetBuild="2026-10-12-qpslot",console.info("[DotStark] widget build:",window._dotstarkWidgetBuild)}catch{}const A=document.createElement("dotstark-booking-widget");document.body.appendChild(A)})()})();
