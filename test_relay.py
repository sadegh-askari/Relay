import importlib.util,sys,json,threading,unittest,tempfile,time
from pathlib import Path
from unittest.mock import patch,call
import urllib.request,urllib.error,http.cookiejar
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE));spec=importlib.util.spec_from_file_location('relay',BASE/'server.py');tv=importlib.util.module_from_spec(spec);spec.loader.exec_module(tv)
import setup

def share(source='vpn',outbound='utun9',policy=False):
 return dict(ok=True,vpnService="profile",activeVPNService="profile" if outbound.startswith("utun") else "",source=source,outboundInterface=outbound,natInterfaces=['en0'] if source=='direct' else [outbound],sharingEnabled=True,sharingDevices=['en9'],downstreamDevice='en9',upstreamDevice='en0',vpnInterface='utun9',ethernetActive=True,gateway='192.168.2.1',expectedGateway='192.168.2.1',directPolicy=policy,ipv6Guard=True)
class ControllerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.state=patch.object(tv,'STATE',Path(self.tmp.name));self.state.start();self.config=patch.object(tv,'configuration',return_value={'gateway':'192.168.2.1','vpnService':'profile'});self.config.start()
 def tearDown(self):self.config.stop();self.state.stop();self.tmp.cleanup()
 def perform(self,action,helper,state='Connected',keep=False,auto=False):
  c=tv.Controller();c.keep_mac=keep;c.lock.acquire()
  with patch.object(tv,'sharing',side_effect=helper),patch.object(tv,'vpn_snapshot',return_value=('profile',state)),patch.object(tv,'set_vpn') as vpn,patch.object(tv,'wait_share_ready'):c._switch(action,auto)
  self.assertFalse(c.lock.locked());self.assertFalse(c.operation['busy']);return c,vpn
 def test_direct_changes_mac_then_sharing(self):
  calls=[]
  def helper(action='status'):calls.append(action);return share()
  c,vpn=self.perform('direct',helper);self.assertEqual(calls,['status','direct']);vpn.assert_called_once_with('profile',False);self.assertFalse(c.operation['error'])
 def test_independent_direct_never_touches_mac_even_recovery(self):
  calls=[]
  def helper(action='status'):
   calls.append(action)
   if action=='direct':raise tv.ControlError('NAT rejected')
   return share()
  c,vpn=self.perform('direct',helper,keep=True);vpn.assert_not_called();self.assertEqual(calls,['status','direct','vpn']);self.assertTrue(c.operation['error'])
 def test_independent_vpn_requires_connected_mac(self):
  calls=[]
  def helper(action='status'):calls.append(action);return share('direct','en0',True)
  c,vpn=self.perform('vpn',helper,'Disconnected',True);vpn.assert_not_called();self.assertEqual(calls,['status']);self.assertIn('Connect the selected VPN on the Mac first',c.operation['message'])
 def test_independent_direct_saves_preference(self):
  c,vpn=self.perform('direct',lambda action="status":share(),keep=True);vpn.assert_not_called();self.assertEqual(tv.Controller().desired_mode,'direct');self.assertTrue(tv.Controller().keep_mac)
 def test_failure_restores_mac_and_route(self):
  def helper(action='status'):
   if action=='direct':raise tv.ControlError('NAT rejected')
   return share()
  c,vpn=self.perform('direct',helper);self.assertEqual(vpn.call_args_list,[call('profile',False),call('profile',True)]);self.assertTrue(c.operation['error'])
 def test_helper_failure_never_changes_vpn(self):
  c,vpn=self.perform('direct',tv.ControlError('Missing helper'));vpn.assert_not_called()
 def test_confirmed_direct_with_mac_vpn_requires_policy(self):
  for policy,expected in [(False,'unknown'),(True,'direct')]:
   c=tv.Controller()
   with patch.object(tv,'sharing',return_value=share('direct','utun9',policy)),patch.object(tv,'vpn_snapshot',return_value=('profile','Connected')):self.assertEqual(c.snapshot()['mode'],expected)
 def test_wrong_gateway_output_and_tunnel_cannot_report_vpn(self):
  for field,value in [('gateway','192.168.3.1'),('sharingDevices',['en8']),('vpnInterface','utun8'),('natInterfaces',['en0']),('ipv6Guard',False),('directPolicy',True)]:
   c=tv.Controller();s=share();s[field]=value
   with patch.object(tv,'sharing',return_value=s),patch.object(tv,'vpn_snapshot',return_value=('profile','Connected')):self.assertEqual(c.snapshot()['mode'],'unknown')
 def test_valid_vpn(self):
  c=tv.Controller()
  with patch.object(tv,'sharing',return_value=share()),patch.object(tv,'vpn_snapshot',return_value=('profile','Connected')):self.assertEqual(c.snapshot()['mode'],'vpn')
 def test_preferences_persist_and_require_boolean(self):
  c=tv.Controller()
  with patch.object(c,'snapshot',return_value={'mode':'direct'}):c.preferences({'keepMacVPN':True})
  self.assertTrue(tv.Controller().keep_mac)
  for data in [{'keepMacVPN':'false'},{'keepMacVPN':True,'extra':True}]:
   with self.assertRaises(tv.ControlError):c.preferences(data)
 def test_external_disconnect_reconciles_without_changing_mac(self):
  c=tv.Controller()
  with patch.object(c,'snapshot',return_value={'ready':True,'vpnState':'Disconnected','mode':'unknown','sharing':share()}),patch.object(c,'start') as start:
   c.reconcile_once();start.assert_not_called();c.stable_since-=4;c.reconcile_once();start.assert_called_once_with('direct',automatic=True)
 def test_keep_mac_direct_survives_external_vpn_connection(self):
  c=tv.Controller();c.keep_mac=True;c.desired_mode='direct'
  with patch.object(c,'snapshot',return_value={'ready':True,'vpnState':'Connected','mode':'direct','sharing':share('direct','utun9',True)}),patch.object(c,'start') as start:c.reconcile_once();start.assert_not_called()
 def test_automatic_reconcile_never_calls_vpn_commands(self):
  c,vpn=self.perform('direct',lambda action="status":share(),auto=True);vpn.assert_not_called();self.assertFalse(c.operation['error'])
 def test_operation_lock_blocks_preference_mutation(self):
  c=tv.Controller();c.lock.acquire()
  with self.assertRaises(tv.ControlError):c.preferences({'keepMacVPN':True})

 def test_preference_save_never_polls_network(self):
  c=tv.Controller();c.cached={'mode':'vpn'}
  with patch.object(c,'snapshot',side_effect=AssertionError('Preference save must not poll')):
   result=c.preferences({'keepMacVPN':True})
  self.assertTrue(result['keepMacVPN']);self.assertEqual(c.desired_mode,'vpn')
 def test_manual_direct_never_controls_vpn(self):
  c=tv.Controller();c.vpn_service='other';c.lock.acquire()
  with patch.object(tv,'sharing',return_value=share()),patch.object(tv,'set_vpn') as vpn,patch.object(tv,'vpn_snapshot',side_effect=AssertionError('No selected VPN in manual mode')):
   c._switch('direct');vpn.assert_not_called()
  self.assertFalse(c.operation['error'])
 def test_manual_connect_requires_active_full_tunnel(self):
  c=tv.Controller();c.vpn_service='other';c.lock.acquire()
  with patch.object(tv,'sharing',return_value=share('direct','en0',True)) as helper,patch.object(tv,'set_vpn') as vpn:
   c._switch('vpn');vpn.assert_not_called();self.assertEqual(helper.call_count,1)
  self.assertIn('manually',c.operation['message'])
 def test_manual_reconnect_cannot_control_mac(self):
  c=tv.Controller();c.vpn_service='other';c.lock.acquire()
  with patch.object(tv,'sharing',return_value=share()),patch.object(tv,'set_vpn') as vpn:
   c._switch('reconnect');vpn.assert_not_called()
  self.assertTrue(c.operation['error'])
 def test_other_apps_disables_checkbox_on_server(self):
  c=tv.Controller();c.vpn_service='other'
  with self.assertRaises(tv.ControlError):c.preferences({'keepMacVPN':False})
 def test_language_and_provider_persist(self):
  c=tv.Controller()
  with patch.object(tv.vpn_apps,'profile',return_value={'app':'V2Box'}):c.preferences({'vpnService':'regular','language':'fa'})
  restored=tv.Controller();self.assertEqual(restored.selected_service(),'regular');self.assertEqual(restored.language,'fa')
 def test_new_install_defaults_to_english(self):
  self.assertEqual(tv.Controller().language,'en')
 def test_checkbox_and_app_can_be_saved_together_from_manual_mode(self):
  c=tv.Controller();c.vpn_service='other'
  with patch.object(tv.vpn_apps,'profile',return_value={'app':'V2Box'}):
   v=c.preferences({'vpnService':'regular','keepMacVPN':True})
  self.assertTrue(v['keepMacVPN']);self.assertEqual(v['vpnService'],'regular')
 def test_unknown_language_and_unavailable_provider_rejected(self):
  c=tv.Controller()
  with patch.object(tv.vpn_apps,'profile',return_value=None):
   for data in [{'language':'de'},{'vpnService':'missing'},{'vpnService':3}]:
    with self.assertRaises(tv.ControlError):c.preferences(data)
 def test_selecting_app_does_not_steal_active_tunnel(self):
  c=tv.Controller();c.vpn_service='regular'
  with patch.object(c,'snapshot',return_value={'ready':True,'vpnState':'Disconnected','mode':'vpn','sharing':share()}),patch.object(c,'start') as start:
   c.reconcile_once();c.stable_since-=4;c.reconcile_once();start.assert_not_called()
 def test_another_active_vpn_blocks_connection_without_stopping_it(self):
  c=tv.Controller();c.lock.acquire()
  s=share();s['activeVPNService']='another'
  with patch.object(tv,'sharing',return_value=s),patch.object(tv,'vpn_snapshot',return_value=('profile','Disconnected')),patch.object(tv,'set_vpn') as vpn:
   c._switch('vpn');vpn.assert_not_called()
  self.assertIn('Another VPN',c.operation['message'])
 def test_settings_selection_never_sends_vpn_commands(self):
  c=tv.Controller()
  with patch.object(tv.vpn_apps,'profile',return_value={'app':'V2Box'}),patch.object(tv,'sharing') as helper,patch.object(tv,'set_vpn') as vpn:
   c.preferences({'vpnService':'regular'});helper.assert_not_called();vpn.assert_not_called()
 def test_manual_mode_is_ready_without_selected_profile(self):
  c=tv.Controller();c.vpn_service='other'
  with patch.object(tv,'sharing',return_value=share()),patch.object(tv,'vpn_snapshot',side_effect=AssertionError('Manual mode needs no selected profile')):
   v=c.snapshot();self.assertTrue(v['manualVPN']);self.assertTrue(v['ready']);self.assertEqual(v['mode'],'vpn')

class TransitionTests(unittest.TestCase):
 def test_launch_reuses_selected_provider_before_connect(self):
  with patch.object(tv.vpn_apps,'profile',return_value={'provider':'org.amnezia.awg'}),patch.object(tv.vpn_apps,'installed_apps',return_value={'org.amnezia.awg':'/Applications/AmneziaWG.app'}),patch.object(tv,'run',return_value=(0,'')) as run,patch.object(tv,'wait_vpn') as wait:
   tv.set_vpn('selected',True)
   self.assertEqual(run.call_args_list,[call(['/usr/bin/open','-g','-a','/Applications/AmneziaWG.app']),call(['/usr/sbin/scutil','--nc','start','selected'])]);wait.assert_called_once_with('selected','Connected')
 def test_unapproved_or_missing_app_is_never_launched(self):
  for profile in [None,{'provider':'unapproved.app'}]:
   with patch.object(tv.vpn_apps,'profile',return_value=profile),patch.object(tv,'run') as run:
    with self.assertRaises(tv.ControlError):tv.set_vpn('selected',True)
    run.assert_not_called()
 def test_app_launch_failure_never_sends_connect_command(self):
  with patch.object(tv.vpn_apps,'profile',return_value={'provider':'org.amnezia.awg'}),patch.object(tv.vpn_apps,'installed_apps',return_value={'org.amnezia.awg':'/Applications/AmneziaWG.app'}),patch.object(tv,'run',return_value=(1,'')) as run:
   with self.assertRaises(tv.ControlError):tv.set_vpn('selected',True)
   self.assertEqual(run.call_count,1)
 def test_connected_must_remain_stable(self):
  responses=['Connected','Disconnected','Connected','Connected','Connected']
  with patch.object(tv,'run',side_effect=[(0,x) for x in responses]) as run,patch.object(tv.time,'sleep'):
   tv.wait_vpn('selected','Connected');self.assertEqual(run.call_count,5)
 def test_route_waits_until_matching_tunnel_is_ready(self):
  waiting=share('direct','en0',True);ready=share()
  with patch.object(tv,'sharing',side_effect=[waiting,waiting,ready]) as helper,patch.object(tv,'vpn_snapshot',return_value=('profile','Connected')),patch.object(tv.time,'sleep'):
   tv.wait_share_ready('profile');self.assertEqual(helper.call_count,3)
 def test_connected_tunnel_is_ready_while_sharing_is_still_direct(self):
  ready=share('direct','utun9',True);ready['vpnInterface']='';ready['vpnService']=''
  with patch.object(tv,'sharing',return_value=ready) as helper,patch.object(tv,'vpn_snapshot',side_effect=AssertionError('Route is ready')):
   tv.wait_share_ready('profile');helper.assert_called_once_with()
 def test_route_disconnect_does_not_change_sharing(self):
  with patch.object(tv,'sharing',return_value=share('direct','en0',True)) as helper,patch.object(tv,'vpn_snapshot',return_value=('profile','Disconnected')):
   with self.assertRaises(tv.ControlError):tv.wait_share_ready('profile')
   helper.assert_called_once_with()
 def test_active_profile_is_resolved_within_selected_app_only(self):
  first='E8D79118-786A-4730-B0B0-D8C8EDA8F7CD';second='8D8DCB6A-6170-47AF-AA22-1FF8554C92E0';other='FDDFAF59-0399-4ADA-9F8B-08BC8E0C24FE'
  listing=f'* (Disconnected) {first} VPN (org.amnezia.awg) "First"\n* (Connected) {second} VPN (org.amnezia.awg) "Second"\n* (Connected) {other} VPN (com.liguangming.Shadowrocket) "Other"'
  with patch.object(tv,'configuration',return_value={'vpnService':first}),patch.object(tv,'run',side_effect=[(0,listing),(0,'Connected\nPRIVATE')]) as run:
   self.assertEqual(tv.vpn_snapshot(first),(second,'Connected'));self.assertEqual(run.call_args.args[0][-1],second)
 def test_disabled_profile_cannot_be_controlled(self):
  uid='E8D79118-786A-4730-B0B0-D8C8EDA8F7CD'
  with patch.object(tv,'configuration',return_value={'vpnService':uid}),patch.object(tv,'run',return_value=(0,f'  (Disconnected) {uid} VPN (org.amnezia.awg) "Disabled"')):
   with self.assertRaises(tv.ControlError):tv.vpn_snapshot(uid)
 def test_main_name_uses_selected_provider_and_manual_fallback(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(tv,'STATE',Path(temp)),patch.object(tv,'configuration',return_value={'vpnService':'selected'}),patch.object(tv,'sharing',return_value=share('direct','en0',True)),patch.object(tv,'vpn_snapshot',return_value=('selected','Disconnected')),patch.object(tv.vpn_apps,'profile',return_value={'app':'Shadowrocket'}):
   c=tv.Controller();self.assertEqual(c.snapshot()['selectedVPNAppName'],'Shadowrocket');c.vpn_service='other';c.cached_at=0;self.assertEqual(c.snapshot()['selectedVPNAppName'],'VPN')

class SetupTests(unittest.TestCase):
 def setUp(self):
  self.inventory={'services':[{'id':'internet','name':'Wi-Fi','device':'en0','type':'IEEE80211','vpn':False,'address':'10.1.2.3','mask':'255.255.0.0'},{'id':'output','name':'USB Ethernet','device':'en9','type':'Ethernet','vpn':False},{'id':'vpn','name':'Provider','device':'','type':'VPN','vpn':True}]}
  self.config={'version':1,'upstreamService':'internet','downstreamDevice':'en9','vpnService':'vpn','gateway':'192.168.2.1'}
 def test_manual_setup_without_vpn(self):
  self.config['vpnService']='other';self.inventory['services']=self.inventory['services'][:2]
  self.assertEqual(setup.validate_selection(self.config,self.inventory),self.config)
 def test_valid(self):self.assertEqual(setup.validate_selection(self.config,self.inventory),self.config)
 def test_actual_subnet_overlap(self):
  self.config['gateway']='10.1.99.1'
  with self.assertRaises(setup.SetupError):setup.validate_selection(self.config,self.inventory)
 def test_same_adapter_and_bad_address_rejected(self):
  for field,value in [('gateway','8.8.8.1'),('gateway','192.168.2.4'),('downstreamDevice','en0'),('vpnService','missing')]:
   with self.subTest(field=field,value=value),self.assertRaises(setup.SetupError):setup.validate_selection({**self.config,field:value},self.inventory)
 def test_hotspot_requires_native_setup(self):
  self.inventory['services'][0]['type']='Ethernet';self.inventory['services'][1]['type']='IEEE80211'
  with self.assertRaises(setup.SetupError):setup.validate_selection(self.config,self.inventory)
  self.inventory['existing']={'enabled':True,'devices':['en9'],'primaryService':'internet'};self.assertEqual(setup.validate_selection(self.config,self.inventory),self.config)

 def test_hotspot_validates_layout_and_strips_ui_field(self):
  self.inventory['services'][0]['type']='Ethernet';self.inventory['services'][1]['type']='IEEE80211'
  self.inventory['existing']={'enabled':True,'devices':['en9'],'primaryService':'internet'}
  self.assertEqual(setup.validate_selection({**self.config,'topology':'hotspot'},self.inventory),self.config)
  self.inventory['existing']['primaryService']='wrong'
  with self.assertRaises(setup.SetupError):setup.validate_selection({**self.config,'topology':'hotspot'},self.inventory)
 def test_hotspot_rejects_wifi_input_and_invalid_layout(self):
  for layout in ['hotspot','invalid']:
   with self.assertRaises(setup.SetupError):setup.validate_selection({**self.config,'topology':layout},self.inventory)
 def test_hotspot_settings_uses_only_fixed_native_destination(self):
  with patch.object(setup.subprocess,'run',return_value=type('Result',(),{'returncode':0})()) as run:
   setup.open_hotspot_settings();self.assertEqual(run.call_args.args[0],['/usr/bin/open','x-apple.systempreferences:com.apple.Sharing-Settings.extension?Internet'])
 def test_topology_detects_wifi_output_without_changing_configuration(self):
  setup.topology.cache_clear()
  self.inventory['services'][1]['type']='IEEE80211'
  with patch.object(setup,'inventory',return_value=self.inventory) as detect:
   self.assertEqual(setup.topology(BASE,'en9'),'hotspot');self.assertEqual(setup.topology(BASE,'en9'),'hotspot');self.assertEqual(detect.call_count,1)
  setup.topology.cache_clear()

class DiscoveryTests(unittest.TestCase):
 def test_missing_apps_invalid_profiles_and_ppp_are_excluded(self):
  import subprocess
  v=tv.vpn_apps;v._cache=None
  uid='9D11813B-B884-420F-892B-5E06B6D088D3'
  listing='* (Disconnected) '+uid+' VPN (hossin.asaadi.V2Box) "Test profile" [VPN:test.app]\n'
  listing+='* (Disconnected) 97961466-3EE7-4EFC-A2B7-60FE1E197774 VPN (missing.app) "Absent"\n'
  listing+='  (Invalid) 2DDBC57F-6FD4-4260-AD29-EFDE9F795F0C VPN (hossin.asaadi.V2Box) "Invalid"\n'
  listing+='* (Disconnected) 00DD3925-4EA3-4E58-A5F7-08199B064FF8 PPP --> L2TP "Legacy"\n'
  def command(args,**kwargs):return subprocess.CompletedProcess(args,0,listing if args[-1]=='list' else 'Disconnected\nSECRET-CONFIG', '')
  with patch.object(v,'installed_apps',return_value={'hossin.asaadi.V2Box':'V2Box'}),patch.object(v.subprocess,'run',side_effect=command):
   result=v.discover(True)
  self.assertEqual(len(result),1);self.assertEqual(result[0]['profiles'],[{'id':uid,'name':'Test profile','state':'Disconnected'}]);self.assertNotIn('SECRET',json.dumps(result))
  v._cache=None
 def test_unapproved_installed_app_is_excluded(self):
  import subprocess
  v=tv.vpn_apps;v._cache=None
  listing='* (Disconnected) 9D11813B-B884-420F-892B-5E06B6D088D3 VPN (com.speedify.mac) "Speedify"\n'
  with patch.object(v,'installed_apps',return_value={'com.speedify.mac':'Speedify'}),patch.object(v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,listing,'')):
   self.assertEqual(v.discover(True),[])
  v._cache=None
 def test_unresponsive_control_profile_is_excluded(self):
  import subprocess
  v=tv.vpn_apps;v._cache=None
  listing='* (Disconnected) 9D11813B-B884-420F-892B-5E06B6D088D3 VPN (hossin.asaadi.V2Box) "Test"\n'
  with patch.object(v,'installed_apps',return_value={'hossin.asaadi.V2Box':'V2Box'}),patch.object(v.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,listing,''),subprocess.CompletedProcess([],1,'Unknown','')]):self.assertEqual(v.discover(True),[])
  v._cache=None
 def test_wrapped_ios_app_and_malformed_plist(self):
  import plistlib
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);valid=root/'Pro.app/Wrapper/Inner.app';valid.mkdir(parents=True);(valid/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'pro.app'}))
   bad=root/'Malformed.app/Contents';bad.mkdir(parents=True);(bad/'Info.plist').write_bytes(b'<?xml invalid>')
   self.assertEqual(tv.vpn_apps.installed_apps([root]),{'pro.app':'Pro'})

class ExperimentalTests(unittest.TestCase):
 def test_only_requested_experimental_apps_are_discovered(self):
  import subprocess
  v=tv.vpn_apps;v._cache=None
  with patch.object(v,'installed_apps',return_value={'org.openvpn.client.app':'OpenVPN','com.happ.www':'Happ','com.cisco.anyconnect.gui':'Cisco'}),patch.object(v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')):
   apps=v.discover(True)
  self.assertEqual({a['name'] for a in apps},{'OpenVPN Connect','Happ'});self.assertTrue(all(a['experimental'] for a in apps));v._cache=None
 def test_experimental_commands_never_touch_sharing(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(tv,'STATE',Path(temp)),patch.object(tv,'configuration',return_value={'vpnService':'other'}):
   c=tv.Controller();c.vpn_service='app:happ'
   with patch.object(tv.vendor_control,'command') as command,patch.object(tv,'sharing') as helper,patch.object(tv,'set_vpn') as vpn:
    result=c.experimental_command({'action':'connect'});command.assert_called_once_with('app:happ','connect');helper.assert_not_called();vpn.assert_not_called();self.assertIn('unchanged',result['message'])
   with patch.object(c,'snapshot',side_effect=AssertionError('No automatic sharing for experimental apps')):c.reconcile_once()
 def test_experimental_mode_cannot_claim_automatic_support(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(tv,'STATE',Path(temp)),patch.object(tv,'configuration',return_value={'vpnService':'other'}):
   c=tv.Controller();c.vpn_service='app:openvpn'
   with self.assertRaises(tv.ControlError):c.start('vpn')
   with self.assertRaises(tv.ControlError):c.preferences({'keepMacVPN':False})
 def test_fixed_happ_commands_reject_unapproved_inputs(self):
  import subprocess
  v=tv.vendor_control
  with patch.object(v.vpn_apps,'installed_apps',return_value={'com.happ.www':'/Applications/Happ.app'}),patch.object(v.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as run:
   v.command('app:happ','connect');self.assertEqual(run.call_args.args[0],['/usr/bin/open','-g','-a','/Applications/Happ.app','happ://connect'])
  with patch.object(v.subprocess,'run') as run:
   for service,action in [('app:cisco','connect'),('app:happ','connect;bad')]:
    with self.assertRaises(v.VendorError):v.command(service,action)
   run.assert_not_called()
 def test_openvpn_reuses_valid_last_attempt_and_does_not_expose_credentials(self):
  import subprocess
  v=tv.vendor_control
  with tempfile.TemporaryDirectory() as temp,patch.object(v.Path,'home',return_value=Path(temp)),patch.object(v.vpn_apps,'installed_apps',return_value={'org.openvpn.client.app':'/Applications/OpenVPN Connect.app'}):
   file=Path(temp)/'Library/Application Support/OpenVPN Connect/config.json';file.parent.mkdir(parents=True)
   file.write_text(json.dumps({'persist:root':json.dumps({'status':json.dumps({'previouslyConnectedProfile':{'id':'12345'},'profiles':{'private':'SECRET'}})})}))
   def invoke(args,**kwargs):return subprocess.CompletedProcess(args,0,'[{"id":"12345"}]' if '--list-profiles' in args else '', '')
   with patch.object(v.subprocess,'run',side_effect=invoke) as run:
    v.command('app:openvpn','connect');self.assertEqual(run.call_args.args[0][-1],'--connect-shortcut=12345');self.assertNotIn('SECRET',str(run.call_args_list))

class FakeController:
 language='en'
 def settings(self):return {'language':'en','apps':[], 'vpnService':'other'}
 def __init__(self):self.calls=[];self.lock=threading.Lock()
 def snapshot(self):return {'mode':'vpn'}
 def start(self,mode):self.calls.append(mode);return {'accepted':True}
 def preferences(self,data):self.calls.append(data);return {'keepMacVPN':data['keepMacVPN']}
class HTTPTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  tv.BASE=BASE;cls.controller=FakeController();cls.server=tv.AppServer(('127.0.0.1',0),cls.controller);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url=f'http://127.0.0.1:{cls.server.server_port}'
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
 def setUp(self):
  self.cookies=http.cookiejar.CookieJar();self.client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(self.cookies));self.controller.calls.clear()
 def request(self,path,data=None,headers=None):
  h={'Content-Type':'application/json'} if data is not None else {};h.update(headers or {});req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
  try:
   with self.client.open(req,timeout=5) as r:return r.status,r.read(),r.headers
  except urllib.error.HTTPError as r:return r.code,r.read(),r.headers
 def session(self):return json.loads(self.request('/api/session')[1])['csrf']
 def test_no_pin_session_and_csrf(self):
  self.assertEqual(self.request('/api/mode',{'mode':'direct'})[0],401);csrf=self.session();cookie=next(iter(self.cookies));self.assertTrue(cookie.has_nonstandard_attr('HttpOnly'));self.assertEqual(cookie.get_nonstandard_attr('SameSite'),'Strict');self.assertEqual(self.request('/api/mode',{'mode':'direct'},{'X-TV-CSRF':csrf,'Origin':self.url})[0],202)
 def test_cross_site_requests_rejected(self):
  csrf=self.session()
  for headers in [{},{'X-TV-CSRF':csrf,'Origin':'https://evil.example'}]:self.assertEqual(self.request('/api/preferences',{'keepMacVPN':True},headers)[0],403)
  self.assertEqual(self.controller.calls,[])
 def test_public_host_rejected(self):self.assertEqual(self.request('/',headers={'Host':'evil.example'})[0],403)
 def test_setup_assets_local_only(self):
  csrf=self.session()
  with patch.object(tv.Handler,'local_setup',return_value=False):
   code,body,headers=self.request('/setup.css');self.assertEqual(code,200);self.assertEqual(headers['Content-Type'],'text/css; charset=utf-8');self.assertIn(b'.settings',body)
   self.assertEqual(self.request('/setup')[0],403);self.assertEqual(self.request('/api/setup')[0],403);self.assertEqual(self.request('/api/setup',{}, {'X-TV-CSRF':csrf})[0],403)
 def test_hotspot_open_requires_local_session_csrf_and_fixed_action(self):
  self.assertEqual(self.request('/api/hotspot-settings',{'action':'open'})[0],401)
  csrf=self.session()
  with patch.object(tv,'open_hotspot_settings',return_value={'message':'Opened'}) as opening:
   self.assertEqual(self.request('/api/hotspot-settings',{'action':'open'})[0],403)
   with patch.object(tv.Handler,'local_setup',return_value=False):
    self.assertEqual(self.request('/api/hotspot-settings',{'action':'open'},{'X-TV-CSRF':csrf})[0],403)
   self.assertEqual(self.request('/api/hotspot-settings',{'action':'other'},{'X-TV-CSRF':csrf})[0],400)
   opening.assert_not_called()
   self.assertEqual(self.request('/api/hotspot-settings',{'action':'open'},{'X-TV-CSRF':csrf})[0],200);opening.assert_called_once_with()
 def test_setup_requires_session(self):self.assertEqual(self.request('/api/setup')[0],401)
 def test_assets_and_headers(self):
  code,body,headers=self.request('/');self.assertEqual(code,200);self.assertIn(b'Relay',body);self.assertNotIn(b'LG G3',body);self.assertNotIn(b'MikroTik',body);self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy']);self.assertEqual(self.request('/../server.py')[0],404)
 def test_offline_persian_font_and_unified_settings(self):
  code,body,headers=self.request('/fonts/Vazirmatn-Regular.woff2');self.assertEqual(code,200);self.assertEqual(headers['Content-Type'],'font/woff2');self.assertTrue(body.startswith(b'wOF2'))
  body=self.request('/settings')[1];self.assertIn(b'setup-form',body);self.assertNotIn(b'vpn-profile',body)
  self.assertEqual(body,self.request('/setup')[1]);self.assertNotIn(b'LOCAL NETWORK',self.request('/')[1])
 def test_html_bootstraps_saved_language_before_reveal(self):
  with patch.object(self.controller,'language','fa',create=True):
   for page in ('/','/settings','/guide','/about'):
    code,body,headers=self.request(page);self.assertEqual(code,200);self.assertIn(b'data-language="fa"',body);self.assertIn(b'dir="rtl"',body);self.assertIn(b'data-page-ready="false"',body);self.assertIn(b'/shared.css',body)
 def test_about_assets_available_from_network_client(self):
  with patch.object(tv.Handler,'local_setup',return_value=False):
   self.assertEqual(self.request('/about')[0],200);self.assertEqual(self.request('/app-info.json')[0],200);self.assertEqual(self.request('/shared.css')[0],200);self.assertEqual(self.request('/preferences-exit.js')[0],200);self.assertEqual(self.request('/manifest.webmanifest')[0],200);self.assertEqual(self.request('/apple-touch-icon.png')[0],200)
 def test_preferences_endpoint(self):
  csrf=self.session();self.assertEqual(self.request('/api/preferences',{'keepMacVPN':True},{'X-TV-CSRF':csrf})[0],200);self.assertEqual(self.controller.calls,[{'keepMacVPN':True}])
 def test_settings_require_session_and_are_available_on_tv(self):
  self.assertEqual(self.request('/api/settings')[0],401);self.session()
  with patch.object(tv.Handler,'local_setup',return_value=False):
   self.assertEqual(self.request('/settings')[0],200);self.assertEqual(self.request('/api/settings')[0],200)
 def test_session_recovery(self):
  old=self.session();self.server.sessions.clear();self.assertEqual(self.request('/api/mode',{'mode':'vpn'},{'X-TV-CSRF':old})[0],401);new=self.session();self.assertNotEqual(new,old)
class OnDemandFailureTests(unittest.TestCase):
 def test_disconnect_failure_explains_automatic_reconnection(self):
  with patch.object(tv,'run',return_value=(0,'Connected\n')),patch.object(tv.time,'sleep'):
   with self.assertRaisesRegex(tv.ControlError,'Connect On Demand'):tv.wait_vpn('selected','Disconnected')

class CatalogTests(unittest.TestCase):
 def test_catalog_fresh_installed_and_uninstalled_groups(self):
  configured={'id':'dev.hexasoftware.V2Box','name':'V2Box Pro','profiles':[{'id':'test','state':'Disconnected'}]}
  with patch.object(tv.vpn_apps,'discover',return_value=[configured]) as discovery,patch.object(tv.vpn_apps,'installed_apps',return_value={'dev.hexasoftware.V2Box':'V2Box Pro','com.wireguard.macos':'WireGuard'}):
   rows=tv.vpn_apps.catalog();discovery.assert_called_once_with(refresh=True);self.assertEqual([r['name'] for r in rows if r['installed']],['V2Box Pro','WireGuard']);wire=next(r for r in rows if r['name']=='WireGuard');self.assertFalse(wire['available']);self.assertEqual(wire['profiles'],[]);self.assertTrue(next(r for r in rows if r['name']=='V2Box Pro')['available']);self.assertFalse(any(r['name']=='Cisco Secure Client' for r in rows))

if __name__=='__main__':unittest.main(verbosity=2)
