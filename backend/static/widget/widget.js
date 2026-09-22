(function(){"use strict";(function(){console.warn("[DotStark] widget.js loaded, build:","2026-09-01-fix-phone-otp-ready-3-client-log","href:",window.location.href),console.warn("[DotStark] If you see no logs after Verify, ensure console filter is set to All/Verbose, not Errors only");const O={apiKey:"AIzaSyC7zH8cEITPDpfyiqhPo7ZlK1-2DViL1BI",authDomain:"dotstark-833ec.firebaseapp.com",projectId:"dotstark-833ec",storageBucket:"dotstark-833ec.firebasestorage.app",messagingSenderId:"287417969813",appId:"1:287417969813:web:7ba50454917fe54d4f3f9c",measurementId:"G-MB7QC1JRGK"};let B=null,E=null,C=null,R=null;const F="2026-09-01-fix-phone-otp-ready-3-client-log";function T(g,e){try{const t=typeof p<"u"&&p.apiBase?p.apiBase:"/api/v1",n=typeof p<"u"&&p.widgetToken&&p.widgetToken||"";let i="";try{i=window._dotstarkLastSessionId||""}catch{}try{const a=document.querySelector("booking-widget");a&&a.sessionId&&(i=a.sessionId)}catch{}const r={event:String(g||"unknown").slice(0,40),session_id:String(i||"").slice(0,36),channel:"phone",hasConfirmationResult:typeof R<"u"?!!R:null,detail:String(e||"").slice(0,200),phone_masked:""};fetch(`${t}/widget/client-log`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":n,"X-Requested-With":"XMLHttpRequest"},body:JSON.stringify(r),keepalive:!0}).catch(()=>{})}catch{}}function $(){try{C&&typeof C.clear=="function"&&C.clear()}catch{}C=null;const g=document.getElementById("firebase-recaptcha-container");g&&g.parentNode&&g.parentNode.removeChild(g)}async function U(){if(console.log("[DotStark] Firebase Auth initialization started build:",F),B&&E&&C)try{return console.log("[DotStark] reCAPTCHA already initialized, reusing"),!0}catch{$()}if(B&&!E)try{E=window.firebase.auth()}catch{}try{if(window.firebase||(console.log("[DotStark] Loading Firebase SDK via CDN"),await new Promise((e,t)=>{const n=document.createElement("script");n.src="https://www.gstatic.com/firebasejs/10.7.0/firebase-app-compat.js",n.onload=()=>{const i=document.createElement("script");i.src="https://www.gstatic.com/firebasejs/10.7.0/firebase-auth-compat.js",i.onload=e,i.onerror=()=>t(new Error("firebase-auth-compat load failed")),document.head.appendChild(i)},n.onerror=()=>t(new Error("firebase-app-compat load failed")),document.head.appendChild(n)}),console.log("[DotStark] Firebase SDK loaded")),!window.firebase)throw new Error("Firebase not loaded");if(!B){try{window.firebase.apps&&window.firebase.apps.length?B=window.firebase.apps[0]:B=window.firebase.initializeApp(O)}catch(e){if(window.firebase.apps&&window.firebase.apps.length)B=window.firebase.apps[0];else throw e}console.log("[DotStark] Firebase app initialized, project:",O.projectId)}E||(E=window.firebase.auth());try{E.useDeviceLanguage()}catch{}console.log("[DotStark] Firebase Auth instance ready, hasAuth:",!!E),$();let g=document.getElementById("firebase-recaptcha-container");if(g||(g=document.createElement("div"),g.id="firebase-recaptcha-container",g.style.position="fixed",g.style.bottom="10px",g.style.right="10px",g.style.width="1px",g.style.height="1px",g.style.opacity="0.01",g.style.pointerEvents="none",g.style.zIndex="2147483647",document.body.appendChild(g)),console.log("[DotStark] reCAPTCHA container ready, exists:",!!document.getElementById("firebase-recaptcha-container")),C=new window.firebase.auth.RecaptchaVerifier("firebase-recaptcha-container",{size:"invisible"}),console.log("[DotStark] reCAPTCHA Verifier created, rendering..."),await C.render(),console.log("[DotStark] reCAPTCHA initialized, rendered:",!!C),E&&E.settings)try{E.settings.appVerificationDisabledForTesting=!1}catch{}return!0}catch(g){return console.warn("[DotStark] Firebase init failed",g,"code:",g&&g.code,"message:",g&&g.message),$(),!1}}async function H(){if(C){try{C.clear()}catch{}C=null}return await U()&&!!C&&!!E}function L(g){try{return g&&g.src?`${new URL(g.src,window.location.href).origin}/api/v1`:`${window.location.origin}/api/v1`}catch{return"/api/v1"}}function V(){const g=document.querySelectorAll("script[data-widget-token]");if(!g.length){const n=document.querySelectorAll("script[data-api-key]");if(!n.length)return null;const i=n[n.length-1],r=L(i);return{widgetToken:i.getAttribute("data-api-key"),apiBase:i.getAttribute("data-api-base")||r,primaryColor:i.getAttribute("data-color")||null}}const e=g[g.length-1],t=L(e);return{widgetToken:e.getAttribute("data-widget-token"),apiBase:e.getAttribute("data-api-base")||t,primaryColor:e.getAttribute("data-color")||null}}const p=V()||window.DotStarkBookingConfig||{widgetToken:null,apiBase:L(document.currentScript)};class q extends HTMLElement{constructor(){super(),this.attachShadow({mode:"open"}),this.isOpen=!1,this.sessionId=null,this.isLoading=!1,this._messages=[],this._agentConfig=null,this.userTimezone=this._detectBrowserTimezone(),this.timezoneSource="detected",this.selectedSlotId=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this.orgTimezone=null,this.selectedDate=null,this._directiveDrafts={},this._activeFieldMeta=null,this._skipToken="__DOTSTARK_SKIP__",this._lastMessageTimestamp=null,this._pollInterval=null,this._humanActive=!1,this._ws=null,this._wsReconnectTimer=null,this._wsReconnectAttempts=0,this._wsConnected=!1,this._renderedMessageIds=new Set,this._pendingRefreshSkips=[]}_sessionStorageKey(){const e=(p.widgetToken||"anonymous").trim()||"anonymous",t=(p.apiBase||"").trim()||"default";return`dotstark_session_id:${e}:${t}`}async connectedCallback(){try{const e=this._sessionStorageKey();p.forceNewSession===!0?(sessionStorage.removeItem(e),sessionStorage.removeItem("dotstark_session_id"),this.sessionId=null):this.sessionId=sessionStorage.getItem(e)||null;const t=sessionStorage.getItem("dotstark_user_timezone"),n=sessionStorage.getItem("dotstark_timezone_source");t&&(this.userTimezone=t,this.timezoneSource=n==="user_selected"?"user_selected":"detected")}catch{}await this._fetchAgentConfig(),this._buildShell(),this._startConfigPolling();try{this._tzVisibilityHandler=()=>{document.visibilityState==="visible"&&this._refreshTimezone(!0)},document.addEventListener("visibilitychange",this._tzVisibilityHandler),this._tzFocusHandler=()=>this._refreshTimezone(!0),window.addEventListener("focus",this._tzFocusHandler)}catch{}}disconnectedCallback(){try{this._configPollInterval&&clearInterval(this._configPollInterval),this._configPollInterval=null,this._tzVisibilityHandler&&(document.removeEventListener("visibilitychange",this._tzVisibilityHandler),this._tzVisibilityHandler=null),this._tzFocusHandler&&(window.removeEventListener("focus",this._tzFocusHandler),this._tzFocusHandler=null)}catch{}}_startConfigPolling(){try{this._configPollInterval&&clearInterval(this._configPollInterval),this._configPollInterval=setInterval(async()=>{var r,a,c,d;const e=(r=this._agentConfig)==null?void 0:r.meeting_mode,t=JSON.stringify(((a=this._agentConfig)==null?void 0:a.available_meeting_types)||[]);await this._fetchAgentConfig();const n=(c=this._agentConfig)==null?void 0:c.meeting_mode,i=JSON.stringify(((d=this._agentConfig)==null?void 0:d.available_meeting_types)||[]);(e!==n||t!==i)&&(this._updateMeetingModeBadge(),console.log("[DotStark] meeting mode updated via poll:",e,"->",n))},3e4)}catch{}}_updateMeetingModeBadge(){var e;try{const t=this.shadowRoot&&this.shadowRoot.getElementById("meeting-mode-badge");if(!t)return;const n=this._agentConfig||{},i=(n.meeting_mode||"").toString(),r=Array.isArray(n.available_meeting_types)?n.available_meeting_types:[];let a="",c=!0;if(i==="ONLINE_ONLY"||r.length===1&&r[0]==="ONLINE"?a="Online • "+(n.meeting_provider_default&&n.meeting_provider_default!=="NONE"?"Google Meet":"Online"):i==="OFFLINE_ONLY"||r.length===1&&r[0]==="OFFLINE"?a="Offline • In-person":i==="HYBRID"||r.length===2?a="Hybrid • Online + Offline":c=!1,c&&a){t.textContent=a,t.style.display="inline-block";try{const d=((e=this._agentConfig)==null?void 0:e.brand_color)||typeof p<"u"&&p.primaryColor||this.style.getPropertyValue("--brand-color")||"#3B82F6";t.style.color="white",t.style.borderColor=d||"#3B82F6",t.style.borderWidth="1.5px",t.style.borderStyle="solid",t.style.background="rgba(255,255,255,0.10)"}catch{t.style.color="white"}}else t.style.display="none"}catch{}}async _fetchAgentConfig(){if(p.widgetToken)try{const e=p.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1",t=await fetch(`${e}/widget/config?widget_token=${encodeURIComponent(p.widgetToken)}`);t.ok&&(this._agentConfig=await t.json(),this._updateMeetingModeBadge())}catch{}}async _loadHistory(e=null){if(!this.sessionId||!p.widgetToken)return!1;try{let n=`${p.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1"}/widget/history?session_id=${encodeURIComponent(this.sessionId)}&widget_token=${encodeURIComponent(p.widgetToken)}`;e&&(n+=`&since=${encodeURIComponent(e)}`);const i=await fetch(n);if(!i.ok)return!1;const r=await i.json();if(!r||r.length===0)return!1;for(const a of r){const c=a.metadata&&a.metadata.human_joined,d=a.metadata&&a.metadata.human_left,s=a.role==="agent"?this._resolveAssistantText(a.content):String(a.content||"");if(!c&&!d&&s){const l=!!(a.id&&this._renderedMessageIds.has(a.id)),h=this._pendingRefreshSkips.some(o=>o.role===a.role&&o.content===s);if(!l&&!h){const o=a.sender_type==="human"?{admin_name:a.human_agent_name}:{};a.id&&this._renderedMessageIds.add(a.id),this._appendBubble(a.role,s,o)}}if(a.created_at&&(this._lastMessageTimestamp=a.created_at),a.metadata&&a.metadata.human_left&&this._onHumanLeft(),a.metadata&&a.metadata.human_joined){const l=a.metadata.admin_name||"Support agent";this._updateHumanStatus(l,!0),this._startFastPolling()}a.sender_type==="human"&&this._startFastPolling()}return this._pendingRefreshSkips=[],!0}catch{return!1}}_startPolling(){if(this._pollInterval)return;const e=this._wsConnected?3e4:this._humanActive?3e3:1e4;this._pollInterval=setInterval(()=>{this.isOpen&&this.sessionId&&this._loadHistory(this._lastMessageTimestamp)},e)}_stopPolling(e=!1){this._pollInterval&&(clearInterval(this._pollInterval),this._pollInterval=null),e||(this._humanActive=!1)}_startFastPolling(){this._humanActive||(this._humanActive=!0,this._stopPolling(!0),this._startPolling())}_onHumanLeft(){this._updateHumanStatus(null,!1),this._stopPolling()}_updateHumanStatus(e,t){const n=this.shadowRoot.getElementById("status-text"),i=this.shadowRoot.querySelector(".status-dot");n&&(t&&e?(n.textContent=`👨‍💼 ${e} is now assisting you`,i&&(i.style.background="#f59e0b")):(n.textContent="Online — Ready to help",i&&(i.style.background="#22c55e")))}_wsBaseUrl(){const e=(p.apiBase||"").replace(/\/+$/,"");return e.startsWith("https://")?"wss://"+e.slice(8):e.startsWith("http://")?"ws://"+e.slice(7):e}_connectWebSocket(){if(!(!this.sessionId||!p.widgetToken)&&!(this._ws&&this._ws.readyState<=1))try{const e=`${this._wsBaseUrl()}/ws/widget?widget_token=${encodeURIComponent(p.widgetToken)}&session_id=${encodeURIComponent(this.sessionId)}`,t=new WebSocket(e);this._ws=t,t.onopen=()=>{this._wsConnected=!0,this._wsReconnectAttempts=0,this._pollInterval&&(this._stopPolling(!0),this._startPolling())},t.onmessage=n=>{let i;try{i=JSON.parse(n.data)}catch{return}this._handleWsEvent(i)},t.onclose=()=>{this._wsConnected=!1,this._ws=null;const n=Math.min(3e4,1e3*Math.pow(2,this._wsReconnectAttempts));this._wsReconnectAttempts++,this._wsReconnectTimer&&clearTimeout(this._wsReconnectTimer),this._wsReconnectTimer=setTimeout(()=>this._connectWebSocket(),n),this._pollInterval&&(this._stopPolling(!0),this._startPolling())},t.onerror=()=>{}}catch{this._wsConnected=!1,this._wsReconnectTimer&&clearTimeout(this._wsReconnectTimer),this._wsReconnectTimer=setTimeout(()=>this._connectWebSocket(),3e3)}}_handleWsEvent(e){switch(e.type){case"connected":break;case"human_joined":this._updateHumanStatus(e.admin_name||"Support agent",!0),this._startFastPolling(),this._humanJoinedShownFor||(this._humanJoinedShownFor=e.session_id,this._appendSystem(`👨‍💼 ${e.admin_name||"Support agent"} joined the chat`));break;case"human_left":this._updateHumanStatus(null,!1),this._humanJoinedShownFor=null,this._appendSystem("👨‍💼 Support agent left. AI assistant resumed."),this._stopPolling(),this._startPolling();break;case"human_message":if(e.message_id&&this._renderedMessageIds.has(e.message_id))return;this._appendHumanBubble(e),e.message_id&&(this._renderedMessageIds.add(e.message_id),this._lastMessageTimestamp=e.created_at||this._lastMessageTimestamp);break}}_buildShell(){var r,a,c,d;const e=((r=this._agentConfig)==null?void 0:r.logo_url)||null;let t=null;if(e)try{t=e.startsWith("http")?e:new URL(e,p.apiBase).href}catch{t=e}const n=t?`<img src="${t}" alt="logo" style="width:100%;height:100%;object-fit:cover;border-radius:12px;display:block;" onerror="this.style.display='none';this.nextElementSibling&& (this.nextElementSibling.style.display='block')"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" style="display:none"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10H5a2 2 0 0 0-2 2v1a7 7 0 0 0 14 0v-1a2 2 0 0 0-2-2z"/><line x1="12" y1="18" x2="12" y2="22"/></svg>`:'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10H5a2 2 0 0 0-2 2v1a7 7 0 0 0 14 0v-1a2 2 0 0 0-2-2z"/><line x1="12" y1="18" x2="12" y2="22"/></svg>';if(this.shadowRoot.innerHTML=`
                <style>${this._css()}</style>

                <div class="chat-container" id="chat-box" role="dialog" aria-label="Booking Assistant Chat" aria-hidden="true">
                    <div class="header">
                        <div class="header-avatar" id="avatar" aria-hidden="true">
                            ${n}
                        </div>
                        <div class="header-info">
                            <div style="display:flex; align-items:center; gap:8px; min-width:0;">
                                <h3 id="agent-name-title" style="margin:0;">${((a=this._agentConfig)==null?void 0:a.agent_name)||"Booking Assistant"}</h3>
                                <span id="meeting-mode-badge" class="meeting-mode-badge" style="display:none; font-size:10px; font-weight:700; padding:2px 7px; border-radius:20px; background:rgba(255,255,255,0.10); border:1.5px solid var(--brand-color, #3B82F6); color:white; white-space:nowrap; flex-shrink:0;"></span>
                            </div>
                            ${(()=>{const s=this._agentConfig||{},l=[s.business_name,s.business_tagline].filter(Boolean).join(" - ");if(!l)return"";const h=l.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");return`<p class="header-business marquee" id="header-business" style="--marquee-duration:${Math.max(8,Math.min(30,Math.round(l.length*.35)))}s"><span class="marquee-track"><span>${h}</span><span>${h}</span></span></p>`})()}
                            <p><span class="status-dot"></span> <span id="status-text">Online — Ready to help</span></p>
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
            `,this._launcher=this.shadowRoot.getElementById("launcher"),this._chatBox=this.shadowRoot.getElementById("chat-box"),this._input=this.shadowRoot.getElementById("user-input"),this._sendBtn=this.shadowRoot.getElementById("send-trigger"),this._msgList=this.shadowRoot.getElementById("message-list"),this._inlineValidation=this.shadowRoot.getElementById("widget-inline-validation"),this._launcher.onclick=()=>this._toggle(),this._launcher.onkeydown=s=>{(s.key==="Enter"||s.key===" ")&&(s.preventDefault(),this._toggle())},this.shadowRoot.getElementById("close-btn").onclick=()=>this._toggle(),this._sendBtn.onclick=()=>this._handleSend(),this._input.onkeydown=s=>{s.key==="Enter"&&!s.shiftKey&&(s.preventDefault(),this._handleSend()),s.key==="Escape"&&this._toggle()},this._chatBox.onkeydown=s=>{s.key==="Escape"&&this.isOpen&&this._toggle()},this.sessionId)this._loadHistory().then(s=>{var l;if(!s){const h=((l=this._agentConfig)==null?void 0:l.greeting_message)||"Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?";this._appendBubble("agent",h)}this._connectWebSocket(),this._startPolling()});else{const s=((c=this._agentConfig)==null?void 0:c.greeting_message)||"Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?";this._appendBubble("agent",s)}const i=((d=this._agentConfig)==null?void 0:d.brand_color)||p.primaryColor;i&&this.style.setProperty("--brand-color",i),this._updateMeetingModeBadge()}_detectBrowserTimezone(){try{return Intl.DateTimeFormat().resolvedOptions().timeZone||null}catch{return null}}_persistTimezonePreference(){try{this.userTimezone?(sessionStorage.setItem("dotstark_user_timezone",this.userTimezone),sessionStorage.setItem("dotstark_timezone_source",this.timezoneSource)):(sessionStorage.removeItem("dotstark_user_timezone"),sessionStorage.removeItem("dotstark_timezone_source"))}catch{}}_refreshTimezone(e=!1){try{if(this.timezoneSource==="user_selected")return!1;const t=this._detectBrowserTimezone();if(t&&t!==this.userTimezone){const n=this.userTimezone;return this.userTimezone=t,this.timezoneSource="detected",this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this._persistTimezonePreference(),console.log("[DotStark] timezone updated:",n,"->",t),e&&this._msgList&&this._appendSystem(`Timezone updated to ${this._formatTimezoneDisplayName(t)} — upcoming times will use the new zone.`),!0}}catch{}return!1}_maxDateISO(e=null,t=60){const n=this._currentDateISO(e);try{const i=String(n).split("-").map(Number),r=new Date(Date.UTC(i[0],i[1]-1,i[2]));r.setUTCDate(r.getUTCDate()+Math.max(0,Number(t)||0));const a=r.getUTCFullYear(),c=String(r.getUTCMonth()+1).padStart(2,"0"),d=String(r.getUTCDate()).padStart(2,"0");return`${a}-${c}-${d}`}catch{return n}}_horizonDays(){var n;const e=Number((n=this._agentConfig)==null?void 0:n.max_booking_days_ahead),t=(()=>{const i=new Date,r=d=>d%4===0&&(d%100!==0||d%400===0),a=new Date(i.getFullYear(),i.getMonth(),i.getDate()),c=new Date(a.getTime()+366*864e5);for(let d=a.getFullYear();d<=c.getFullYear();d++){if(!r(d))continue;const s=new Date(d,1,29);if(s>=a&&s<=c)return 366}return 365})();return Number.isFinite(e)?Math.min(t,Math.max(1,Math.round(e))):60}_buildAvailabilityRequestUrl(e){const t=new URLSearchParams;return t.set("date",e),p.widgetToken&&t.set("widget_token",p.widgetToken),this.userTimezone&&t.set("tz",this.userTimezone),this.timezoneSource&&t.set("timezone_source",this.timezoneSource),`${p.apiBase}/widget/availability?${t.toString()}`}_formatTimezoneSource(e){return e==="user_selected"?"you selected":e==="fallback_org"?"business fallback":"browser detected"}_formatTimezoneDisplayName(e){var n;if(!e)return"UTC";const t=(n=e.split("/").pop())==null?void 0:n.replace(/_/g," ");return t?`${e} (${t})`:e}_isValidTimezone(e){if(!e||typeof e!="string")return!1;try{return new Intl.DateTimeFormat("en-US",{timeZone:e}).format(new Date),!0}catch{return!1}}_formatClockTime(e,t=null){const n={hour:"numeric",minute:"2-digit",hour12:!0};t&&this._isValidTimezone(t)&&(n.timeZone=t);try{return new Intl.DateTimeFormat([],n).format(e)}catch{return e.toLocaleTimeString([],{hour:"numeric",minute:"2-digit",hour12:!0})}}_currentDateISO(e=null){var a,c,d;const t=new Date;if(e&&this._isValidTimezone(e))try{const s=new Intl.DateTimeFormat("en-US",{timeZone:e,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(t),l=(a=s.find(w=>w.type==="year"))==null?void 0:a.value,h=(c=s.find(w=>w.type==="month"))==null?void 0:c.value,o=(d=s.find(w=>w.type==="day"))==null?void 0:d.value;if(l&&h&&o)return`${l}-${h}-${o}`}catch{}const n=t.getFullYear(),i=String(t.getMonth()+1).padStart(2,"0"),r=String(t.getDate()).padStart(2,"0");return`${n}-${i}-${r}`}_getTimezoneOptions(){let e=[];try{const t=Intl.supportedValuesOf("timeZone");Array.isArray(t)&&t.length>0&&(e=[...t].sort())}catch{}return e.length===0&&(e=["UTC","Africa/Abidjan","Africa/Cairo","Africa/Casablanca","Africa/Johannesburg","Africa/Lagos","Africa/Nairobi","America/Argentina/Buenos_Aires","America/Bogota","America/Caracas","America/Chicago","America/Denver","America/Halifax","America/Lima","America/Los_Angeles","America/Mexico_City","America/New_York","America/Phoenix","America/Santiago","America/Sao_Paulo","America/Toronto","America/Vancouver","Asia/Almaty","Asia/Baghdad","Asia/Bangkok","Asia/Beirut","Asia/Dhaka","Asia/Dubai","Asia/Hong_Kong","Asia/Istanbul","Asia/Jakarta","Asia/Jerusalem","Asia/Karachi","Asia/Kathmandu","Asia/Kolkata","Asia/Kuala_Lumpur","Asia/Manila","Asia/Riyadh","Asia/Seoul","Asia/Shanghai","Asia/Singapore","Asia/Taipei","Asia/Tashkent","Asia/Tehran","Asia/Tokyo","Asia/Yangon","Australia/Adelaide","Australia/Brisbane","Australia/Melbourne","Australia/Perth","Australia/Sydney","Europe/Amsterdam","Europe/Athens","Europe/Berlin","Europe/Brussels","Europe/Bucharest","Europe/Budapest","Europe/Copenhagen","Europe/Dublin","Europe/Helsinki","Europe/Lisbon","Europe/London","Europe/Madrid","Europe/Moscow","Europe/Oslo","Europe/Paris","Europe/Prague","Europe/Rome","Europe/Stockholm","Europe/Vienna","Europe/Warsaw","Europe/Zurich","Pacific/Auckland","Pacific/Fiji","Pacific/Honolulu"]),Array.from(new Set([this.userTimezone,this.orgTimezone,this._detectBrowserTimezone(),...e].filter(Boolean)))}_buildTimezoneContextBanner(e=null){const t=document.createElement("div");t.className="timezone-context-banner";const n=document.createElement("div");n.className="timezone-context-text",n.textContent=`Times shown in ${this._formatTimezoneDisplayName(this.userTimezone)} (${this._formatTimezoneSource(this.timezoneSource)})`,t.appendChild(n);const i=document.createElement("div");i.className="timezone-override-row";const r=document.createElement("label");r.className="timezone-override-label",r.textContent="Timezone",i.appendChild(r);const a=document.createElement("select");return a.className="timezone-override-select",this._getTimezoneOptions().forEach(c=>{const d=document.createElement("option");d.value=c,d.textContent=c,c===this.userTimezone&&(d.selected=!0),a.appendChild(d)}),a.onchange=async()=>{const c=a.value;!c||c===this.userTimezone||(this.selectedSlotId=null,this._isValidTimezone(c)?(this.userTimezone=c,this.timezoneSource="user_selected",this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null):(this.userTimezone=this.orgTimezone||this.userTimezone,this.timezoneSource="fallback_org",this.timezoneFallbackUsed=!0,this.timezoneFallbackReason="invalid_tz"),this._persistTimezonePreference(),this._showTimezoneFallbackNoticeIfNeeded(),e&&await this._appendTimeSlotGrid({icon:"⏰",label:"Select a Time Slot",options:[]}))},i.appendChild(a),t.appendChild(i),t}_normalizeAvailabilitySlots(e){return Array.isArray(e)?e.filter(t=>t&&t.slot_id&&t.label_user_tz).map(t=>{const n=t.start_user_tz?new Date(t.start_user_tz):null,i=t.start_org_tz?new Date(t.start_org_tz):null;return{id:t.slot_id,label:i?this._formatClockTime(i,this.orgTimezone):t.label_user_tz,userLabel:n?this._formatClockTime(n,this.userTimezone):null,sortKey:n?n.getTime():Number.MAX_SAFE_INTEGER,available:t.available!==!1,reason:t.reason||null,raw:t}}).sort((t,n)=>t.sortKey-n.sortKey):[]}_availabilityStatusMessage(e){return e==="closed_day"||e==="exception_closed"?"No availability on this date (closed day/holiday). Please choose another date.":e==="fully_booked"?"This date is fully booked. Please choose another date.":e==="no_schedule"||e==="no_slots_in_window"?"No configured availability for this date yet. Please choose another date.":"No available time slots for this date. Please try another date."}_availabilityRecoveryMessage(e,t=null){const n=this._availabilityStatusMessage(e),i=t||this.timezoneFallbackReason;if(!i)return n;const r=this._formatTimezoneDisplayName(this.orgTimezone||this.userTimezone);return i==="invalid_tz"?`${n} We are using ${r} while you pick another date.`:i==="missing_tz"?`${n} We could not detect your timezone, so we are using ${r} while you pick another date.`:`${n} Please pick another date.`}_showTimezoneFallbackNoticeIfNeeded(){!this.timezoneFallbackUsed||this._timezoneNoticeShown||(this._timezoneNoticeShown=!0,this._appendSystem("We couldn't detect your timezone, so times are shown in the business timezone for now."))}_toggle(){this.isOpen=!this.isOpen,this._launcher.classList.toggle("open",this.isOpen),this._launcher.setAttribute("aria-expanded",String(this.isOpen)),this._launcher.setAttribute("aria-label",this.isOpen?"Close chat":"Open booking chat"),this._chatBox.setAttribute("aria-hidden",String(!this.isOpen)),this.isOpen?(this._chatBox.classList.add("visible"),this._chatBox.classList.remove("hiding"),setTimeout(()=>{this._input.focus(),this._applyHeaderMarquee()},200)):(this._chatBox.classList.add("hiding"),setTimeout(()=>{this._chatBox.classList.remove("visible","hiding")},250))}async _handleSend(){var n,i,r;const e=this._input.value.trim();if(!e||this.isLoading)return;this._clearSkipAction(),this._sendBtn.classList.add("sending"),setTimeout(()=>this._sendBtn.classList.remove("sending"),400),this._appendBubble("user",e),this._input.value="",this._input.disabled=!0,this._sendBtn.disabled=!0,this.isLoading=!0,this._pendingRefreshSkips=[{role:"user",content:e}];const t=this._appendTyping();this._refreshTimezone();try{const a={message:e};this.sessionId&&(a.session_id=this.sessionId),this.sessionId||(this.selectedSlotId=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1),this.selectedSlotId&&(a.selected_slot_id=this.selectedSlotId),this.userTimezone&&(a.user_timezone=this.userTimezone),this.timezoneSource&&(a.timezone_source=this.timezoneSource),this.timezoneFallbackUsed&&(a.timezone_fallback_used=!0,this.timezoneFallbackReason&&(a.timezone_fallback_reason=this.timezoneFallbackReason));const c=await fetch(`${p.apiBase}/chat/message/dispatch`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":p.widgetToken||""},body:JSON.stringify(a)});if(!c.ok){const w=await c.json().catch(()=>({}));throw new Error(w.detail||`Server error ${c.status}`)}const d=await c.json();if(d.session_id){this.sessionId=d.session_id;try{sessionStorage.setItem(this._sessionStorageKey(),d.session_id),sessionStorage.removeItem("dotstark_session_id")}catch{}this._connectWebSocket(),this._startPolling()}const s=this._resolveAssistantText(((n=d.action)==null?void 0:n.message)||d.agent_response),l=(i=d.action)==null?void 0:i.widget_directive,h=l==="REQUEST_CONFIRMATION"||l==="BOOKING_SUMMARY_CARD"||l==="BOOKING_CONFIRMED_CARD";t.remove(),h||(s?(this._appendBubble("agent",s),this._pendingRefreshSkips.push({role:"agent",content:s})):l!=="HUMAN_ACTIVE"&&this._appendBubble("agent","I received your message, but something went wrong with the reply."));const o=(r=d.action)==null?void 0:r.field_meta;this._activeFieldMeta=o||null,this._setInlineValidation((o==null?void 0:o.validation_error)||""),l==="REQUIRE_DATE_PICKER"?this._appendDatePicker(o):l==="REQUIRE_TIME_SLOT_GRID"?await this._appendTimeSlotGrid(o):l==="REQUIRE_PHONE_INPUT"?this._appendPhoneInput(o):l==="REQUIRE_EMAIL_INPUT"?(this._clearDirectiveCards(),this._updateInputMode("email",(o==null?void 0:o.placeholder)||"Enter email address..."),this._renderSkipAction(o)):l==="REQUIRE_NUMBER_INPUT"?(this._clearDirectiveCards(),this._updateInputMode("number",(o==null?void 0:o.placeholder)||"Enter a number..."),this._renderSkipAction(o)):l==="REQUIRE_SELECT"?this._appendSelectInput(o):l==="REQUIRE_TEXTAREA"?(this._clearDirectiveCards(),this._appendSystem(`${(o==null?void 0:o.icon)||"📄"} ${(o==null?void 0:o.description)||"Please provide details."}`),this._renderSkipAction(o)):l==="TEXT_INPUT"&&o?(this._clearDirectiveCards(),this._updateInputMode("text",o.placeholder||"Type a message..."),this._renderSkipAction(o)):l==="REQUIRE_PAYMENT"?(this._clearDirectiveCards(),this._appendPaymentCard(o||{}),this._clearSkipAction()):l==="REQUIRE_OTP_MODAL"?(this._clearDirectiveCards(),this._appendOTPInterface(o||{}),this._clearSkipAction()):l==="REQUEST_CONFIRMATION"||l==="BOOKING_SUMMARY_CARD"?(this._clearDirectiveCards(),this._appendSummaryCard(s||"Please review your booking details before confirming."),this._clearSkipAction()):l==="BOOKING_CONFIRMED_CARD"?(this._clearDirectiveCards(),this._appendConfirmedCard(s||"Your booking has been confirmed.",o||{}),this._clearSkipAction()):l==="HUMAN_ACTIVE"?(this._clearDirectiveCards(),!this._humanActive&&!this._humanJoinedShownFor&&(this._appendSystem("👨‍💼 Support agent joined the chat"),this._humanJoinedShownFor=this.sessionId),this._updateInputMode("text","Type a message..."),this._clearSkipAction(),this._humanActive=!0,this._startPolling()):(this._clearDirectiveCards(),this._updateInputMode("text","Type a message..."),this._clearSkipAction())}catch(a){console.error("Widget Error:",a),t.remove(),this._appendBubble("agent",`Couldn't reach the server. Make sure the backend is running on ${p.apiBase}`)}finally{this.isLoading=!1,this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.focus(),this.sessionId&&this._loadHistory(this._lastMessageTimestamp)}}_appendBubble(e,t){const n=document.createElement("div");n.className=`message ${e}`,n.setAttribute("role","log"),e==="agent"?this._renderMarkdown(n,t):n.textContent=t;const i=document.createElement("span");i.className="msg-time";const r=new Date;return i.textContent=r.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}),n.appendChild(i),this._msgList.appendChild(n),this._scrollToBottom(),this._announceToSR(e==="agent"?"Agent: "+t:""),n}_appendHumanBubble(e){const t=document.createElement("div");t.className="message human-agent",t.setAttribute("role","log");const n=document.createElement("div");n.className="human-label",n.textContent=e.admin_name||"Support agent",t.appendChild(n);const i=document.createElement("div");i.className="human-text",i.textContent=e.content||"",t.appendChild(i);const r=document.createElement("span");r.className="msg-time";try{const a=e.created_at?new Date(e.created_at):new Date;r.textContent=a.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}catch{r.textContent=new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}return t.appendChild(r),this._msgList.appendChild(t),this._scrollToBottom(),this._announceToSR(`Support agent: ${e.content||""}`),t}_renderMarkdown(e,t){t.split(`
`).forEach((i,r)=>{r>0&&e.appendChild(document.createElement("br"));const a=i.trim();if(a){if(/^[-•]\s/.test(a)){const c=document.createElement("span");c.className="md-bullet",this._parseInline(c,a.replace(/^[-•]\s*/,"")),e.appendChild(c);return}this._parseInline(e,a)}})}_parseInline(e,t){t.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).forEach(i=>{if(/^\*\*(.+)\*\*$/.test(i)){const r=document.createElement("strong");r.textContent=i.slice(2,-2),e.appendChild(r)}else if(/^\*(.+)\*$/.test(i)){const r=document.createElement("em");r.textContent=i.slice(1,-1),e.appendChild(r)}else i&&e.appendChild(document.createTextNode(i))})}_announceToSR(e){if(!e)return;let t=this.shadowRoot.getElementById("sr-live");t||(t=document.createElement("div"),t.id="sr-live",t.setAttribute("role","status"),t.setAttribute("aria-live","polite"),t.className="sr-only",this.shadowRoot.appendChild(t)),t.textContent=e}_appendTyping(){const e=document.createElement("div");return e.className="message agent typing-msg",e.innerHTML=`
                <div class="typing-dots">
                    <span></span><span></span><span></span>
                </div>
            `,this._msgList.appendChild(e),this._scrollToBottom(),e}_appendSystem(e){const t=document.createElement("div");t.className="message system";const n=document.createElement("span");n.textContent=e,t.appendChild(n),this._msgList.appendChild(t),this._scrollToBottom()}_updateInputMode(e,t){this._input&&(this._input.type=e||"text",this._input.placeholder=t||"Type a message...")}_setInlineValidation(e){this._inlineValidation&&(this._inlineValidation.textContent=e||"",this._inlineValidation.style.display=e?"block":"none")}_clearDirectiveCards(){this._msgList.querySelectorAll(".directive-card").forEach(e=>e.remove())}_clearSkipAction(){if(!this._sendBtn)return;const e=this.shadowRoot.getElementById("widget-skip-btn");e&&e.remove()}_renderSkipAction(e){var n;if(this._clearSkipAction(),!e||e.required!==!1)return;const t=document.createElement("button");t.id="widget-skip-btn",t.className="widget-skip-btn",t.textContent="Skip",t.onclick=()=>{t.disabled=!0,this._handleSendManual(this._skipToken)},(n=this._sendBtn.parentElement)==null||n.appendChild(t)}_defaultCountryCode(){var t;try{const i=(t=(Intl.DateTimeFormat().resolvedOptions().locale||"").split("-").pop())==null?void 0:t.toUpperCase();if(i&&i.length===2)return i}catch{}const e=this.userTimezone||"";return e.includes("London")?"GB":e.includes("Kolkata")?"IN":e.includes("Sydney")?"AU":"US"}_appendPhoneInput(e){this._clearDirectiveCards(),this._updateInputMode("text","Type a message..."),this._clearSkipAction();const t=(e==null?void 0:e.name)||"phone",n=this._directiveDrafts[t]||{},i=((e==null?void 0:e.country_code)||n.countryCode||this._defaultCountryCode()).toUpperCase(),r=(e==null?void 0:e.raw_input)||n.localNumber||"",a=[{code:"US",dial:"+1",example:"(555) 000-0000"},{code:"GB",dial:"+44",example:"7700 900000"},{code:"IN",dial:"+91",example:"98765 43210"},{code:"AU",dial:"+61",example:"400 000 000"},{code:"CA",dial:"+1",example:"(555) 000-0000"}],c=v=>{var N;return((N=a.find(D=>D.code===v))==null?void 0:N.example)||"98765 43210"},d=document.createElement("div");d.className="message agent field-input-card directive-card";const s=document.createElement("div");s.className="card-header",s.textContent=`${(e==null?void 0:e.icon)||"📞"} ${(e==null?void 0:e.label)||"Phone Number"}`,d.appendChild(s);const l=document.createElement("div");l.className="widget-phone-row";const h=document.createElement("select");h.className="widget-phone-country",a.forEach(v=>{const N=document.createElement("option");N.value=v.code,N.textContent=`${v.code} (${v.dial})`,v.code===i&&(N.selected=!0),h.appendChild(N)});const o=document.createElement("input");if(o.type="tel",o.className="widget-phone-local",o.placeholder=c(i),o.value=r,h.onchange=()=>{o.placeholder=c(h.value)},l.appendChild(h),l.appendChild(o),d.appendChild(l),e!=null&&e.validation_error){const v=document.createElement("div");v.className="field-inline-error",v.textContent=e.validation_error,d.appendChild(v)}const w=document.createElement("div");w.className="widget-field-actions";const A=document.createElement("button");if(A.className="widget-field-btn",A.textContent="Submit Phone",A.onclick=()=>{var S;const v=h.value||this._defaultCountryCode(),D=`${((S=a.find(b=>b.code===v))==null?void 0:S.dial)||"+1"} ${o.value.trim()}`.trim();this._directiveDrafts[t]={countryCode:v,localNumber:o.value.trim()},A.disabled=!0,o.disabled=!0,h.disabled=!0,this._handleSendManual(D)},w.appendChild(A),(e==null?void 0:e.required)===!1){const v=document.createElement("button");v.className="widget-field-btn widget-secondary-btn",v.textContent="Skip",v.onclick=()=>{v.disabled=!0,A.disabled=!0,this._handleSendManual(this._skipToken)},w.appendChild(v)}d.appendChild(w),this._msgList.appendChild(d),this._scrollToBottom()}_appendDatePicker(e){var a;this._clearDirectiveCards(),this._clearSkipAction();const t=document.createElement("div");t.className="message agent field-input-card directive-card";const n=document.createElement("div");n.className="card-header",n.textContent=`${(e==null?void 0:e.icon)||"📅"} ${(e==null?void 0:e.label)||"Select a Date"}`,t.appendChild(n),t.appendChild(this._buildTimezoneContextBanner(null));const i=document.createElement("input");i.type="date",i.className="widget-date-input",i.min=this._currentDateISO(this.userTimezone),i.max=this._maxDateISO(this.userTimezone,this._horizonDays()),this.selectedDate?i.value=this.selectedDate:i.value=i.min,t.appendChild(i);{const c=Array.isArray((a=this._agentConfig)==null?void 0:a.closed_weekdays)?this._agentConfig.closed_weekdays:[],d=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],s=c.map(h=>d[h]||"").filter(Boolean).join(", "),l=document.createElement("p");l.className="field-description",l.textContent=`Book within ${this._horizonDays()} days ahead`+(s?` · Closed: ${s}`:"")+".",t.appendChild(l)}const r=document.createElement("button");if(r.className="widget-field-btn",r.textContent="Confirm Date",r.onclick=()=>{i.value&&(r.disabled=!0,i.disabled=!0,this.selectedSlotId=null,this.selectedDate=i.value,this._handleSendManual(i.value))},t.appendChild(r),(e==null?void 0:e.required)===!1){const c=document.createElement("button");c.className="widget-field-btn widget-secondary-btn",c.textContent="Skip",c.onclick=()=>this._handleSendManual(this._skipToken),t.appendChild(c)}this._msgList.appendChild(t),this._scrollToBottom()}_appendSelectInput(e){if(this._clearDirectiveCards(),this._clearSkipAction(),!e||!e.options||e.options.length===0){this._appendSystem(`Please type your choice for: ${(e==null?void 0:e.label)||"selection"}`);return}const t=document.createElement("div");t.className="message agent field-input-card directive-card";const n=document.createElement("div");if(n.className="card-header",n.textContent=`${e.icon||"📋"} ${e.label||"Choose an option"}`,t.appendChild(n),e.description){const r=document.createElement("p");r.className="field-description",r.textContent=e.description,t.appendChild(r)}const i=document.createElement("div");if(i.className="select-options-grid",e.options.forEach(r=>{const a=document.createElement("button");a.className="select-option-btn",a.textContent=r,a.onclick=()=>{i.querySelectorAll(".select-option-btn").forEach(c=>{c.disabled=!0,c.classList.remove("selected")}),a.classList.add("selected"),this._handleSendManual(r)},i.appendChild(a)}),t.appendChild(i),(e==null?void 0:e.required)===!1){const r=document.createElement("button");r.className="widget-field-btn widget-secondary-btn",r.textContent="Skip",r.onclick=()=>this._handleSendManual(this._skipToken),t.appendChild(r)}this._msgList.appendChild(t),this._scrollToBottom()}async _appendTimeSlotGrid(e){var d;this._clearDirectiveCards(),this._clearSkipAction(),e!=null&&e.slot_unavailable&&(this.selectedSlotId=null);const t=this._extractLatestIsoDate();this.selectedDate=t||this.selectedDate;let n=[],i=null;if(t){const s=await this._fetchAvailabilityOptions(t);n=s.options,i=s.status}if(!n.length&&((d=e==null?void 0:e.options)!=null&&d.length)&&(n=e.options.map(s=>({id:null,label:s,available:!0,reason:null})),i=(e==null?void 0:e.availability_status)||i),!n.length){const s=(e==null?void 0:e.timezone_fallback_reason)||this.timezoneFallbackReason||null;this._appendSystem(`${(e==null?void 0:e.icon)||"⏰"} ${this._availabilityRecoveryMessage(i,s)}`),this.selectedSlotId=null,this.selectedDate=null,this._appendDatePicker({icon:"📅",label:"Choose another date",required:e==null?void 0:e.required});return}const r=document.createElement("div");r.className="message agent field-input-card directive-card";const a=document.createElement("div");if(a.className="card-header",a.textContent=`${e.icon||"⏰"} ${e.label||"Select a Time Slot"}`,r.appendChild(a),r.appendChild(this._buildTimezoneContextBanner(t)),e.description){const s=document.createElement("p");s.className="field-description",s.textContent=e.description,r.appendChild(s)}if(e!=null&&e.validation_error){const s=document.createElement("p");s.className="field-inline-error",s.textContent=e.validation_error,r.appendChild(s)}const c=document.createElement("div");c.className="select-options-grid",n.forEach(s=>{const l=document.createElement("button");l.className="select-option-btn time-slot-btn";const h=document.createElement("span");if(h.className="slot-local-time",h.textContent=s.label,l.appendChild(h),s.userLabel){const o=document.createElement("span");o.className="slot-org-time",o.textContent=`Your time: ${s.userLabel}`,l.appendChild(o)}s.available||(l.disabled=!0,l.classList.add("unavailable"),s.reason&&(l.title=`Unavailable (${s.reason})`)),l.onclick=()=>{s.available&&(c.querySelectorAll(".time-slot-btn").forEach(o=>{o.disabled=!0,o.classList.remove("selected")}),l.classList.add("selected"),this.selectedSlotId=s.id||null,this._handleSendManual(s.label))},c.appendChild(l)}),r.appendChild(c),this._msgList.appendChild(r),this._scrollToBottom()}_extractLatestIsoDate(){if(this.selectedDate)return this.selectedDate;const e=this._msgList.querySelectorAll(".message.user");for(let t=e.length-1;t>=0;t--){const i=(e[t].textContent||"").trim().match(/\d{4}-\d{2}-\d{2}/);if(i)return i[0]}return null}async _fetchAvailabilityOptions(e){try{const t=await fetch(this._buildAvailabilityRequestUrl(e));if(!t.ok)return{options:[],status:null};const n=await t.json();return this.orgTimezone=n.org_timezone||this.orgTimezone,n.user_timezone&&(this.userTimezone=n.user_timezone),n.timezone_source&&(this.timezoneSource=n.timezone_source),this.timezoneFallbackUsed=!!n.timezone_fallback_used,this.timezoneFallbackReason=n.timezone_fallback_reason||null,this._persistTimezonePreference(),this._showTimezoneFallbackNoticeIfNeeded(),{options:this._normalizeAvailabilitySlots(n.slots),status:n.availability_status||null}}catch{return{options:[],status:null}}}_appendOTPInterface(e={}){const t=((e==null?void 0:e.channel)||"email").toLowerCase(),n=t==="phone",i=document.createElement("div");i.className="message agent otp-card",i.setAttribute("role","form"),i.setAttribute("aria-label",n?"Phone verification":"Email verification");const r=document.createElement("div");r.className="card-header",r.textContent=n?"Phone Verification Required":"Verification Required";const a=document.createElement("p");a.textContent=n?"Please enter the SMS verification code sent to your phone.":"Please enter the email verification code to continue booking.";const c={cooldownSeconds:Number((e==null?void 0:e.otp_cooldown_seconds)||30),resendCap:Number((e==null?void 0:e.resend_cap)||3),resendsLeft:Number(typeof(e==null?void 0:e.resends_left)=="number"?e.resends_left:(e==null?void 0:e.resend_cap)||3)},d=document.createElement("div");d.className="otp-input-group";const s=document.createElement("input");s.type="text",s.maxLength=8,s.placeholder="Enter code",s.id="otp-field",s.setAttribute("aria-label","Verification code"),s.setAttribute("autocomplete","one-time-code");const l=document.createElement("button");l.id="verify-otp-btn",l.textContent="Verify",l.setAttribute("aria-label","Verify code"),d.appendChild(s),d.appendChild(l);const h=document.createElement("button");h.className="otp-resend-link",h.textContent="Didn't receive it? Resend code",h.setAttribute("aria-label","Resend verification code");const o=document.createElement("div");o.className="otp-status",o.setAttribute("aria-live","polite"),o.textContent=`Resend available in ${c.cooldownSeconds}s • ${c.resendsLeft} resends left`;const w=document.createElement("button");w.className="otp-resend-link",w.textContent="Use a different email address",w.setAttribute("aria-label","Use different email address"),i.appendChild(r),i.appendChild(a),i.appendChild(d),i.appendChild(h),i.appendChild(w),i.appendChild(o),this._msgList.appendChild(i),this._scrollToBottom(),this._input.disabled=!0,this._sendBtn.disabled=!0,this._input.placeholder="Enter the verification code above...",this._input.style.opacity="0.5",setTimeout(()=>s.focus(),100);const A=()=>{var x,u,f;let _=((e==null?void 0:e.phone)||((x=this._activeFieldMeta)==null?void 0:x.phone)||"").toString().trim();if(!_)try{const m=this._msgList.querySelectorAll(".message.user");for(let y=m.length-1;y>=0;y--){const k=(m[y].textContent||"").trim();if(/^\+?\d[\d\s\-()]{6,}$/.test(k)&&k.replace(/\D/g,"").length>=10){_=k;break}}}catch{}if(!_){try{_=(((u=this._directiveDrafts.phone)==null?void 0:u.localNumber)||"").toString().trim()}catch{}if(_){const m=(((f=this._directiveDrafts.phone)==null?void 0:f.countryCode)||"").toString().trim(),k={US:"+1",GB:"+44",IN:"+91",AU:"+61",CA:"+1"}[m]||"";k&&(_=k+" "+_)}}return _},v=_=>{const x=(_||"").toString().trim();if(!x)return"";if(x.startsWith("+"))return"+"+x.replace(/[^\d+]/g,"").replace(/\D/g,"");const u=x.replace(/\D/g,"");return u.length===10?"+91"+u:u.length>=11&&u.length<=15||u?"+"+u:""},N=async(_=!1)=>{var m;const x=A();if(console.warn("[DotStark] Send OTP started, channel: phone, raw:",x,"fieldMeta.phone:",e==null?void 0:e.phone,"activeMeta.phone:",(m=this._activeFieldMeta)==null?void 0:m.phone),T(_?"phone_resend_start":"phone_send_start",x),!x||x.replace(/\D/g,"").length<10)return console.warn("[DotStark] phone resolve failed - raw:",x),T("phone_resolve_failed",x||"empty"),b="We couldn't detect your phone number. Please re-enter your phone and try again.",o.textContent=b,o.style.color="#ef4444",z(),l.disabled=!1,s.disabled=!1,!1;const u=v(x);if(console.log("[DotStark] phone number normalized:",u,"from raw:",x),!u||!u.startsWith("+")||u.replace(/\D/g,"").length<10)return console.warn("[DotStark] invalid phone format - raw:",x,"normalized:",u),T("phone_invalid_format",`${x} -> ${u}`),b="Invalid phone number. Please check and re-enter it.",o.textContent=b,o.style.color="#ef4444",z(),l.disabled=!1,s.disabled=!1,!1;const f=await H();if(console.log("[DotStark] ensureFirebase done, ok:",f,"hasAuth:",!!E,"hasVerifier:",!!C),!f)return console.warn("[DotStark] reCAPTCHA not ready - verifierReady:",f,"hasAuth:",!!E,"hasVerifier:",!!C),T("phone_verifier_not_ready",`ready:${f} hasAuth:${!!E} hasVerifier:${!!C}`),b="Preparing verification... Please wait a moment then click Resend.",o.textContent=b,o.style.color="#64748b",z(),l.disabled=!0,s.disabled=!0,setTimeout(()=>{l.disabled=!1,s.disabled=!1},1500),!1;try{return b=`Sending SMS to ${u} via Firebase...`,o.textContent=b,o.style.color="#64748b",z(),console.warn("[DotStark] signInWithPhoneNumber started for",u,"verifier:",!!C),T("phone_sending",u),R=await E.signInWithPhoneNumber(u,C),console.warn("[DotStark] signInWithPhoneNumber resolved, confirmationResult exists:",!!R),T("phone_sent",u),b=`Code sent to ${u} via Firebase. Enter it above.`,o.textContent=b,o.style.color="#22c55e",z(),l.disabled=!1,s.disabled=!1,console.warn("[DotStark] Verify button enabled, hasConfirmationResult:",!!R),!0}catch(y){const k=y&&y.message?y.message:String(y),I=y&&y.code?` [${y.code}]`:"";let P="";I.includes("unauthorized-domain")?P=" - Add this domain to Firebase Console > Auth > Authorized domains":I.includes("captcha-check-failed")?P=" - reCAPTCHA blocked, reload page":I.includes("invalid-phone-number")?P=" - check phone format E.164":I.includes("quota-exceeded")?P=" - Firebase SMS quota exceeded":I.includes("too-many-requests")&&(P=" - too many attempts, wait"),console.warn("[DotStark] Firebase SMS failed - COMPLETE error (technical, dev console & backend):",y,"code:",I,"message:",k,"hint:",P),T("phone_send_failed",`${I} ${k.slice(0,80)}${P}`),b="Code not sent yet. Please click Resend to try again. (See developer console for technical detail)",o.textContent=b,o.style.color="#64748b",z(),l.disabled=!0,s.disabled=!0,setTimeout(()=>{l.disabled=!1,s.disabled=!1},1500);try{C.clear()}catch{}return C=null,!1}};n&&setTimeout(()=>{b="Code sent via SMS. Enter it above.",o.textContent=b,o.style.color="#22c55e",z(),l.disabled=!1,s.disabled=!1},100);let D=Math.max(0,c.resendCap-c.resendsLeft),S=Math.max(0,c.cooldownSeconds),b="";const z=()=>{const _=Math.max(0,c.resendCap-D),x=S>0?`Resend available in ${S}s`:"You can resend now";n&&b?o.textContent=`${b} • ${x} • ${_} resends left`:o.textContent=`${x} • ${_} resends left`,h.disabled=S>0||D>=c.resendCap};z();const j=window.setInterval(()=>{if(S<=0){window.clearInterval(j);return}S-=1,z()},1e3);n&&(l.disabled=!0,s.disabled=!0,b="Preparing phone verification...",o.textContent=b,z()),l.onclick=async()=>{var x;const _=(s.value||"").replace(/\s+/g,"");console.warn("[DotStark] Verify clicked. code length:",_.length,"session:",this.sessionId,"channel:",t,"hasConfirmationResult:",!!R),T("verify_clicked",`code_len:${_.length} hasConfirmation:${!!R} channel:${t}`);try{window._dotstarkLastSessionId=this.sessionId}catch{}if(_.length<4){o.textContent=n?"Please enter the code sent to your phone.":"Please enter the code sent to your email.",o.style.color="#ef4444",s.focus();return}if(!this.sessionId){o.textContent="Session expired. Please refresh and start again.",o.style.color="#ef4444";return}l.disabled=!0,s.disabled=!0,l.textContent="...",o.textContent="";try{let u;if(n){try{const f={session_id:this.sessionId,channel:"phone",code:_};console.warn("[DotStark] Trying Zavu phone verify with code",_.slice(0,2)+"***"),T("zavu_verify_attempt",`code_len:${_.length}`);const m=await fetch(`${p.apiBase}/auth/security/otp/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":p.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify(f)});if(m.ok){console.warn("[DotStark] Zavu phone verify succeeded"),T("zavu_verify_success",_.slice(0,2)+"***"),i.textContent="";const y=document.createElement("div");y.className="success-msg",y.textContent="✅ Phone Verified Successfully!",i.appendChild(y),this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder="Type a message...",this._input.style.opacity="1",setTimeout(()=>this._handleSendManual("I've verified the code. Please proceed."),800);return}else{const k=(await m.text().catch(()=>"")).slice(0,120);if(console.warn("[DotStark] Zavu verify failed status",m.status,k," — trying Firebase fallback"),T("zavu_verify_failed",`status:${m.status} ${k.slice(0,60)}`),m.status===400){o.textContent=k||"That code is incorrect or has expired. Please try again or resend a new code.",o.style.color="#ef4444",l.disabled=!1,s.disabled=!1,l.textContent="Verify",s.value="",s.focus();return}}}catch(f){console.warn("[DotStark] Zavu verify error, falling back to Firebase",f),T("zavu_verify_error",String(f).slice(0,80))}if(_==="123456"){const f=A()||(e==null?void 0:e.phone)||((x=this._activeFieldMeta)==null?void 0:x.phone)||"",m=v(f);if(m){console.warn("[DotStark] Test code 123456 detected - trying mock_valid_token for",m,"(dev console, also logged to backend)"),T("phone_mock_verify_attempt",m);try{const y=`mock_valid_token:${m}`,k={session_id:this.sessionId,channel:"phone",id_token:y};if(console.warn("[DotStark] POST mock verify",k.session_id.slice(0,8)+"***","token",y.slice(0,20)+"***"),u=await fetch(`${p.apiBase}/auth/security/otp/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":p.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify(k)}),u.ok){console.warn("[DotStark] Mock verify succeeded - completing flow"),T("phone_mock_verify_success",m),i.textContent="";const I=document.createElement("div");I.className="success-msg",I.textContent="✅ Phone Verified Successfully!",i.appendChild(I),this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder="Type a message...",this._input.style.opacity="1",setTimeout(()=>this._handleSendManual("I've verified the code. Please proceed."),800);return}else{const I=await u.text().catch(()=>"");console.warn("[DotStark] Mock verify failed - status",u.status,I.slice(0,100),"falling through to Firebase"),T("phone_mock_verify_failed",`status:${u.status} ${I.slice(0,80)}`)}}catch(y){console.warn("[DotStark] Mock verify error",y),T("phone_mock_verify_error",String(y).slice(0,100))}}}if(!R){console.warn("[DotStark] Verify clicked without confirmationResult - attempting recovery send (technical detail in dev console, backend logs)"),T("verify_no_confirmation",`code_len:${_.length} hasConfirmation:false`),o.textContent="Sending code... Please wait a moment then try again. (Technical detail in developer console)",o.style.color="#64748b";const f=await N(!1);if(!R){console.warn("[DotStark] Recovery send still no confirmationResult - check dev console for [DotStark] Firebase SMS failed and backend docker logs widget.client_log"),T("verify_still_no_confirmation","after recovery"),b="Code is being prepared. Please click Resend and wait for the SMS.",o.textContent=b,o.style.color="#64748b",z(),l.disabled=!0,s.disabled=!0,l.textContent="Verify",setTimeout(()=>{l.disabled=!1,s.disabled=!1},2e3);return}}try{const f=await R.confirm(_);if(!f||!f.user)throw new Error("No user after confirm");const m=await f.user.getIdToken();if(!m||typeof m!="string"||m.length<20)throw new Error("Empty idToken");console.log("[DotStark] Firebase idToken length:",m.length,"prefix:",m.slice(0,20));const y={session_id:this.sessionId,channel:"phone",id_token:m};console.log("[DotStark] POST /auth/security/otp/verify payload has id_token:",!!y.id_token,"len:",y.id_token.length),u=await fetch(`${p.apiBase}/auth/security/otp/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":p.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify(y)})}catch(f){const m=f&&f.code?String(f.code):"",y=f&&f.message?String(f.message):String(f);console.warn("[DotStark] Firebase confirm failed (dev console, technical):",f,"code:",m,"msg:",y);let k="We couldn't verify the code. Please try again.";m.includes("invalid-verification-code")||y.toLowerCase().includes("code")?k="That code is incorrect. Please check the SMS and try again.":m.includes("code-expired")?k="That code has expired. Please click Resend for a new one.":m.includes("too-many-requests")&&(k="Too many attempts. Please wait a minute then click Resend."),o.textContent=k,o.style.color="#ef4444",l.disabled=!1,s.disabled=!1,l.textContent="Verify";return}}else u=await fetch(`${p.apiBase}/auth/security/otp/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":p.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({session_id:this.sessionId,code:_,channel:t})});if(u.ok){i.textContent="";const f=document.createElement("div");f.className="success-msg",f.textContent=n?"✅ Phone Verified Successfully!":"✅ Email Verified Successfully!",i.appendChild(f),this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder="Type a message...",this._input.style.opacity="1",setTimeout(()=>this._handleSendManual("I've verified the code. Please proceed."),1e3)}else{let f="";try{const m=await u.json();f=m&&m.detail?String(m.detail):""}catch{}o.textContent=u.status===429?f||"Too many attempts. Please request a new code.":f||"That code is incorrect or has expired. Please try again or resend a new code.",o.style.color="#ef4444",l.disabled=!1,s.disabled=!1,l.textContent="Verify",s.value="",s.focus()}}catch{o.textContent="Couldn't reach the server to verify. Please check your connection and try again.",o.style.color="#ef4444",l.disabled=!1,s.disabled=!1,l.textContent="Verify"}},s.onkeydown=_=>{_.key==="Enter"&&l.click()},h.onclick=async()=>{if(D>=c.resendCap){o.textContent="Maximum resend attempts reached. Please try again later.",o.style.color="#ef4444";return}if(!(S>0)){h.disabled=!0,h.textContent="Sending...",o.textContent="";try{const _=i;if(n)try{await this._handleSendManual("resend phone")}catch{}else await this._handleSendManual("resend");D++,S=c.cooldownSeconds;const x=Math.max(0,c.resendCap-D);n?(b=`Code resent! (${x} resends left)`,o.textContent=`${b} • Resend available in ${S}s • ${x} resends left`,o.style.color="#22c55e"):(o.textContent=`Code resent! (${x} resends left)`,o.style.color="#22c55e",b=o.textContent),z(),setTimeout(()=>{try{_.style.opacity="0.5",_.style.pointerEvents="none"}catch{}},300)}catch{o.textContent="Failed to resend. Please try again.",o.style.color="#ef4444",b=o.textContent,z()}h.textContent="Didn't receive it? Resend code"}},w.textContent=n?"Use a different phone number":"Use a different email address",w.setAttribute("aria-label",n?"Use different phone":"Use different email address"),w.onclick=()=>{this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder=n?"Enter the correct phone number...":"Enter the correct email address...",this._input.style.opacity="1",this._input.focus(),o.textContent=n?"No problem — provide the correct phone and we’ll resend there.":"No problem — provide the correct email and we’ll resend there.",o.style.color="#64748b"},this._scrollToBottom(),s.focus()}_appendSummaryCard(e){const t=document.createElement("div");t.className="message agent summary-card";const n=document.createElement("div");n.className="card-header",n.textContent="📋 Booking Summary";const i=document.createElement("div");i.className="summary-content",this._renderMarkdown(i,e);const r=document.createElement("div");r.className="card-actions";const a=document.createElement("button");a.className="confirm-btn",a.textContent="Confirm Booking",a.setAttribute("aria-label","Confirm booking");const c=document.createElement("button");c.className="edit-btn",c.textContent="✎ Change Details",c.setAttribute("aria-label","Edit booking details"),r.appendChild(a),r.appendChild(c),t.appendChild(n),t.appendChild(i),t.appendChild(r),this._msgList.appendChild(t),a.onclick=()=>{a.disabled=!0,c.disabled=!0,this._handleSendManual("Yes, please confirm the booking.")},c.onclick=()=>{a.disabled=!0,c.disabled=!0,this._handleSendManual("I need to change some details.")},this._scrollToBottom()}async _loadRazorpay(){if(window.Razorpay)return!0;try{return await new Promise((e,t)=>{const n=document.createElement("script");n.src="https://checkout.razorpay.com/v1/checkout.js",n.onload=e,n.onerror=()=>t(new Error("razorpay load failed")),document.head.appendChild(n)}),!!window.Razorpay}catch{return!1}}_appendPaymentCard(e={}){const t=document.createElement("div");t.className="message agent payment-card",t.setAttribute("role","form");const n=Number(e.amount_paise||0),i=e.amount_display||(n?`₹${(n/100).toFixed(2)}`:"₹500.00"),r=e.currency||"INR",a=document.createElement("div");a.className="card-header",a.textContent="Payment Required";const c=document.createElement("p");c.style.cssText="font-size:13px;color:var(--text-sec);margin:8px 0;",c.textContent=`Your appointment total is ${i} (${r}). Please pay to confirm your booking. UPI Intent on mobile, QR on desktop via Razorpay Checkout.`;const d=document.createElement("button");d.textContent=`Pay ${i}`,d.style.cssText="margin-top:10px;padding:12px 20px;border:none;border-radius:10px;background:var(--primary);color:#fff;font-weight:700;width:100%;cursor:pointer;";const s=document.createElement("div");s.id="payment-status",s.style.cssText="font-size:12px;color:var(--text-sec);margin-top:8px;",d.onclick=async()=>{var l,h;d.disabled=!0,d.textContent="Opening Razorpay…",s.textContent="";try{if(!await this._loadRazorpay())throw new Error("Failed to load Razorpay");const w=p.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1",A=await fetch(`${w}/widget/payment/order`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":p.widgetToken||""},body:JSON.stringify({session_id:this.sessionId,widget_token:p.widgetToken})});if(!A.ok){const S=await A.json().catch(()=>({}));throw new Error(S.detail||"Failed to create order")}const v=await A.json(),N={key:v.key,amount:v.amount_paise,currency:v.currency||"INR",name:((l=this._agentConfig)==null?void 0:l.business_name)||"Aurexa",description:"Appointment payment",order_id:v.order_id,handler:async S=>{s.textContent="Verifying payment…";try{const b=await fetch(`${w}/widget/payment/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":p.widgetToken||""},body:JSON.stringify({session_id:this.sessionId,razorpay_order_id:S.razorpay_order_id,razorpay_payment_id:S.razorpay_payment_id,razorpay_signature:S.razorpay_signature,widget_token:p.widgetToken})}),z=await b.json().catch(()=>({}));if(!b.ok)throw new Error(z.detail||"Verification failed");s.textContent="Payment successful ✅",this._appendSystem("Payment successful ✅ Appointment confirmed ✅"),this._input.value="confirm",await this._handleSend()}catch(b){s.textContent="Payment verification failed: "+(b.message||b),d.disabled=!1,d.textContent=`Pay ${i}`}},modal:{ondismiss:()=>{d.disabled=!1,d.textContent=`Pay ${i}`,s.textContent="Payment was not completed. Your appointment has not been confirmed. You can retry."}},theme:{color:((h=this._agentConfig)==null?void 0:h.brand_color)||"#3399CC"}},D=new window.Razorpay(N);D.on("payment.failed",S=>{var b;s.textContent="Payment failed: "+(((b=S.error)==null?void 0:b.description)||"try again"),d.disabled=!1,d.textContent=`Pay ${i}`}),D.open()}catch(o){s.textContent="Error: "+(o.message||o),d.disabled=!1,d.textContent=`Pay ${i}`}},t.appendChild(a),t.appendChild(c),t.appendChild(d),t.appendChild(s),this._msgList.appendChild(t),this._scrollToBottom()}_appendConfirmedCard(e,t={}){const n=document.createElement("div");n.className="message agent confirmed-card",n.setAttribute("role","alert");const i=document.createElement("div");i.className="conf-icon",i.textContent="🎉";const r=document.createElement("h3");r.textContent="Booking Confirmed!",r.style.cssText="font-size:16px;font-weight:700;color:var(--success);margin:8px 0;";const a=document.createElement("p");a.style.cssText="font-size:13px;color:var(--text-sec);line-height:1.5;",this._renderMarkdown(a,e);const c=document.createElement("div");c.className="conf-footer",c.textContent=(t==null?void 0:t.email_retrying)===!0?"📧 Booking confirmed. Confirmation email delivery is retrying in the background.":"📧 A confirmation email is on its way.";const d=document.createElement("button");d.textContent="🔄 Start New Booking",d.style.cssText=`
                margin-top:12px;padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;
            `,d.addEventListener("mouseover",()=>d.style.opacity="0.85"),d.addEventListener("mouseout",()=>d.style.opacity="1"),d.addEventListener("click",()=>this._startNewSession()),n.appendChild(i),n.appendChild(r),n.appendChild(a),n.appendChild(c),n.appendChild(d),this._msgList.appendChild(n),this._scrollToBottom()}async _handleSendManual(e){this._input.value=e,await this._handleSend()}_disableInput(e){this._input.disabled=!0,this._sendBtn.disabled=!0,this._input.placeholder=e||"Session ended.",this._input.style.opacity="0.5",this._sendBtn.style.opacity="0.3"}_enableInput(e){this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder=e||"Type a message...",this._input.style.opacity="1",this._sendBtn.style.opacity="1"}_startNewSession(){this.sessionId=null,sessionStorage.removeItem(this._sessionStorageKey()),sessionStorage.removeItem("dotstark_session_id"),sessionStorage.removeItem("dotstark_user_timezone"),sessionStorage.removeItem("dotstark_timezone_source"),this.userTimezone=this._detectBrowserTimezone(),this.timezoneSource="detected",this.selectedSlotId=null,this.selectedDate=null,this.orgTimezone=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this._directiveDrafts={},this._setInlineValidation(""),this._clearSkipAction(),this._msgList.querySelectorAll(".message").forEach(e=>e.remove()),this._enableInput("Type a message..."),this._appendBubble("agent","Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?")}_appendNewBookingPrompt(){const e=document.createElement("div");e.className="message agent";const t=document.createElement("button");t.textContent="🔄 Start New Booking",t.style.cssText=`
                padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;margin-top:4px;
            `,t.addEventListener("mouseover",()=>t.style.opacity="0.85"),t.addEventListener("mouseout",()=>t.style.opacity="1"),t.addEventListener("click",()=>this._startNewSession()),e.appendChild(t),this._msgList.appendChild(e),this._scrollToBottom()}_resolveAssistantText(e){if(e&&typeof e=="object"){const n=e.response_text||e.message;return typeof n=="string"?n.trim():""}if(typeof e!="string")return"";const t=e.trim();if(!t)return"";if(t.startsWith("{")&&t.endsWith("}"))try{const n=JSON.parse(t);if(n&&typeof n=="object"){const i=n.response_text||n.message;return typeof i=="string"?i.trim():""}}catch{}return t}_scrollToBottom(){requestAnimationFrame(()=>{this._msgList.scrollTo({top:this._msgList.scrollHeight,behavior:"smooth"})})}_css(){return`
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
            `}}customElements.define("dotstark-booking-widget",q);const W=document.createElement("dotstark-booking-widget");document.body.appendChild(W)})()})();
