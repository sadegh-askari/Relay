'use strict';
(function(){var destination='/',leaving=false,saving=false,previousFocus=null;var dialog=document.getElementById('unsaved-dialog'),error=document.getElementById('unsaved-error');
function dirty(){return RelaySettings.dirty()||RelayNetwork.dirty();}
function busy(){return RelaySettings.busy()||RelayNetwork.busy();}
function buttons(disabled){['exit-save','exit-discard','exit-cancel'].forEach(function(id){document.getElementById(id).disabled=disabled;});document.getElementById('exit-save').textContent=I18n.t(disabled?'Saving…':'Save and exit');dialog.setAttribute('aria-busy',String(disabled));}
function close(){if(saving)return;dialog.hidden=true;if(previousFocus)previousFocus.focus();}
function leave(){leaving=true;window.location.assign(destination);}
document.addEventListener('click',function(event){var link=event.target.closest('a[href]');if(!link||link.target==='_blank'||event.metaKey||event.ctrlKey)return;var url=new URL(link.href,location.href);if(url.origin!==location.origin||url.pathname===location.pathname)return;if(!dirty()&&!busy())return;event.preventDefault();destination=link.href;previousFocus=link;error.hidden=true;dialog.hidden=false;buttons(false);if(busy()){error.textContent=I18n.t('Wait for the current connection change to finish.');error.hidden=false;document.getElementById('exit-save').disabled=true;document.getElementById('exit-discard').disabled=true;}document.getElementById('exit-cancel').focus();});
document.getElementById('exit-cancel').addEventListener('click',close);
document.getElementById('exit-discard').addEventListener('click',function(){if(!busy()&&!saving)leave();});
document.getElementById('exit-save').addEventListener('click',function(){if(busy()||saving)return;saving=true;buttons(true);error.hidden=true;var networkDirty=RelayNetwork.dirty();var operation=RelaySettings.dirty()?RelaySettings.save():Promise.resolve();operation.then(function(){return networkDirty?RelayNetwork.apply():null;}).then(leave).catch(function(e){error.textContent=I18n.t(e.message);error.hidden=false;}).then(function(){saving=false;buttons(false);});});
document.addEventListener('keydown',function(event){if(dialog.hidden)return;if(event.key==='Escape'){event.preventDefault();close();}if(event.key==='Tab'){var enabled=Array.prototype.filter.call(dialog.querySelectorAll('button'),function(b){return !b.disabled;});var first=enabled[0],last=enabled[enabled.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
window.addEventListener('beforeunload',function(event){if(!leaving&&(dirty()||busy())){event.preventDefault();event.returnValue='';}});
})();
