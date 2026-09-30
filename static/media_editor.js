/*
 * Media upload compatibility layer.
 *
 * Media editing was removed intentionally. The composer still calls the
 * MediaEditor API, so keep this tiny compatibility object instead of making
 * the upload page depend on an editor/transcoder.
 *
 * IMPORTANT: files are sent exactly as selected by the user. No canvas
 * rendering, JPEG conversion, rotation, filtering, text overlay, trimming,
 * or video transformation happens in the browser.
 */
(function(){
  'use strict';

  const state={dirty:false,type:null,source:null,mode:'post'};

  function setMode(mode){
    state.mode=mode||'post';
    state.dirty=false;
    state.source=null;
    state.type=null;
  }

  function clearFile(){
    const input=document.getElementById('composerMedia');
    if(input) input.value='';
    state.source=null;
    state.type=null;
    state.dirty=false;
  }

  function init(){
    const input=document.getElementById('composerMedia');
    if(input){
      input.addEventListener('change',function(){
        state.source=input.files && input.files[0] ? input.files[0] : null;
        state.type=state.source ? state.source.type : null;
        state.dirty=false;
      });
    }
  }

  // Compatibility API used by templates/user_home.html.
  // Return the native FormData without modifying the selected file.
  async function prepareFormData(form){
    return new FormData(form);
  }

  window.MediaEditor={
    init:init,
    prepareFormData:prepareFormData,
    setMode:setMode,
    clearFile:clearFile,
    state:state
  };

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',init,{once:true});
  }else{
    init();
  }
})();