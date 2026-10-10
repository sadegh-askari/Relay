#!/usr/bin/env python3
"""Build an ad-hoc signed universal Relay app. Run with universal2 Python 3.12."""
import json, plistlib, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BUILD=ROOT/'build'; DIST=ROOT/'dist'; APP=DIST/'Relay.app'
def run(*args): subprocess.run(list(map(str,args)),cwd=ROOT,check=True)
def main():
 BUILD.mkdir(exist_ok=True);DIST.mkdir(exist_ok=True)
 for name,source,frameworks in [('Relay','Relay.swift',['AppKit','WebKit','ServiceManagement']),('sharectl','ShareControl.swift',['SystemConfiguration'])]:
  slices=[]
  for arch in ('arm64','x86_64'):
   target=BUILD/(name+'-'+arch);slices.append(target)
   command=['xcrun','swiftc','-O','-module-cache-path',BUILD/'swift-cache','-target',arch+'-apple-macos13.0',ROOT/source,'-o',target]
   for framework in frameworks:command+=['-framework',framework]
   run(*command)
  run('lipo','-create',*slices,'-output',BUILD/name)
 run(sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--name','relay-server','--target-arch','universal2','--distpath',BUILD/'runtime','--workpath',BUILD/'pyinstaller','--specpath',BUILD,ROOT/'server.py')
 if APP.exists():shutil.rmtree(APP)
 contents=APP/'Contents';resources=contents/'Resources';website=resources/'website'
 (contents/'MacOS').mkdir(parents=True);website.mkdir(parents=True)
 shutil.copy2(BUILD/'Relay',contents/'MacOS/Relay');shutil.copytree(BUILD/'runtime/relay-server',resources/'runtime',symlinks=True)
 for f in ROOT.iterdir():
  if f.suffix in ('.html','.css','.js','.svg','.zsh','.png','.webmanifest') and f.name not in ('check_adapter_defaults.js',):shutil.copy2(f,website/f.name)
 shutil.copy2(ROOT/'app-info.json',website/'app-info.json');shutil.copy2(BUILD/'sharectl',website/'sharectl');shutil.copytree(ROOT/'fonts',website/'fonts')
 for name in ('Relay.icns','ICON-LICENSE.txt','PYTHON-LICENSE.txt','PYINSTALLER-LICENSE.txt'):shutil.copy2(ROOT/'assets'/name,resources/name)
 version=json.loads((ROOT/'app-info.json').read_text())['version']
 info={'CFBundleIdentifier':'local.relay.tv','CFBundleName':'Relay','CFBundleDisplayName':'Relay','CFBundleExecutable':'Relay','CFBundlePackageType':'APPL','CFBundleShortVersionString':version,'CFBundleVersion':'8','CFBundleIconFile':'Relay.icns','LSMinimumSystemVersion':'13.0','LSUIElement':True,'NSHighResolutionCapable':True,'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},'NSHumanReadableCopyright':'Relay · Personal build'}
 (contents/'Info.plist').write_bytes(plistlib.dumps(info));(contents/'PkgInfo').write_text('APPL????')
 run('codesign','--force','--deep','--sign','-',APP);run('codesign','--verify','--deep','--strict',APP)
 print(APP)
if __name__=='__main__':main()
