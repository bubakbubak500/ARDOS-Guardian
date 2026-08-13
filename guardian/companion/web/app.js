"use strict";
const $ = id => document.getElementById(id);
let state = null, revision = -1, watch = false, wakeLock = null, audio = null;
let lastUnread = 0, lastReply = Date.now(), retry = 600, pairingToken = "";
let linkAlarmed = false, checkinAlarmed = false;

function toast(message, bad=false) {
  const el=$("toast"); el.textContent=message; el.style.background=bad?"var(--red)":"var(--cyan)"; el.classList.add("show");
  clearTimeout(el.timer); el.timer=setTimeout(()=>el.classList.remove("show"),3200);
}
async function api(path, body) {
  const options=body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)};
  const response=await fetch(path, options), value=await response.json().catch(()=>({error:"Invalid response"}));
  if (!response.ok) { const error=new Error(value.error||`HTTP ${response.status}`); error.status=response.status; throw error; }
  return value;
}
function setConnected(ok) { const el=$("connection"); el.textContent=ok?"● Linked":"◆ Link lost"; el.className=`pill ${ok?"online":"offline"}`; }
function tab(name) { document.querySelectorAll("nav button").forEach(b=>b.classList.toggle("active",b.dataset.tab===name)); document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active",t.id===`tab-${name}`)); }
document.querySelectorAll("[data-tab]").forEach(b=>b.addEventListener("click",()=>tab(b.dataset.tab)));

async function boot() {
  pairingToken=new URLSearchParams(location.hash.slice(1)).get("pair")||"";
  try { state=await api("/api/state?after=-1"); showApp(); render(state); poll(); }
  catch (error) { if(error.status===401) { $("pairing").classList.remove("hidden"); if(!pairingToken) $("pair-error").textContent="Open a fresh QR code in Guardian."; } else retryPoll(); }
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(()=>{});
  if(!window.isSecureContext) $("watch-detail").textContent="Keep this page visible and disable auto-lock; local HTTP cannot hold an iPhone wake lock.";
}
$("pair").addEventListener("click", async()=>{ try { await api("/api/pair",{token:pairingToken,name:$("phone-name").value}); history.replaceState(null,"",location.pathname); $("pairing").classList.add("hidden"); state=await api("/api/state?after=-1"); showApp(); render(state); poll(); } catch(e){ $("pair-error").textContent=e.message; } });
function showApp(){ $("app").classList.remove("hidden"); $("pairing").classList.add("hidden"); setConnected(true); }

async function poll(){ try { const next=await api(`/api/state?after=${revision}`); lastReply=Date.now(); linkAlarmed=false; retry=600; setConnected(true); render(next); setTimeout(poll,60); } catch(e){ setConnected(false); if(e.status===401){ location.reload(); return; } retryPoll(); } }
function retryPoll(){ setConnected(false); setTimeout(poll,retry); retry=Math.min(8000,retry*1.7); }
async function measureLink(){ const started=performance.now(); try{await api("/api/info");$("latency").textContent=`${Math.round(performance.now()-started)} ms`;}catch(_e){} }
setInterval(measureLink,5000);
setInterval(()=>{if(watch&&Date.now()-lastReply>30000&&!linkAlarmed){linkAlarmed=true;alarm("Guardian link lost — move back toward the station");}},2000);

function render(next){
  const initial=revision<0; state=next; revision=next.revision; $("station").textContent=next.station; $("unread").textContent=next.mailbox.unread; $("inbox-count").textContent=next.mailbox.inbox; $("outbox-count").textContent=next.mailbox.outbox; $("heard-count").textContent=next.network.heard;
  $("control-state").textContent=next.network.control?"Active":"Off"; $("radio-state").textContent=next.radio.connected?(next.radio.ptt?"Transmitting":"Connected"):"Disconnected"; $("session-count").textContent=next.network.sessions;
  $("transmit-row").classList.toggle("hidden",!next.remote_emergency_armed||$("priority").value!=="3");
  renderMessages(next.messages); renderEvents(next.events); renderNotes(next.notes); renderCheckin(next);
  if(!initial && next.mailbox.unread>lastUnread) alarm(`${next.mailbox.unread-lastUnread} new message${next.mailbox.unread-lastUnread===1?"":"s"}`);
  lastUnread=next.mailbox.unread;
  if("setAppBadge" in navigator){ if(next.mailbox.unread) navigator.setAppBadge(next.mailbox.unread).catch(()=>{}); else navigator.clearAppBadge().catch(()=>{}); }
}
function renderMessages(messages){ const inbox=messages.filter(m=>m.folder==="inbox"); const box=$("messages"); box.innerHTML=""; box.classList.toggle("empty",!inbox.length); if(!inbox.length){box.textContent="No messages";return;} inbox.forEach(m=>{ const b=document.createElement("button"); b.className=`message ${m.read?"":"unread"} ${m.priority>=3?"emergency":""}`; const preview=state.hide_previews?"Preview hidden":(m.body||"").slice(0,92); b.innerHTML=`<strong>${escapeHtml(m.subject||"No subject")}</strong><time>${when(m.created)}</time><small>${escapeHtml(m.source)} · ${escapeHtml(preview)}</small><small class="priority">${["Routine","Priority","Urgent","Emergency"][m.priority]||""}</small>`; b.addEventListener("click",()=>openMessage(m)); box.appendChild(b); }); }
async function openMessage(m){ $("reader-route").textContent=`${m.source} → ${m.final_dest} · ${when(m.created)}`; $("reader-subject").textContent=m.subject||"No subject"; $("reader-body").textContent=m.body||""; $("reader").showModal(); if(!m.read) try{await api(`/api/messages/${m.msg_id}/read`,{});}catch(e){toast(e.message,true);} }
$("reader-close").addEventListener("click",()=>$("reader").close());
function renderEvents(events){ const box=$("events"); box.innerHTML=""; [...events].reverse().slice(0,80).forEach(e=>{ const row=document.createElement("div"); row.className=`event ${e.level}`; row.innerHTML=`<time>${new Date(e.time).toLocaleTimeString()} · ${escapeHtml(e.source)}</time><p>${escapeHtml(e.message)}</p>`; box.appendChild(row); }); }
function renderNotes(notes){ const box=$("notes"); box.innerHTML=""; notes.forEach(n=>{const row=document.createElement("div");row.className="note";row.textContent=n.text;const del=document.createElement("button");del.textContent="×";del.addEventListener("click",async()=>{try{await api("/api/notes/delete",{id:n.id});}catch(e){toast(e.message,true);}});row.appendChild(del);box.appendChild(row);});}
function renderCheckin(s){ const c=s.checkin, clear=$("clear-checkin"); clear.classList.toggle("hidden",!c); if(!c){$("checkin-title").textContent="Set a check-in";checkinAlarmed=false;return;} const seconds=Math.round(c.deadline-Date.now()/1000); $("checkin-title").textContent=seconds<=0?"CHECK-IN OVERDUE":`Return in ${Math.floor(seconds/60)}:${String(Math.max(0,seconds%60)).padStart(2,"0")}`; if(s.checkin_overdue&&!checkinAlarmed){checkinAlarmed=true;alarm("Field check-in overdue");} }
setInterval(()=>state&&renderCheckin(state),1000);

$("priority").addEventListener("change",()=>$("transmit-row").classList.toggle("hidden",!state.remote_emergency_armed||$("priority").value!=="3"));
$("compose-form").addEventListener("submit",async e=>{e.preventDefault();const transmit=$("transmit").checked;if(transmit&&!confirm("Transmit this EMERGENCY message over radio now?"))return;$("queue").disabled=true;try{const result=await api("/api/messages",{destination:$("destination").value,subject:$("subject").value,body:$("body").value,priority:Number($("priority").value),transmit});$("compose-result").textContent=result.transmit_started?`Emergency #${result.id} transmission started.`:`Message #${result.id} queued in Guardian.`;$("body").value="";$("transmit").checked=false;toast("Message accepted by Guardian");}catch(err){$("compose-result").textContent=err.message;toast(err.message,true);}finally{$("queue").disabled=false;}});
$("save-note").addEventListener("click",async()=>{try{await api("/api/notes",{text:$("note-text").value});$("note-text").value="";toast("Field note saved");}catch(e){toast(e.message,true);}});
$("start-checkin").addEventListener("click",async()=>{try{await api("/api/checkin",{minutes:Number($("checkin-minutes").value)});toast("Return timer started");}catch(e){toast(e.message,true);}});
$("clear-checkin").addEventListener("click",async()=>{try{await api("/api/checkin/clear",{});toast("Welcome back");}catch(e){toast(e.message,true);}});
$("ping").addEventListener("click",async()=>{try{await api("/api/ping",{});toast("Station pinged");}catch(e){toast(e.message,true);}});

$("watch").addEventListener("click",async()=>{ watch=!watch; document.body.classList.toggle("watch",watch); $("watch").textContent=watch?"Stop watch":"Start watch"; $("watch-title").textContent=watch?"Watching Guardian":"Keep me in range"; if(watch){ audio=new (window.AudioContext||window.webkitAudioContext)(); await audio.resume(); if(window.isSecureContext&&"Notification" in window&&Notification.permission==="default")await Notification.requestPermission().catch(()=>{}); if(navigator.wakeLock) try{wakeLock=await navigator.wakeLock.request("screen");}catch(_e){} beep(520,.10); toast("Field watch armed"); } else { if(wakeLock) await wakeLock.release().catch(()=>{}); wakeLock=null; } });
document.addEventListener("visibilitychange",async()=>{if(watch&&document.visibilityState==="visible"&&navigator.wakeLock)try{wakeLock=await navigator.wakeLock.request("screen");}catch(_e){}});
function beep(freq=760,duration=.18){ if(!audio)return;const o=audio.createOscillator(),g=audio.createGain();o.frequency.value=freq;o.connect(g);g.connect(audio.destination);g.gain.setValueAtTime(.0001,audio.currentTime);g.gain.exponentialRampToValueAtTime(.24,audio.currentTime+.02);g.gain.exponentialRampToValueAtTime(.0001,audio.currentTime+duration);o.start();o.stop(audio.currentTime+duration+.02); }
function alarm(message){ if(!watch)return; [0,260,520].forEach((ms,i)=>setTimeout(()=>beep(i===1?980:720,.2),ms)); if(navigator.vibrate)navigator.vibrate([250,100,250,100,450]); if(window.isSecureContext&&"Notification" in window&&Notification.permission==="granted"&&navigator.serviceWorker)navigator.serviceWorker.ready.then(reg=>reg.showNotification("Guardian",{body:message,tag:"guardian-field",renotify:true,icon:"/icon.svg"})).catch(()=>{}); toast(message); }
function when(epoch){return new Date(epoch*1000).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});}
function escapeHtml(value){const d=document.createElement("div");d.textContent=String(value??"");return d.innerHTML;}
boot();
