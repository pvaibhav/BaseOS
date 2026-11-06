#!/usr/bin/env python3
"""Host-only U4-G builder. Existing pinned sources/deps are reused in place."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
ROOT=Path(__file__).resolve().parents[1]
W=ROOT/'work/mainline/u4g'
CONTAINER='baseos-h700-mesa-build'
PIN='d4a7d7503524cc469fe775242f3f925d4dd56c88'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def run(*args): subprocess.run(args,cwd=ROOT,check=True)
def identity():
    paths=[ROOT/'tools/build-h700-sdl.py',ROOT/'graphics/mesa/build.sh',ROOT/'graphics/mesa/pins.json',ROOT/'work/mainline/frontend-abi/stock-sdl-76de58c8.json']
    paths+=sorted(p for p in (ROOT/'graphics/sdl2').iterdir() if p.is_file())
    return {str(p.relative_to(ROOT)):sha(p) for p in paths}
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cached',action='store_true',help='verify frozen input and output identities without rebuilding')
    ap.add_argument('--finalize',action='store_true',help='verify and freeze the already logged build without rebuilding')
    a=ap.parse_args(); W.mkdir(parents=True,exist_ok=True)
    info=json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]
    pins=json.loads((ROOT/'graphics/mesa/pins.json').read_text())
    assert info['Image']==pins['container_id']
    assert any(m['Destination']=='/src' and Path(m['Source']).resolve()==ROOT for m in info['Mounts'])
    if a.cached:
        stamp=json.loads((W/'build-identity.json').read_text())
        assert stamp['inputs']==identity(),'build recipe drift'
        assert stamp['source']=={str(p.relative_to(W/'source')):sha(p) for p in (W/'source').rglob('*') if p.is_file()},'SDL source drift'
        assert stamp['devel']=={str(p.relative_to(W/'devel')):sha(p) for p in (W/'devel').rglob('*') if p.is_file()},'development output drift'
        for n,h in stamp['runtime'].items(): assert sha(W/'runtime'/n)==h,n
        run('docker','exec',CONTAINER,'python3','/src/graphics/sdl2/verify.py')
        print('PASS guarded cached input/source/runtime identity'); return
    if a.finalize:
        run('docker','exec',CONTAINER,'python3','/src/graphics/sdl2/verify.py','--stage')
        freeze(); return
    s=W/'source'/('SDL-malifbdev-rot-'+PIN)
    if not s.exists():
        s.parent.mkdir(parents=True,exist_ok=True); archive=s.parent/'sdl.download'
        run('curl','-fLsS','https://codeload.github.com/JohnnyonFlame/SDL-malifbdev-rot/tar.gz/'+PIN,'-o',str(archive))
        assert sha(archive)=='6485a6178a4a1554a30d7f7f68b55c3af4d2a52daea489bb09d3b479626c4d1a'
        with tarfile.open(archive) as t: t.extractall(s.parent,filter='data')
        archive.unlink()
        for name in ('sdl2-h700.patch','kmsdrm.patch'):
            subprocess.run(['patch','-p1','-i',str(ROOT/'graphics/sdl2'/name)],cwd=s,check=True)
    for p in (ROOT/'graphics/sdl2').glob('SDL_kmsdrmrotation.*'):
        (s/'src/video/kmsdrm'/p.name).write_bytes(p.read_bytes())
    # The already qualified Mesa container is checked, never replaced or repinned.
    info=json.loads(subprocess.check_output(['docker','inspect',CONTAINER]))[0]
    pins=json.loads((ROOT/'graphics/mesa/pins.json').read_text())
    assert info['Image']==pins['container_id']
    assert any(m['Destination']=='/src' and Path(m['Source']).resolve()==ROOT for m in info['Mounts'])
    run('docker','start',CONTAINER)
    # libdrm ABI/header development output is the existing foundation output.
    import shutil
    d=W/'devel/usr'; (d/'lib/pkgconfig').mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/'work/mainline/graphics/devel/usr/include',d/'include',dirs_exist_ok=True)
    for p in (ROOT/'work/mainline/graphics/devel/usr/lib').glob('libdrm.so*'):
        dest=d/'lib'/p.name
        if not dest.exists(): shutil.copy2(p,dest,follow_symlinks=False)
    shutil.copy2(ROOT/'work/mainline/graphics/devel/usr/lib/pkgconfig/libdrm.pc',d/'lib/pkgconfig')
    run('docker','exec','-e','BASEOS_MESA_PROFILE=standard-egl',CONTAINER,'sh','/src/graphics/mesa/build.sh')
    run('docker','exec',CONTAINER,'sh','/src/graphics/sdl2/build.sh')
    run('docker','exec',CONTAINER,'python3','/src/graphics/sdl2/verify.py','--stage')
    freeze()
def freeze():
    stamp={'devel':{str(p.relative_to(W/'devel')):sha(p) for p in (W/'devel').rglob('*') if p.is_file()},'inputs':identity(),'source':{str(p.relative_to(W/'source')):sha(p) for p in (W/'source').rglob('*') if p.is_file()},'runtime':{str(p.relative_to(W/'runtime')):sha(p) for p in (W/'runtime').rglob('*') if p.is_file()}}
    (W/'build-identity.json').write_text(json.dumps(stamp,indent=2)+'\n')
if __name__=='__main__': main()
