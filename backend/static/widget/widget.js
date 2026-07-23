(function(){"use strict";(function(){function y(f){try{return f&&f.src?`${new URL(f.src,window.location.href).origin}/api/v1`:`${window.location.origin}/api/v1`}catch{return"/api/v1"}}function w(){const f=document.querySelectorAll("script[data-widget-token]");if(!f.length){const n=document.querySelectorAll("script[data-api-key]");if(!n.length)return null;const i=n[n.length-1],o=y(i);return{widgetToken:i.getAttribute("data-api-key"),apiBase:i.getAttribute("data-api-base")||o,primaryColor:i.getAttribute("data-color")||null}}const e=f[f.length-1],t=y(e);return{widgetToken:e.getAttribute("data-widget-token"),apiBase:e.getAttribute("data-api-base")||t,primaryColor:e.getAttribute("data-color")||null}}const m=w()||window.DotStarkBookingConfig||{widgetToken:null,apiBase:y(document.currentScript)};class k extends HTMLElement{constructor(){super(),this.attachShadow({mode:"open"}),this.isOpen=!1,this.sessionId=null,this.isLoading=!1,this._messages=[],this._agentConfig=null,this.userTimezone=this._detectBrowserTimezone(),this.timezoneSource="detected",this.selectedSlotId=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this.orgTimezone=null,this.selectedDate=null,this._directiveDrafts={},this._activeFieldMeta=null,this._skipToken="__DOTSTARK_SKIP__"}_sessionStorageKey(){const e=(m.widgetToken||"anonymous").trim()||"anonymous",t=(m.apiBase||"").trim()||"default";return`dotstark_session_id:${e}:${t}`}async connectedCallback(){try{const e=this._sessionStorageKey();m.forceNewSession===!0?(sessionStorage.removeItem(e),sessionStorage.removeItem("dotstark_session_id"),this.sessionId=null):this.sessionId=sessionStorage.getItem(e)||null;const t=sessionStorage.getItem("dotstark_user_timezone"),n=sessionStorage.getItem("dotstark_timezone_source");t&&(this.userTimezone=t,this.timezoneSource=n==="user_selected"?"user_selected":"detected")}catch{}await this._fetchAgentConfig(),this._buildShell()}async _fetchAgentConfig(){if(m.widgetToken)try{const e=m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1",t=await fetch(`${e}/widget/config?widget_token=${encodeURIComponent(m.widgetToken)}`);t.ok&&(this._agentConfig=await t.json())}catch{}}async _loadHistory(){if(!this.sessionId||!m.widgetToken)return!1;try{const t=`${m.apiBase.replace(/\/api\/v1\/?$/,"")+"/api/v1"}/widget/history?session_id=${encodeURIComponent(this.sessionId)}&widget_token=${encodeURIComponent(m.widgetToken)}`,n=await fetch(t);if(!n.ok)return!1;const i=await n.json();if(!i||i.length===0)return!1;for(const o of i){const a=o.role==="agent"?this._resolveAssistantText(o.content):String(o.content||"");a&&this._appendBubble(o.role,a)}return!0}catch{return!1}}_buildShell(){var t,n,i;if(this.shadowRoot.innerHTML=`
                <style>${this._css()}</style>

                <div class="chat-container" id="chat-box" role="dialog" aria-label="Booking Assistant Chat" aria-hidden="true">
                    <div class="header">
                        <div class="header-avatar" id="avatar" aria-hidden="true">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round"><path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10H5a2 2 0 0 0-2 2v1a7 7 0 0 0 14 0v-1a2 2 0 0 0-2-2z"/><line x1="12" y1="18" x2="12" y2="22"/></svg>
                        </div>
                        <div class="header-info">
                            <h3 id="agent-name-title">${((t=this._agentConfig)==null?void 0:t.agent_name)||"Booking Assistant"}</h3>
                            <p><span class="status-dot"></span> Online — Ready to help</p>
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
            `,this._launcher=this.shadowRoot.getElementById("launcher"),this._chatBox=this.shadowRoot.getElementById("chat-box"),this._input=this.shadowRoot.getElementById("user-input"),this._sendBtn=this.shadowRoot.getElementById("send-trigger"),this._msgList=this.shadowRoot.getElementById("message-list"),this._inlineValidation=this.shadowRoot.getElementById("widget-inline-validation"),this._launcher.onclick=()=>this._toggle(),this._launcher.onkeydown=o=>{(o.key==="Enter"||o.key===" ")&&(o.preventDefault(),this._toggle())},this.shadowRoot.getElementById("close-btn").onclick=()=>this._toggle(),this._sendBtn.onclick=()=>this._handleSend(),this._input.onkeydown=o=>{o.key==="Enter"&&!o.shiftKey&&(o.preventDefault(),this._handleSend()),o.key==="Escape"&&this._toggle()},this._chatBox.onkeydown=o=>{o.key==="Escape"&&this.isOpen&&this._toggle()},this.sessionId)this._loadHistory().then(o=>{var a;if(!o){const s=((a=this._agentConfig)==null?void 0:a.greeting_message)||"Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?";this._appendBubble("agent",s)}});else{const o=((n=this._agentConfig)==null?void 0:n.greeting_message)||"Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?";this._appendBubble("agent",o)}const e=((i=this._agentConfig)==null?void 0:i.brand_color)||m.primaryColor;e&&this._chatBox.style.setProperty("--brand-color",e)}_detectBrowserTimezone(){try{return Intl.DateTimeFormat().resolvedOptions().timeZone||null}catch{return null}}_persistTimezonePreference(){try{this.userTimezone?(sessionStorage.setItem("dotstark_user_timezone",this.userTimezone),sessionStorage.setItem("dotstark_timezone_source",this.timezoneSource)):(sessionStorage.removeItem("dotstark_user_timezone"),sessionStorage.removeItem("dotstark_timezone_source"))}catch{}}_buildAvailabilityRequestUrl(e){const t=new URLSearchParams;return t.set("date",e),m.widgetToken&&t.set("widget_token",m.widgetToken),this.userTimezone&&t.set("tz",this.userTimezone),this.timezoneSource&&t.set("timezone_source",this.timezoneSource),`${m.apiBase}/widget/availability?${t.toString()}`}_formatTimezoneSource(e){return e==="user_selected"?"you selected":e==="fallback_org"?"business fallback":"browser detected"}_formatTimezoneDisplayName(e){var n;if(!e)return"UTC";const t=(n=e.split("/").pop())==null?void 0:n.replace(/_/g," ");return t?`${e} (${t})`:e}_isValidTimezone(e){if(!e||typeof e!="string")return!1;try{return new Intl.DateTimeFormat("en-US",{timeZone:e}).format(new Date),!0}catch{return!1}}_formatClockTime(e,t=null){const n={hour:"numeric",minute:"2-digit",hour12:!0};t&&this._isValidTimezone(t)&&(n.timeZone=t);try{return new Intl.DateTimeFormat([],n).format(e)}catch{return e.toLocaleTimeString([],{hour:"numeric",minute:"2-digit",hour12:!0})}}_currentDateISO(e=null){var a,s,d;const t=new Date;if(e&&this._isValidTimezone(e))try{const l=new Intl.DateTimeFormat("en-US",{timeZone:e,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(t),r=(a=l.find(h=>h.type==="year"))==null?void 0:a.value,p=(s=l.find(h=>h.type==="month"))==null?void 0:s.value,c=(d=l.find(h=>h.type==="day"))==null?void 0:d.value;if(r&&p&&c)return`${r}-${p}-${c}`}catch{}const n=t.getFullYear(),i=String(t.getMonth()+1).padStart(2,"0"),o=String(t.getDate()).padStart(2,"0");return`${n}-${i}-${o}`}_getTimezoneOptions(){const e=["UTC","America/New_York","America/Los_Angeles","Europe/London","Asia/Kolkata","Asia/Dubai","Asia/Singapore","Australia/Sydney"];return Array.from(new Set([this.userTimezone,this.orgTimezone,this._detectBrowserTimezone(),...e].filter(Boolean)))}_buildTimezoneContextBanner(e=null){const t=document.createElement("div");t.className="timezone-context-banner";const n=document.createElement("div");n.className="timezone-context-text",n.textContent=`Times shown in ${this._formatTimezoneDisplayName(this.userTimezone)} (${this._formatTimezoneSource(this.timezoneSource)})`,t.appendChild(n);const i=document.createElement("div");i.className="timezone-override-row";const o=document.createElement("label");o.className="timezone-override-label",o.textContent="Timezone",i.appendChild(o);const a=document.createElement("select");return a.className="timezone-override-select",this._getTimezoneOptions().forEach(s=>{const d=document.createElement("option");d.value=s,d.textContent=s,s===this.userTimezone&&(d.selected=!0),a.appendChild(d)}),a.onchange=async()=>{const s=a.value;!s||s===this.userTimezone||(this.selectedSlotId=null,this._isValidTimezone(s)?(this.userTimezone=s,this.timezoneSource="user_selected",this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null):(this.userTimezone=this.orgTimezone||this.userTimezone,this.timezoneSource="fallback_org",this.timezoneFallbackUsed=!0,this.timezoneFallbackReason="invalid_tz"),this._persistTimezonePreference(),this._showTimezoneFallbackNoticeIfNeeded(),e&&await this._appendTimeSlotGrid({icon:"⏰",label:"Select a Time Slot",options:[]}))},i.appendChild(a),t.appendChild(i),t}_normalizeAvailabilitySlots(e){return Array.isArray(e)?e.filter(t=>t&&t.slot_id&&t.label_user_tz).map(t=>{const n=t.start_user_tz?new Date(t.start_user_tz):null,i=t.start_org_tz?new Date(t.start_org_tz):null;return{id:t.slot_id,label:t.label_user_tz||(n?this._formatClockTime(n,this.userTimezone):""),orgLabel:i?this._formatClockTime(i,this.orgTimezone):null,sortKey:n?n.getTime():Number.MAX_SAFE_INTEGER,available:t.available!==!1,reason:t.reason||null,raw:t}}).sort((t,n)=>t.sortKey-n.sortKey):[]}_availabilityStatusMessage(e){return e==="closed_day"||e==="exception_closed"?"No availability on this date (closed day/holiday). Please choose another date.":e==="fully_booked"?"This date is fully booked. Please choose another date.":e==="no_schedule"||e==="no_slots_in_window"?"No configured availability for this date yet. Please choose another date.":"No available time slots for this date. Please try another date."}_availabilityRecoveryMessage(e,t=null){const n=this._availabilityStatusMessage(e),i=t||this.timezoneFallbackReason;if(!i)return n;const o=this._formatTimezoneDisplayName(this.orgTimezone||this.userTimezone);return i==="invalid_tz"?`${n} We are using ${o} while you pick another date.`:i==="missing_tz"?`${n} We could not detect your timezone, so we are using ${o} while you pick another date.`:`${n} Please pick another date.`}_showTimezoneFallbackNoticeIfNeeded(){!this.timezoneFallbackUsed||this._timezoneNoticeShown||(this._timezoneNoticeShown=!0,this._appendSystem("We couldn't detect your timezone, so times are shown in the business timezone for now."))}_toggle(){this.isOpen=!this.isOpen,this._launcher.classList.toggle("open",this.isOpen),this._launcher.setAttribute("aria-expanded",String(this.isOpen)),this._launcher.setAttribute("aria-label",this.isOpen?"Close chat":"Open booking chat"),this._chatBox.setAttribute("aria-hidden",String(!this.isOpen)),this.isOpen?(this._chatBox.classList.add("visible"),this._chatBox.classList.remove("hiding"),setTimeout(()=>this._input.focus(),200)):(this._chatBox.classList.add("hiding"),setTimeout(()=>{this._chatBox.classList.remove("visible","hiding")},250))}async _handleSend(){var n,i,o;const e=this._input.value.trim();if(!e||this.isLoading)return;this._clearSkipAction(),this._sendBtn.classList.add("sending"),setTimeout(()=>this._sendBtn.classList.remove("sending"),400),this._appendBubble("user",e),this._input.value="",this._input.disabled=!0,this._sendBtn.disabled=!0,this.isLoading=!0;const t=this._appendTyping();try{const a={message:e};this.sessionId&&(a.session_id=this.sessionId),this.sessionId||(this.selectedSlotId=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1),this.selectedSlotId&&(a.selected_slot_id=this.selectedSlotId),this.userTimezone&&(a.user_timezone=this.userTimezone),this.timezoneSource&&(a.timezone_source=this.timezoneSource),this.timezoneFallbackUsed&&(a.timezone_fallback_used=!0,this.timezoneFallbackReason&&(a.timezone_fallback_reason=this.timezoneFallbackReason));const s=await fetch(`${m.apiBase}/chat/message/dispatch`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||""},body:JSON.stringify(a)});if(!s.ok){const h=await s.json().catch(()=>({}));throw new Error(h.detail||`Server error ${s.status}`)}const d=await s.json();if(d.session_id){this.sessionId=d.session_id;try{sessionStorage.setItem(this._sessionStorageKey(),d.session_id),sessionStorage.removeItem("dotstark_session_id")}catch{}}const l=this._resolveAssistantText(((n=d.action)==null?void 0:n.message)||d.agent_response),r=(i=d.action)==null?void 0:i.widget_directive,p=r==="REQUEST_CONFIRMATION"||r==="BOOKING_SUMMARY_CARD"||r==="BOOKING_CONFIRMED_CARD";t.remove(),p||(l?this._appendBubble("agent",l):this._appendBubble("agent","I received your message, but something went wrong with the reply."));const c=(o=d.action)==null?void 0:o.field_meta;this._activeFieldMeta=c||null,this._setInlineValidation((c==null?void 0:c.validation_error)||""),r==="REQUIRE_DATE_PICKER"?this._appendDatePicker(c):r==="REQUIRE_TIME_SLOT_GRID"?await this._appendTimeSlotGrid(c):r==="REQUIRE_PHONE_INPUT"?this._appendPhoneInput(c):r==="REQUIRE_EMAIL_INPUT"?(this._clearDirectiveCards(),this._updateInputMode("email",(c==null?void 0:c.placeholder)||"Enter email address..."),this._renderSkipAction(c)):r==="REQUIRE_NUMBER_INPUT"?(this._clearDirectiveCards(),this._updateInputMode("number",(c==null?void 0:c.placeholder)||"Enter a number..."),this._renderSkipAction(c)):r==="REQUIRE_SELECT"?this._appendSelectInput(c):r==="REQUIRE_TEXTAREA"?(this._clearDirectiveCards(),this._appendSystem(`${(c==null?void 0:c.icon)||"📄"} ${(c==null?void 0:c.description)||"Please provide details."}`),this._renderSkipAction(c)):r==="TEXT_INPUT"&&c?(this._clearDirectiveCards(),this._updateInputMode("text",c.placeholder||"Type a message..."),this._renderSkipAction(c)):r==="REQUIRE_OTP_MODAL"?(this._clearDirectiveCards(),this._appendOTPInterface(c||{}),this._clearSkipAction()):r==="REQUEST_CONFIRMATION"||r==="BOOKING_SUMMARY_CARD"?(this._clearDirectiveCards(),this._appendSummaryCard(l||"Please review your booking details before confirming."),this._clearSkipAction()):r==="BOOKING_CONFIRMED_CARD"?(this._clearDirectiveCards(),this._appendConfirmedCard(l||"Your booking has been confirmed.",c||{}),this._clearSkipAction()):(this._clearDirectiveCards(),this._updateInputMode("text","Type a message..."),this._clearSkipAction())}catch(a){console.error("Widget Error:",a),t.remove(),this._appendBubble("agent",`Couldn't reach the server. Make sure the backend is running on ${m.apiBase}`)}finally{this.isLoading=!1,this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.focus()}}_appendBubble(e,t){const n=document.createElement("div");n.className=`message ${e}`,n.setAttribute("role","log"),e==="agent"?this._renderMarkdown(n,t):n.textContent=t;const i=document.createElement("span");i.className="msg-time";const o=new Date;return i.textContent=o.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"}),n.appendChild(i),this._msgList.appendChild(n),this._scrollToBottom(),this._announceToSR(e==="agent"?"Agent: "+t:""),n}_renderMarkdown(e,t){t.split(`
`).forEach((i,o)=>{o>0&&e.appendChild(document.createElement("br"));const a=i.trim();if(a){if(/^[-•]\s/.test(a)){const s=document.createElement("span");s.className="md-bullet",this._parseInline(s,a.replace(/^[-•]\s*/,"")),e.appendChild(s);return}this._parseInline(e,a)}})}_parseInline(e,t){t.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).forEach(i=>{if(/^\*\*(.+)\*\*$/.test(i)){const o=document.createElement("strong");o.textContent=i.slice(2,-2),e.appendChild(o)}else if(/^\*(.+)\*$/.test(i)){const o=document.createElement("em");o.textContent=i.slice(1,-1),e.appendChild(o)}else i&&e.appendChild(document.createTextNode(i))})}_announceToSR(e){if(!e)return;let t=this.shadowRoot.getElementById("sr-live");t||(t=document.createElement("div"),t.id="sr-live",t.setAttribute("role","status"),t.setAttribute("aria-live","polite"),t.className="sr-only",this.shadowRoot.appendChild(t)),t.textContent=e}_appendTyping(){const e=document.createElement("div");return e.className="message agent typing-msg",e.innerHTML=`
                <div class="typing-dots">
                    <span></span><span></span><span></span>
                </div>
            `,this._msgList.appendChild(e),this._scrollToBottom(),e}_appendSystem(e){const t=document.createElement("div");t.className="message system";const n=document.createElement("span");n.textContent=e,t.appendChild(n),this._msgList.appendChild(t),this._scrollToBottom()}_updateInputMode(e,t){this._input&&(this._input.type=e||"text",this._input.placeholder=t||"Type a message...")}_setInlineValidation(e){this._inlineValidation&&(this._inlineValidation.textContent=e||"",this._inlineValidation.style.display=e?"block":"none")}_clearDirectiveCards(){this._msgList.querySelectorAll(".directive-card").forEach(e=>e.remove())}_clearSkipAction(){if(!this._sendBtn)return;const e=this.shadowRoot.getElementById("widget-skip-btn");e&&e.remove()}_renderSkipAction(e){var n;if(this._clearSkipAction(),!e||e.required!==!1)return;const t=document.createElement("button");t.id="widget-skip-btn",t.className="widget-skip-btn",t.textContent="Skip",t.onclick=()=>{t.disabled=!0,this._handleSendManual(this._skipToken)},(n=this._sendBtn.parentElement)==null||n.appendChild(t)}_defaultCountryCode(){var t;try{const i=(t=(Intl.DateTimeFormat().resolvedOptions().locale||"").split("-").pop())==null?void 0:t.toUpperCase();if(i&&i.length===2)return i}catch{}const e=this.userTimezone||"";return e.includes("London")?"GB":e.includes("Kolkata")?"IN":e.includes("Sydney")?"AU":"US"}_appendPhoneInput(e){this._clearDirectiveCards(),this._updateInputMode("text","Type a message..."),this._clearSkipAction();const t=(e==null?void 0:e.name)||"phone",n=this._directiveDrafts[t]||{},i=((e==null?void 0:e.country_code)||n.countryCode||this._defaultCountryCode()).toUpperCase(),o=(e==null?void 0:e.raw_input)||n.localNumber||"",a=[{code:"US",dial:"+1"},{code:"GB",dial:"+44"},{code:"IN",dial:"+91"},{code:"AU",dial:"+61"},{code:"CA",dial:"+1"}],s=document.createElement("div");s.className="message agent field-input-card directive-card";const d=document.createElement("div");d.className="card-header",d.textContent=`${(e==null?void 0:e.icon)||"📞"} ${(e==null?void 0:e.label)||"Phone Number"}`,s.appendChild(d);const l=document.createElement("div");l.className="widget-phone-row";const r=document.createElement("select");r.className="widget-phone-country",a.forEach(u=>{const _=document.createElement("option");_.value=u.code,_.textContent=`${u.code} (${u.dial})`,u.code===i&&(_.selected=!0),r.appendChild(_)});const p=document.createElement("input");if(p.type="tel",p.className="widget-phone-local",p.placeholder=(e==null?void 0:e.placeholder)||"Enter local phone number",p.value=o,l.appendChild(r),l.appendChild(p),s.appendChild(l),e!=null&&e.validation_error){const u=document.createElement("div");u.className="field-inline-error",u.textContent=e.validation_error,s.appendChild(u)}const c=document.createElement("div");c.className="widget-field-actions";const h=document.createElement("button");if(h.className="widget-field-btn",h.textContent="Submit Phone",h.onclick=()=>{var b;const u=r.value||this._defaultCountryCode(),g=`${((b=a.find(x=>x.code===u))==null?void 0:b.dial)||"+1"} ${p.value.trim()}`.trim();this._directiveDrafts[t]={countryCode:u,localNumber:p.value.trim()},h.disabled=!0,p.disabled=!0,r.disabled=!0,this._handleSendManual(g)},c.appendChild(h),(e==null?void 0:e.required)===!1){const u=document.createElement("button");u.className="widget-field-btn widget-secondary-btn",u.textContent="Skip",u.onclick=()=>{u.disabled=!0,h.disabled=!0,this._handleSendManual(this._skipToken)},c.appendChild(u)}s.appendChild(c),this._msgList.appendChild(s),this._scrollToBottom()}_appendDatePicker(e){this._clearDirectiveCards(),this._clearSkipAction();const t=document.createElement("div");t.className="message agent field-input-card directive-card";const n=document.createElement("div");n.className="card-header",n.textContent=`${(e==null?void 0:e.icon)||"📅"} ${(e==null?void 0:e.label)||"Select a Date"}`,t.appendChild(n),t.appendChild(this._buildTimezoneContextBanner(null));const i=document.createElement("input");i.type="date",i.className="widget-date-input",i.min=this._currentDateISO(this.userTimezone),this.selectedDate?i.value=this.selectedDate:i.value=i.min,t.appendChild(i);const o=document.createElement("button");if(o.className="widget-field-btn",o.textContent="Confirm Date",o.onclick=()=>{i.value&&(o.disabled=!0,i.disabled=!0,this.selectedSlotId=null,this.selectedDate=i.value,this._handleSendManual(i.value))},t.appendChild(o),(e==null?void 0:e.required)===!1){const a=document.createElement("button");a.className="widget-field-btn widget-secondary-btn",a.textContent="Skip",a.onclick=()=>this._handleSendManual(this._skipToken),t.appendChild(a)}this._msgList.appendChild(t),this._scrollToBottom()}_appendSelectInput(e){if(this._clearDirectiveCards(),this._clearSkipAction(),!e||!e.options||e.options.length===0){this._appendSystem(`Please type your choice for: ${(e==null?void 0:e.label)||"selection"}`);return}const t=document.createElement("div");t.className="message agent field-input-card directive-card";const n=document.createElement("div");if(n.className="card-header",n.textContent=`${e.icon||"📋"} ${e.label||"Choose an option"}`,t.appendChild(n),e.description){const o=document.createElement("p");o.className="field-description",o.textContent=e.description,t.appendChild(o)}const i=document.createElement("div");if(i.className="select-options-grid",e.options.forEach(o=>{const a=document.createElement("button");a.className="select-option-btn",a.textContent=o,a.onclick=()=>{i.querySelectorAll(".select-option-btn").forEach(s=>{s.disabled=!0,s.classList.remove("selected")}),a.classList.add("selected"),this._handleSendManual(o)},i.appendChild(a)}),t.appendChild(i),(e==null?void 0:e.required)===!1){const o=document.createElement("button");o.className="widget-field-btn widget-secondary-btn",o.textContent="Skip",o.onclick=()=>this._handleSendManual(this._skipToken),t.appendChild(o)}this._msgList.appendChild(t),this._scrollToBottom()}async _appendTimeSlotGrid(e){var d;this._clearDirectiveCards(),this._clearSkipAction(),e!=null&&e.slot_unavailable&&(this.selectedSlotId=null);const t=this._extractLatestIsoDate();this.selectedDate=t||this.selectedDate;let n=[],i=null;if(t){const l=await this._fetchAvailabilityOptions(t);n=l.options,i=l.status}if(!n.length&&((d=e==null?void 0:e.options)!=null&&d.length)&&(n=e.options.map(l=>({id:null,label:l,available:!0,reason:null})),i=(e==null?void 0:e.availability_status)||i),!n.length){const l=(e==null?void 0:e.timezone_fallback_reason)||this.timezoneFallbackReason||null;this._appendSystem(`${(e==null?void 0:e.icon)||"⏰"} ${this._availabilityRecoveryMessage(i,l)}`),this.selectedSlotId=null,this.selectedDate=null,this._appendDatePicker({icon:"📅",label:"Choose another date",required:e==null?void 0:e.required});return}const o=document.createElement("div");o.className="message agent field-input-card directive-card";const a=document.createElement("div");if(a.className="card-header",a.textContent=`${e.icon||"⏰"} ${e.label||"Select a Time Slot"}`,o.appendChild(a),o.appendChild(this._buildTimezoneContextBanner(t)),e.description){const l=document.createElement("p");l.className="field-description",l.textContent=e.description,o.appendChild(l)}if(e!=null&&e.validation_error){const l=document.createElement("p");l.className="field-inline-error",l.textContent=e.validation_error,o.appendChild(l)}const s=document.createElement("div");s.className="select-options-grid",n.forEach(l=>{const r=document.createElement("button");r.className="select-option-btn time-slot-btn";const p=document.createElement("span");if(p.className="slot-local-time",p.textContent=l.label,r.appendChild(p),l.orgLabel){const c=document.createElement("span");c.className="slot-org-time",c.textContent=`Org time: ${l.orgLabel}`,r.appendChild(c)}l.available||(r.disabled=!0,r.classList.add("unavailable"),l.reason&&(r.title=`Unavailable (${l.reason})`)),r.onclick=()=>{l.available&&(s.querySelectorAll(".time-slot-btn").forEach(c=>{c.disabled=!0,c.classList.remove("selected")}),r.classList.add("selected"),this.selectedSlotId=l.id||null,this._handleSendManual(l.label))},s.appendChild(r)}),o.appendChild(s),this._msgList.appendChild(o),this._scrollToBottom()}_extractLatestIsoDate(){if(this.selectedDate)return this.selectedDate;const e=this._msgList.querySelectorAll(".message.user");for(let t=e.length-1;t>=0;t--){const i=(e[t].textContent||"").trim().match(/\d{4}-\d{2}-\d{2}/);if(i)return i[0]}return null}async _fetchAvailabilityOptions(e){try{const t=await fetch(this._buildAvailabilityRequestUrl(e));if(!t.ok)return{options:[],status:null};const n=await t.json();return this.orgTimezone=n.org_timezone||this.orgTimezone,n.user_timezone&&(this.userTimezone=n.user_timezone),n.timezone_source&&(this.timezoneSource=n.timezone_source),this.timezoneFallbackUsed=!!n.timezone_fallback_used,this.timezoneFallbackReason=n.timezone_fallback_reason||null,this._persistTimezonePreference(),this._showTimezoneFallbackNoticeIfNeeded(),{options:this._normalizeAvailabilitySlots(n.slots),status:n.availability_status||null}}catch{return{options:[],status:null}}}_appendOTPInterface(e={}){const t=document.createElement("div");t.className="message agent otp-card",t.setAttribute("role","form"),t.setAttribute("aria-label","Email verification");const n=document.createElement("div");n.className="card-header",n.textContent="Verification Required";const i=document.createElement("p");i.textContent="Please enter the email verification code to continue booking.";const o={cooldownSeconds:Number((e==null?void 0:e.otp_cooldown_seconds)||30),resendCap:Number((e==null?void 0:e.resend_cap)||3),resendsLeft:Number(typeof(e==null?void 0:e.resends_left)=="number"?e.resends_left:(e==null?void 0:e.resend_cap)||3)},a=document.createElement("div");a.className="otp-input-group";const s=document.createElement("input");s.type="text",s.maxLength=8,s.placeholder="Enter code",s.id="otp-field",s.setAttribute("aria-label","Verification code"),s.setAttribute("autocomplete","one-time-code");const d=document.createElement("button");d.id="verify-otp-btn",d.textContent="Verify",d.setAttribute("aria-label","Verify code"),a.appendChild(s),a.appendChild(d);const l=document.createElement("button");l.className="otp-resend-link",l.textContent="Didn't receive it? Resend code",l.setAttribute("aria-label","Resend verification code");const r=document.createElement("div");r.className="otp-status",r.setAttribute("aria-live","polite"),r.textContent=`Resend available in ${o.cooldownSeconds}s • ${o.resendsLeft} resends left`;const p=document.createElement("button");p.className="otp-resend-link",p.textContent="Use a different email address",p.setAttribute("aria-label","Use different email address"),t.appendChild(n),t.appendChild(i),t.appendChild(a),t.appendChild(l),t.appendChild(p),t.appendChild(r),this._msgList.appendChild(t),this._scrollToBottom(),this._input.disabled=!0,this._sendBtn.disabled=!0,this._input.placeholder="Enter the verification code above...",this._input.style.opacity="0.5",setTimeout(()=>s.focus(),100);let c=Math.max(0,o.resendCap-o.resendsLeft),h=Math.max(0,o.cooldownSeconds);const u=()=>{const g=Math.max(0,o.resendCap-c),b=h>0?`Resend available in ${h}s`:"You can resend now";r.textContent=`${b} • ${g} resends left`,l.disabled=h>0||c>=o.resendCap};u();const _=window.setInterval(()=>{if(h<=0){window.clearInterval(_);return}h-=1,u()},1e3);d.onclick=async()=>{const g=(s.value||"").replace(/\s+/g,"");if(console.log("[DotStark] Verify clicked. code length:",g.length,"session:",this.sessionId),g.length<4){r.textContent="Please enter the code sent to your email.",r.style.color="#ef4444",s.focus();return}if(!this.sessionId){r.textContent="Session expired. Please refresh and start again.",r.style.color="#ef4444";return}d.disabled=!0,s.disabled=!0,d.textContent="...",r.textContent="";try{const b=await fetch(`${m.apiBase}/auth/security/otp/verify`,{method:"POST",headers:{"Content-Type":"application/json","X-Widget-Token":m.widgetToken||"","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({session_id:this.sessionId,code:g})});if(b.ok){t.textContent="";const x=document.createElement("div");x.className="success-msg",x.textContent="✅ Email Verified Successfully!",t.appendChild(x),this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder="Type a message...",this._input.style.opacity="1",setTimeout(()=>this._handleSendManual("I've verified the code. Please proceed."),1e3)}else{let x="";try{const v=await b.json();x=v&&v.detail?String(v.detail):""}catch{}r.textContent=b.status===429?x||"Too many attempts. Please request a new code.":x||"That code is incorrect or has expired. Please try again or resend a new code.",r.style.color="#ef4444",d.disabled=!1,s.disabled=!1,d.textContent="Verify",s.value="",s.focus()}}catch{r.textContent="Couldn't reach the server to verify. Please check your connection and try again.",r.style.color="#ef4444",d.disabled=!1,s.disabled=!1,d.textContent="Verify"}},s.onkeydown=g=>{g.key==="Enter"&&d.click()},l.onclick=async()=>{if(c>=o.resendCap){r.textContent="Maximum resend attempts reached. Please try again later.",r.style.color="#ef4444";return}l.disabled=!0,l.textContent="Sending...",r.textContent="";try{await this._handleSendManual("resend"),c++,h=o.cooldownSeconds,r.textContent=`Code resent! (${Math.max(0,o.resendCap-c)} resends left)`,r.style.color="#22c55e"}catch{r.textContent="Failed to resend. Please try again.",r.style.color="#ef4444"}u(),l.textContent="Didn't receive it? Resend code"},p.onclick=()=>{this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder="Enter the correct email address...",this._input.style.opacity="1",this._input.focus(),r.textContent="No problem — provide the correct email and we’ll resend there.",r.style.color="#64748b"},this._scrollToBottom(),s.focus()}_appendSummaryCard(e){const t=document.createElement("div");t.className="message agent summary-card";const n=document.createElement("div");n.className="card-header",n.textContent="📋 Booking Summary";const i=document.createElement("div");i.className="summary-content",this._renderMarkdown(i,e);const o=document.createElement("div");o.className="card-actions";const a=document.createElement("button");a.className="confirm-btn",a.textContent="Confirm Booking",a.setAttribute("aria-label","Confirm booking");const s=document.createElement("button");s.className="edit-btn",s.textContent="✎ Change Details",s.setAttribute("aria-label","Edit booking details"),o.appendChild(a),o.appendChild(s),t.appendChild(n),t.appendChild(i),t.appendChild(o),this._msgList.appendChild(t),a.onclick=()=>{a.disabled=!0,s.disabled=!0,this._handleSendManual("Yes, please confirm the booking.")},s.onclick=()=>{a.disabled=!0,s.disabled=!0,this._handleSendManual("I need to change some details.")},this._scrollToBottom()}_appendConfirmedCard(e,t={}){const n=document.createElement("div");n.className="message agent confirmed-card",n.setAttribute("role","alert");const i=document.createElement("div");i.className="conf-icon",i.textContent="🎉";const o=document.createElement("h3");o.textContent="Booking Confirmed!",o.style.cssText="font-size:16px;font-weight:700;color:var(--success);margin:8px 0;";const a=document.createElement("p");a.style.cssText="font-size:13px;color:var(--text-sec);line-height:1.5;",this._renderMarkdown(a,e);const s=document.createElement("div");s.className="conf-footer",s.textContent=(t==null?void 0:t.email_retrying)===!0?"📧 Booking confirmed. Confirmation email delivery is retrying in the background.":"📧 A confirmation email is on its way.";const d=document.createElement("button");d.textContent="🔄 Start New Booking",d.style.cssText=`
                margin-top:12px;padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;
            `,d.addEventListener("mouseover",()=>d.style.opacity="0.85"),d.addEventListener("mouseout",()=>d.style.opacity="1"),d.addEventListener("click",()=>this._startNewSession()),n.appendChild(i),n.appendChild(o),n.appendChild(a),n.appendChild(s),n.appendChild(d),this._msgList.appendChild(n),this._scrollToBottom()}async _handleSendManual(e){this._input.value=e,await this._handleSend()}_disableInput(e){this._input.disabled=!0,this._sendBtn.disabled=!0,this._input.placeholder=e||"Session ended.",this._input.style.opacity="0.5",this._sendBtn.style.opacity="0.3"}_enableInput(e){this._input.disabled=!1,this._sendBtn.disabled=!1,this._input.placeholder=e||"Type a message...",this._input.style.opacity="1",this._sendBtn.style.opacity="1"}_startNewSession(){this.sessionId=null,sessionStorage.removeItem(this._sessionStorageKey()),sessionStorage.removeItem("dotstark_session_id"),sessionStorage.removeItem("dotstark_user_timezone"),sessionStorage.removeItem("dotstark_timezone_source"),this.userTimezone=this._detectBrowserTimezone(),this.timezoneSource="detected",this.selectedSlotId=null,this.selectedDate=null,this.orgTimezone=null,this.timezoneFallbackUsed=!1,this.timezoneFallbackReason=null,this._timezoneNoticeShown=!1,this._directiveDrafts={},this._setInlineValidation(""),this._clearSkipAction(),this._msgList.querySelectorAll(".message").forEach(e=>e.remove()),this._enableInput("Type a message..."),this._appendBubble("agent","Hello! 👋 I'm your booking assistant. How can I help you schedule an appointment today?")}_appendNewBookingPrompt(){const e=document.createElement("div");e.className="message agent";const t=document.createElement("button");t.textContent="🔄 Start New Booking",t.style.cssText=`
                padding:10px 20px;border:none;border-radius:8px;
                background:var(--primary);color:#fff;font-size:13px;font-weight:600;
                cursor:pointer;width:100%;transition:opacity 0.2s;margin-top:4px;
            `,t.addEventListener("mouseover",()=>t.style.opacity="0.85"),t.addEventListener("mouseout",()=>t.style.opacity="1"),t.addEventListener("click",()=>this._startNewSession()),e.appendChild(t),this._msgList.appendChild(e),this._scrollToBottom()}_resolveAssistantText(e){if(e&&typeof e=="object"){const n=e.response_text||e.message;return typeof n=="string"?n.trim():""}if(typeof e!="string")return"";const t=e.trim();if(!t)return"";if(t.startsWith("{")&&t.endsWith("}"))try{const n=JSON.parse(t);if(n&&typeof n=="object"){const i=n.response_text||n.message;return typeof i=="string"?i.trim():""}}catch{}return t}_scrollToBottom(){requestAnimationFrame(()=>{this._msgList.scrollTo({top:this._msgList.scrollHeight,behavior:"smooth"})})}_css(){return`
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

                :host {
                    --accent: #3b82f6;
                    --accent-hover: #2563eb;
                    --accent-glow: rgba(59, 130, 246, 0.3);
                    --accent-soft: rgba(59, 130, 246, 0.08);
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
                    --msg-user-bg: #3b82f6;
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
                    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.2);
                }

                .message.user:hover {
                    box-shadow: 0 4px 16px rgba(59, 130, 246, 0.3);
                }

                .message.system {
                    align-self: center;
                    background: var(--accent-soft);
                    color: var(--text-sec);
                    font-size: 12px;
                    text-align: center;
                    padding: 8px 16px;
                    max-width: 90%;
                    border: 1px solid rgba(59, 130, 246, 0.12);
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
                    50% { border-color: rgba(59, 130, 246, 0.3); }
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
                    display: flex;
                    flex-wrap: wrap;
                    gap: 8px;
                    margin-top: 8px;
                }

                .timezone-context-banner {
                    margin: 8px 0 10px;
                    padding: 8px;
                    border: 1px solid rgba(59, 130, 246, 0.45);
                    border-radius: 10px;
                    background: rgba(59, 130, 246, 0.08);
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
            `}}customElements.define("dotstark-booking-widget",k);const C=document.createElement("dotstark-booking-widget");document.body.appendChild(C)})()})();
