"use strict";
var RelayAdapterDefaults=(function(){
function choose(services,mode,current,remembered,configured,existing){
 current=current||{};remembered=remembered||{};configured=configured||{};existing=existing||{};
 var physical=services.filter(function(s){return !s.vpn&&(s.type==='Ethernet'||s.type==='IEEE80211');});
 var inputs=physical.filter(function(s){return mode!=='hotspot'||s.type==='Ethernet';});
 var outputType=mode==='hotspot'?'IEEE80211':'Ethernet';
 function input(id){return inputs.filter(function(s){return s.id===id;})[0];}
 function outputs(up){return physical.filter(function(s){return s.type===outputType&&(!up||s.device!==up.device);});}
 function pick(rows,preferred,kind){
  for(var i=0;i<preferred.length;i++){var found=rows.filter(function(s){return (kind==='input'?s.id:s.device)===preferred[i];})[0];if(found)return found;}
  return rows.reduce(function(best,s){var score=(s.active?20:0)+(kind==='input'&&s.address?100:0);return !best||score>best.score?{value:s,score:score}:best;},null);
 }
 function value(result){return result&&result.value?result.value:result;}
 var up=value(pick(inputs,[remembered.upstream,current.upstream,configured.upstreamService,existing.primaryService],'input'));
 // When one Ethernet adapter is reserved for router output, use Wi-Fi as input.
 if(up&&!outputs(up).length){var alternatives=inputs.filter(function(s){return outputs(s).length;});if(alternatives.length)up=value(pick(alternatives,[remembered.upstream,current.upstream,configured.upstreamService,existing.primaryService],'input'));}
 var out=value(pick(outputs(up),[remembered.downstream,current.downstream,configured.downstreamDevice,(existing.devices||[])[0]],'output'));
 return {upstream:up?up.id:'',downstream:out?out.device:'',inputs:inputs,outputs:outputs(up)};
}
return {choose:choose};})();
