(function(){
"use strict";

const STYLE = \`
.media-editor{margin-top:10px;border:1px solid var(--line);border-radius:14px;background:var(--soft);overflow:hidden}
.media-editor-head{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--line);font-weight:900}
.media-editor-head small{margin-left:auto;color:var(--muted);font-weight:600}
.media-editor-body{padding:10px 12px}
.media-editor-preview{position:relative;display:grid;place-items:center;min-height:190px;max-height:420px;background:#111;border-radius:12px;overflow:hidden}
.media-editor-preview canvas{display:block;max-width:100%;max-height:400px;object-fit:contain}
.media-editor-preview video{display:none}
.media-editor-tools{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}
.media-editor-tool{display:flex;align-items:center;gap:7px;padding:8px 9px;background:var(--card);border:1px solid var(--line);border-radius:9px;font-size:12px}
.media-editor-tool label{font-weight:800;white-space:nowrap}
.media-editor-tool input[type=range]{width:100%}
.media-editor-tool select,.media-editor-tool input[type=text]{width:100%;min-width:0;border:1px solid var(--line);border-radius:7px;background:var(--card);color:var(--text);padding:6px}
.media-editor-buttons{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}
.media-editor-buttons button{border:0;border-radius:8px;padding:7px 10px;background:var(--card);color:var(--text);font-weight:800;cursor:pointer}
.media-editor-buttons button.active{background:var(--blue);color:#fff}
.media-editor-help{font-size:11px;color:var(--muted);margin-top:8px;line-height:1.4}
.media-editor-progress{height:5px;background:var(--line);border-radius:999px;overflow:hidden;margin-top:8px;display:none}
.media-editor-progress span{display:block;width:0;height:100%;background:var(--blue);transition:width .15s}
@media(max-width:600px){.media-editor-tools{grid-template-columns:1fr}.media-editor-preview{min-height:150px;max-height:330px}.media-editor-preview canvas{max-height:310px}}
\`;
if(!document.getElementById("media-editor-style")){const st=document.createElement("style");st.id="media-editor-style";st.textContent=STYLE;document.head.appendChild(st)}

const state={mode:"post",file:null,url:null,type:null,kind:null,rotation:0,ratio:"original",brightness:100,contrast:100,saturation:100,text:"",start:0,end:null,duration:0,video:null,canvas:null,ctx:null,dirty:false};

function qs(s){return document.querySelector(s)}
function clamp(n,a,b){return Math.max(a,Math.min(b,n))}
function aspectValue(r, w, h){
 if(r==="original") return w/h;
 const p=r.split(":"); return Number(p[0])/Number(p[1]);
}
function cropRect(w,h,ratio){
 const target=aspectValue(ratio,w,h);
 let cw=w,ch=h;
 if(target> w/h) ch=w/target; else cw=h*target;
 return {x:(w-cw)/2,y:(h-ch)/2,w:cw,h:ch};
}
function outputSize(w,h,ratio,rotation){
 const r=rotation%180!==0?{w:h,h:w}:{w:w,h:h};
 const target=aspectValue(ratio,r.w,r.h);
 let ow=r.w,oh=r.h;
 if(ratio!=="original"){if(target>r.w/r.h) oh=r.w/target; else ow=r.h*target}
 const max=720,scale=Math.min(1,max/Math.max(ow,oh));
 return {w:Math.max(2,Math.round(ow*scale)),h:Math.max(2,Math.round(oh*scale))};
}

function ensureUI(){
 if(qs("#mediaEditor")) return;
 const form=qs("#composer-form"); if(!form)return;
 const wrap=document.createElement("section");wrap.id="mediaEditor";wrap.className="media-editor";wrap.hidden=true;
 wrap.innerHTML=\`
 <div class="media-editor-head">🎨 Media Editor <small id="mediaEditorType">Photo / Video</small></div>
 <div class="media-editor-body">
  <div class="media-editor-preview"><canvas id="mediaEditorCanvas"></canvas></div>
  <div class="media-editor-tools">
   <div class="media-editor-tool"><label>Crop</label><select id="mediaEditorRatio"><option value="original">Original</option><option value="1:1">1:1 Square</option><option value="4:5">4:5 Portrait</option><option value="16:9">16:9 Landscape</option><option value="9:16">9:16 Story/Reel</option></select></div>
   <div class="media-editor-tool"><label>Text</label><input id="mediaEditorText" type="text" maxlength="120" placeholder="Add text on media"></div>
   <div class="media-editor-tool"><label>☀</label><input id="mediaEditorBrightness" type="range" min="50" max="150" value="100"></div>
   <div class="media-editor-tool"><label>◐</label><input id="mediaEditorContrast" type="range" min="50" max="150" value="100"></div>
   <div class="media-editor-tool"><label>🎨</label><input id="mediaEditorSaturation" type="range" min="0" max="200" value="100"></div>
   <div class="media-editor-tool" id="mediaEditorTrim"><label>Start</label><input id="mediaEditorStart" type="range" min="0" max="0" step="0.1" value="0"></div>
   <div class="media-editor-tool" id="mediaEditorEnd"><label>End</label><input id="mediaEditorEndRange" type="range" min="0" max="0" step="0.1" value="0"></div>
  </div>
  <div class="media-editor-buttons">
   <button type="button" id="mediaEditorRotate">↻ Rotate</button>
   <button type="button" id="mediaEditorReset">Reset</button>
   <button type="button" id="mediaEditorClose">Hide editor</button>
  </div>
  <div class="media-editor-help" id="mediaEditorHelp">Edits are applied before the media is uploaded.</div>
  <div class="media-editor-progress" id="mediaEditorProgress"><span></span></div>
 </div>\`;
 form.querySelector(".composer-actions")?.after(wrap);
 const bind=(id,ev,fn)=>qs(id)?.addEventListener(ev,fn);
 bind("#mediaEditorRatio","change",e=>{state.ratio=e.target.value;state.dirty=true;draw()});
 bind("#mediaEditorText","input",e=>{state.text=e.target.value;state.dirty=true;draw()});
 bind("#mediaEditorBrightness","input",e=>{state.brightness=+e.target.value;state.dirty=true;draw()});
 bind("#mediaEditorContrast","input",e=>{state.contrast=+e.target.value;state.dirty=true;draw()});
 bind("#mediaEditorSaturation","input",e=>{state.saturation=+e.target.value;state.dirty=true;draw()});
 bind("#mediaEditorStart","input",e=>{state.start=+e.target.value; if(state.end!==null&&state.start>=state.end)state.end=Math.min(state.duration,state.start+.1); if(state.video)state.video.currentTime=state.start;});
 bind("#mediaEditorEndRange","input",e=>{state.end=+e.target.value; if(state.end<=state.start)state.end=Math.min(state.duration,state.start+.1);});
 bind("#mediaEditorRotate","click",()=>{state.rotation=(state.rotation+90)%360;state.dirty=true;draw()});
 bind("#mediaEditorReset","click",resetControls);
 bind("#mediaEditorClose","click",()=>{const w=qs("#mediaEditor");if(w)w.hidden=true});
}

function resetControls(){
 state.rotation=0;state.ratio="original";state.brightness=100;state.contrast=100;state.saturation=100;state.text="";state.start=0;state.end=state.duration||null;state.dirty=false;
 [["#mediaEditorRatio","original"],["#mediaEditorText",""],["#mediaEditorBrightness",100],["#mediaEditorContrast",100],["#mediaEditorSaturation",100],["#mediaEditorStart",0],["#mediaEditorEndRange",state.end||0]].forEach(([id,v])=>{const e=qs(id);if(e)e.value=v});
 draw();
}

function setMode(mode){state.mode=mode;ensureUI();const w=qs("#mediaEditor");if(w)w.hidden=true}
function clearFile(){state.file=null;state.kind=null;state.video=null;state.duration=0;state.end=null;if(state.url){URL.revokeObjectURL(state.url);state.url=null}const w=qs("#mediaEditor");if(w)w.hidden=true}
function setFile(file){
 ensureUI();clearFile();if(!file)return;
 state.file=file;state.url=URL.createObjectURL(file);state.kind=file.type.startsWith("video/")?"video":"image";state.dirty=false;
 const w=qs("#mediaEditor");w.hidden=false;qs("#mediaEditorType").textContent=state.kind==="video"?"Video editor":"Photo editor";
 qs("#mediaEditorTrim").style.display=state.kind==="video"?"flex":"none";qs("#mediaEditorEnd").style.display=state.kind==="video"?"flex":"none";
 if(state.kind==="image"){
   const img=new Image();img.onload=()=>{state.video=img;draw()};img.src=state.url;
   qs("#mediaEditorHelp").textContent="Crop, rotate, adjust light/color, and add text. The edited photo is uploaded.";
 }else{
   const v=document.createElement("video");v.muted=true;v.playsInline=true;v.preload="metadata";
   v.onloadedmetadata=()=>{state.video=v;state.duration=v.duration||0;state.end=state.duration;["#mediaEditorStart","#mediaEditorEndRange"].forEach(id=>{const e=qs(id);e.max=state.duration;e.value=id.includes("Start")?0:state.duration});draw()};
   v.src=state.url;state.video=v;
   qs("#mediaEditorHelp").textContent="Trim the start/end, crop, adjust light/color, rotate, and add text. The edited video is rendered before upload.";
 }
}
function draw(){
 if(!state.video)return;
 const c=qs("#mediaEditorCanvas");if(!c)return;
 const src=state.video,w=src.videoWidth||src.naturalWidth||src.width,h=src.videoHeight||src.naturalHeight||src.height;if(!w||!h)return;
 const rotated=state.rotation%180!==0;
 const rw=rotated?h:w,rh=rotated?w:h;
 const maxSource=960,scale=Math.min(1,maxSource/Math.max(rw,rh));
 const bw=Math.max(2,Math.round(rw*scale)),bh=Math.max(2,Math.round(rh*scale));
 const off=document.createElement("canvas");off.width=bw;off.height=bh;const oc=off.getContext("2d");
 oc.save();oc.translate(bw/2,bh/2);oc.rotate(state.rotation*Math.PI/180);
 oc.filter=\`brightness(\${state.brightness}%) contrast(\${state.contrast}%) saturate(\${state.saturation}%)\`;
 oc.drawImage(src,-(w*scale)/2,-(h*scale)/2,w*scale,h*scale);oc.restore();
 const crop=cropRect(bw,bh,state.ratio);const size=outputSize(bw,bh,state.ratio,0);c.width=size.w;c.height=size.h;
 const ctx=c.getContext("2d");state.canvas=c;state.ctx=ctx;ctx.clearRect(0,0,c.width,c.height);ctx.fillStyle="#111";ctx.fillRect(0,0,c.width,c.height);
 const fit=Math.max(c.width/crop.w,c.height/crop.h);
 ctx.drawImage(off,crop.x,crop.y,crop.w,crop.h,(c.width-crop.w*fit)/2,(c.height-crop.h*fit)/2,crop.w*fit,crop.h*fit);
 if(state.text){ctx.save();ctx.font=\`700 \${Math.max(18,Math.round(c.width/18))}px system-ui,sans-serif\`;ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillStyle="rgba(0,0,0,.5)";const tw=ctx.measureText(state.text).width+28;ctx.fillRect(c.width/2-tw/2,c.height*.78-24,tw,48);ctx.fillStyle="#fff";ctx.fillText(state.text,c.width/2,c.height*.78);ctx.restore()}
}
async function renderImage(){
 draw();return new Promise((resolve,reject)=>qs("#mediaEditorCanvas").toBlob(b=>b?resolve(new File([b],"edited-photo.jpg",{type:"image/jpeg"})):reject(new Error("Could not render the edited photo.")),"image/jpeg",.92))
}
function setProgress(v){const p=qs("#mediaEditorProgress"),bar=p?.querySelector("span");if(p){p.style.display="block";if(bar)bar.style.width=(v*100)+"%";}}
async function renderVideo(){
 if(!state.video||!state.canvas||!state.canvas.captureStream||!window.MediaRecorder) throw new Error("Video editing is not supported by this browser. Try a current Chrome or Edge browser.");
 const v=state.video,c=state.canvas,ctx=state.ctx;
 const start=clamp(state.start,0,state.duration),end=clamp(state.end??state.duration,start+.1,state.duration);
 v.pause();v.currentTime=start;await new Promise(r=>{const f=()=>{if(Math.abs(v.currentTime-start)<.08||v.readyState>=2){v.removeEventListener("timeupdate",f);r()}else{} };v.addEventListener("timeupdate",f);setTimeout(r,500)});
 const stream=c.captureStream(30);
 try{if(v.captureStream){v.muted=false;const vs=v.captureStream();vs.getAudioTracks().forEach(t=>stream.addTrack(t))}}catch(e){}
 const types=["video/webm;codecs=vp9,opus","video/webm;codecs=vp8,opus","video/webm"];
 const mime=types.find(t=>MediaRecorder.isTypeSupported(t));if(!mime)throw new Error("This browser cannot export an edited video.");
 const rec=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:Math.min(5000000,Math.max(1200000,(c.width*c.height*30)/4))});
 const chunks=[];rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
 const done=new Promise((resolve,reject)=>{rec.onstop=()=>resolve(new Blob(chunks,{type:mime}));rec.onerror=e=>reject(e.error||new Error("Video export failed."))});
 let raf=0;
 function frame(){
   if(v.currentTime>=end||v.ended){cancelAnimationFrame(raf);if(rec.state!=="inactive")rec.stop();v.pause();setProgress(1);return}
   draw();setProgress(clamp((v.currentTime-start)/(end-start),0,1));raf=requestAnimationFrame(frame)
 }
 v.currentTime=start;await v.play();rec.start(250);frame();
 const blob=await done;stream.getTracks().forEach(t=>t.stop());v.pause();v.currentTime=start;if(blob.size>58*1024*1024)throw new Error('Edited video is too large. Please shorten the video or lower its resolution.');return new File([blob],"edited-video.webm",{type:"video/webm"});
}
async function prepareFormData(form,fieldName){
 const fd=new FormData(form);
 if(!state.file||!state.kind)return fd;
 if(!state.dirty)return fd;
 setProgress(0);
 const edited=state.kind==="image"?await renderImage():await renderVideo();
 fd.delete(fieldName);fd.append(fieldName,edited,edited.name);return fd;
}
function attachInput(){
 const input=qs("#composerMedia");if(!input||input.dataset.mediaEditorBound)return;
 input.dataset.mediaEditorBound="1";input.addEventListener("change",()=>setFile(input.files?.[0]||null));
}
window.MediaEditor={setMode,clearFile,setFile,prepareFormData,reset:clearFile,attach:attachInput};
document.addEventListener("DOMContentLoaded",()=>{ensureUI();attachInput()});
})();