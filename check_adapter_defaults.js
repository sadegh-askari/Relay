const fs=require('fs'),vm=require('vm'),assert=require('assert');const scope={};vm.runInNewContext(fs.readFileSync(require('path').join(__dirname,'network-defaults.js'),'utf8'),scope);const choose=scope.RelayAdapterDefaults.choose;
const wifi={id:'wifi',device:'en0',type:'IEEE80211',vpn:false,active:true,address:'192.168.100.48'},spare={id:'spare',device:'en4',type:'Ethernet',vpn:false,active:false,address:''},router={id:'router',device:'en9',type:'Ethernet',vpn:false,active:true,address:''},modem={id:'modem',device:'en10',type:'Ethernet',vpn:false,active:true,address:'192.168.100.5'};
const services=[wifi,spare,router,modem],cfg={upstreamService:'wifi',downstreamDevice:'en9'};
function pair(result){return [result.upstream,result.downstream];}
assert.deepEqual(pair(choose(services,'hotspot',{upstream:'wifi',downstream:'en9'},{},cfg)),['modem','en0']);
assert.deepEqual(pair(choose([wifi,spare,router],'hotspot',{upstream:'wifi',downstream:'en9'},{},cfg)),['router','en0']);
assert.deepEqual(pair(choose(services,'router',{upstream:'modem',downstream:'en0'},{upstream:'wifi',downstream:'en9'},cfg)),['wifi','en9']);
assert.deepEqual(pair(choose(services,'hotspot',{upstream:'spare',downstream:'en0'},{},cfg)),['spare','en0']);
assert.deepEqual(pair(choose([wifi,router],'router',{upstream:'router',downstream:'en0'},{},{})),['wifi','en9']);
assert.deepEqual(pair(choose(services,'router',{upstream:'modem',downstream:'en10'},{},{})),['modem','en9']);
assert.deepEqual(pair(choose(services,'hotspot',{upstream:'wifi',downstream:'en9'},{upstream:'spare',downstream:'en0'},cfg)),['spare','en0']);
assert.deepEqual(pair(choose([wifi],'hotspot',{},{})),['','en0']);
console.log('8 adapter selection scenarios passed');
