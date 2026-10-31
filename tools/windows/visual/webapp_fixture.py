"""Owned WebView2 responses at the service origin, with no account or network.

The production origin checks, injected script and browser controls stay active.
Only the native HTTP response provider is replaced with generated HTML/data.
"""

from __future__ import annotations

import ctypes
from contextlib import ExitStack
from urllib.parse import quote

from docking.platform.backends.windows.com import (
    GUID,
    HRESULT,
    method,
    object_result,
    query,
    release,
)
from docking.platform.backends.windows.ffi import bind
from docking.platform.backends.windows.winrt import check

_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>(3) WhatsApp</title><style>
body{margin:0;font:17px system-ui;background:#eef2f4;color:#243c3b}
header{background:#075e54;color:white;padding:24px;font-size:24px}
main{display:grid;grid-template-columns:270px 1fr;min-height:480px}
aside{background:white;padding:25px;border-right:1px solid #cdd7d6}
section{padding:35px}input{padding:12px;font:inherit;width:80%}
button,a{display:inline-block;margin:12px 8px 0 0;padding:12px;background:white;
border:1px solid #8cafaa;border-radius:6px;color:#075e54;text-decoration:none}
small{display:block;color:#53716b;margin-top:24px}
</style></head><body><header>WhatsApp · generated Windows fixture</header>
<main><aside>Native WebView2<br><br>Unread badge and session controls
<small>No account, messages or contacts are used.</small></aside>
<section>LANDMARK<h2>Browser and dock integration</h2>
<input id="input" aria-label="Generated input" placeholder="Type in the native browser">
<p><a id="external" href="https://example.test/docking-owned-fixture">External link</a>
<a id="download" href="https://web.whatsapp.com/fixture-download">
Download generated file</a></p>
<small>This page is supplied by a labeled native response fixture.
The normal origin checks and page bridge remain enabled.</small>
</section></main><script>
window.fixture={dbValue:null,storage:null,closedTitles:[],shownTitles:[]};
window.fixture.store=function(value){
 localStorage.setItem('docking-fixture',value);
 document.cookie='docking_fixture='+value+
  '; Max-Age=3600; Secure; SameSite=Strict; path=/';
 const request=indexedDB.open('docking-fixture',1);
 request.onupgradeneeded=()=>request.result.createObjectStore('store');
 request.onsuccess=()=>{
  const db=request.result, tx=db.transaction('store','readwrite');
  tx.objectStore('store').put(value,'key');
  tx.oncomplete=()=>{window.fixture.dbValue=value;db.close()};
 };
 return true;
};
window.fixture.read=function(){
 window.fixture.storage=null;
 const values={local:localStorage.getItem('docking-fixture'),
  cookie:document.cookie,db:null};
 const request=indexedDB.open('docking-fixture',1);
 request.onupgradeneeded=()=>request.result.createObjectStore('store');
 request.onsuccess=()=>{
  const db=request.result;
  const get=db.transaction('store').objectStore('store').get('key');
  get.onsuccess=()=>{values.db=get.result??null;window.fixture.storage=values;db.close()};
 };
 return true;
};
window.fixture.notify=async function(title){
 const allowed=await Notification.requestPermission();
 if(allowed!=='granted')throw new Error('Notification permission denied');
 const n=new Notification(title,{body:'Owned browser notification fixture',
  tag:'docking-owned'});
 n.onshow=()=>window.fixture.shownTitles.push(title);
 n.onclick=()=>{document.title='(11) WhatsApp';window.fixture.clicked=true};
 n.onclose=()=>{window.fixture.closed=true;window.fixture.closedTitles.push(title)};
 window.fixture.notification=n;window.fixture.sent=true;
};
window.fixture.workerEvents=[];
window.fixture.startWorker=async function(){
 const registration=await navigator.serviceWorker.register('/fixture-worker.js',
  {scope:'/'});
 await navigator.serviceWorker.ready;
 window.fixture.registration=registration;
 window.fixture.workerReady=true;
 return true;
};
window.fixture.workerRequest=function(action,values={}){
 return new Promise((resolve,reject)=>{
  const id=crypto.randomUUID();
  const timeout=setTimeout(()=>{navigator.serviceWorker.removeEventListener('message',
   receive);reject(new Error('Worker fixture timed out'))},15000);
  function receive(event){
   const data=event.data;
   if(data?.fixture!=='docking-worker'||data.id!==id)return;
   clearTimeout(timeout);navigator.serviceWorker.removeEventListener('message',receive);
   if(data.error)reject(new Error(data.error));else resolve(data.result);
  }
  navigator.serviceWorker.addEventListener('message',receive);
  window.fixture.registration.active.postMessage({id,action,...values});
 });
};
navigator.serviceWorker.addEventListener('message',event=>{
 const data=event.data;
 if(data?.fixture==='docking-worker'&&data.event)
  window.fixture.workerEvents.push(data);
});
addEventListener('DOMContentLoaded',()=>navigator.setAppBadge(7));
</script></body></html>"""

_WORKER = r"""
const events=[];
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
async function relay(event,notification){
 events.push({event,title:notification.title,tag:notification.tag});
 const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});
 for(const client of clients)client.postMessage({fixture:'docking-worker',event,
  title:notification.title,tag:notification.tag});
}
self.addEventListener('notificationclick',event=>{
 event.waitUntil((async()=>{
  await relay('clicked',event.notification);
  event.notification.close();
 })());
});
self.addEventListener('notificationclose',event=>
 event.waitUntil(relay('closed',event.notification)));
self.addEventListener('message',event=>{
 const {id,action,title,tag='docking-worker-owned'}=event.data;
 event.waitUntil((async()=>{
  if(action==='show')await self.registration.showNotification(title,
   {body:'Owned generated service-worker notification',tag,silent:true});
  const notifications=await self.registration.getNotifications({tag});
  if(action==='close')for(const notification of notifications)notification.close();
  event.source.postMessage({fixture:'docking-worker',id,
   result:action==='events'?events:notifications.map(n=>({title:n.title,tag:n.tag}))});
 })().catch(error=>event.source.postMessage({fixture:'docking-worker',id,
  error:error.name+': '+error.message})));
});"""


class WebAppPageFixture:
    def __init__(self, profile):
        self.profile = profile
        self.mode = "ready"
        self.requests = 0
        self.worker_requests = 0
        self.download_filename = f"docking-generated-{profile.session_name}.txt"
        self.download_content = f"Docking generated {profile.name} download\n".encode()

    def install(self, engine) -> None:
        engine.subscribe(
            engine.core,
            55,
            "ab00b74c-15f1-4646-80e8-e76341d25d71",
            lambda args: self._response(engine, args),
        )
        with ExitStack() as scope:
            # The original document-only filter misses service-worker requests.
            # Core22's source-kind filter serves the actual worker script too.
            view = query(
                scope, engine.core, GUID.parse("db75dfc7-a857-4632-a398-6969dde26c0a")
            )
            check(
                method(
                    view,
                    123,
                    HRESULT,
                    ctypes.c_wchar_p,
                    ctypes.c_int32,
                    ctypes.c_uint32,
                )(view, self.profile.origin + "*", 0, 0xFFFFFFFF)
            )

    def _response(self, engine, args) -> None:
        self.requests += 1
        with ExitStack() as scope:
            request = object_result(scope, args, 3)
            uri = engine.text(request, 3)
            if uri.endswith("/fixture-worker.js"):
                self.worker_requests += 1
                content = _WORKER.encode()
                headers = (
                    "Content-Type: application/javascript; charset=utf-8\r\n"
                    "Service-Worker-Allowed: /\r\nCache-Control: no-store\r\n"
                )
            elif uri.endswith("/fixture-download"):
                content = self.download_content
                headers = (
                    "Content-Type: application/octet-stream\r\n"
                    "Content-Disposition: attachment; filename*=UTF-8''"
                    + quote(self.download_filename, safe="")
                    + "\r\n"
                )
            else:
                landmark = (
                    '<div id="pane-side">Generated connected state</div>'
                    if self.mode == "ready"
                    else '<canvas aria-label="Generated login placeholder" '
                    'width="150" height="150"></canvas>'
                )
                if self.profile.session_name == "telegram":
                    landmark = (
                        '<div id="column-left"><div class="chatlist">'
                        "Generated connected state</div></div>"
                        if self.mode == "ready"
                        else '<div id="auth-pages">Generated login placeholder</div>'
                    )
                content = (
                    _HTML.replace("LANDMARK", landmark)
                    .replace("WhatsApp", self.profile.name)
                    .replace("https://web.whatsapp.com/", self.profile.origin)
                    .encode()
                )
                headers = "Content-Type: text/html; charset=utf-8\r\n"
            buffer = (ctypes.c_ubyte * len(content)).from_buffer_copy(content)
            stream = ctypes.c_void_p(
                bind(
                    ctypes.WinDLL("shlwapi"),
                    "SHCreateMemStream",
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                )(buffer, len(content))
            )
            if not stream.value:
                raise OSError("Could not create owned response stream")
            scope.callback(release, stream)
            response = object_result(
                scope,
                engine.environment,
                4,
                ctypes.c_void_p,
                stream,
                ctypes.c_int32,
                200,
                ctypes.c_wchar_p,
                "OK",
                ctypes.c_wchar_p,
                headers,
            )
            check(method(args, 5, HRESULT, ctypes.c_void_p)(args, response))
