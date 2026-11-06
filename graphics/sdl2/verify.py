"""U4-G real AArch64 ELF closure and host build checks; no hardware claims."""
import argparse, hashlib, json, os, re, shutil, subprocess
from pathlib import Path
ROOT=Path('/src')
W=ROOT/'work/mainline/u4g'
F=ROOT/'work/mainline/graphics'
def out(*args):
    return subprocess.check_output(args, text=True)

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def elf(p):
    dynamic = out('readelf', '-dW', str(p))
    assert 'AArch64' in out('readelf', '-h', str(p)), str(p)
    assert not any(x in dynamic.lower() for x in ('mali','libllvm','libexpat','libzstd','libz.so')), str(p)
    assert '/src/' not in dynamic, 'build path leaked into dynamic section: ' + str(p)
    needs = {}
    version_text = out('readelf', '-VW', str(p))
    provider = None
    if 'Version needs section' in version_text:
        for line in version_text.split('Version needs section', 1)[1].splitlines():
            match = re.search(r'File: (\S+)', line)
            if match:
                provider = match[1]
                needs[provider] = []
            match = re.search(r'Name: (\S+)', line)
            if match and provider:
                needs[provider].append(match[1])
    return {
        'version_needs': needs,
        'symbolic_binding': 'SYMBOLIC' in dynamic,
        'sha256': sha(p), 'bytes': p.stat().st_size, 'mode': oct(p.stat().st_mode & 0o777),
        'soname': re.findall(r'\(SONAME\).*?\[(.*?)\]', dynamic),
        'needed': re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic),
        'rpath': re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', dynamic),
    }

def symbols(p, defined):
    result = set()
    for line in out('readelf', '--dyn-syms', '-W', str(p)).splitlines():
        fields = line.split()
        if len(fields) < 8 or fields[4] not in ('GLOBAL', 'WEAK'):
            continue
        if (fields[6] != 'UND') == defined:
            name = fields[7].replace('@@', '@')
            result.add(name)
            if defined:
                result.add(name.split('@')[0])
    return result


ap=argparse.ArgumentParser();ap.add_argument('--stage',action='store_true');args=ap.parse_args()
runtime=W/'runtime/usr/lib'
if args.stage:
    if (W/'runtime').exists(): shutil.rmtree(W/'runtime')
    runtime.mkdir(parents=True)
    for p in (W/'devel/usr/lib').rglob('*'):
        if not (p.is_file() or p.is_symlink()) or '.so' not in p.name: continue
        if any(x in p.name for x in ('GLESv1','EGL_mesa','noop','shim')): continue
        target=runtime/p.relative_to(W/'devel/usr/lib');target.parent.mkdir(parents=True,exist_ok=True)
        if p.is_symlink(): target.symlink_to(p.readlink())
        else:
            shutil.copyfile(p,target);subprocess.run(['strip','--strip-unneeded',str(target)],check=True)
files={p.name:p for p in runtime.rglob('*') if p.is_file()}
providers=dict(files);providers.update({p.name:p for p in (F/'harvested').iterdir()})
records={p.name:elf(p) for p in runtime.rglob('*') if p.is_file() and not p.is_symlink()}
assert {'libEGL.so.1','libGLESv2.so.2','libgbm.so.1','libdrm.so.2','libSDL2-2.0.so.0'} <= set(files), sorted(files)
for n in ('libEGL.so.1','libGLESv2.so.2','libgbm.so.1','libdrm.so.2','libSDL2-2.0.so.0'):
    assert elf(files[n])['soname']==[n]
assert 'libmali' not in str(records).lower()
assert all('libGLESv1_CM.so.1' not in r['needed'] for r in records.values())
config=(W/'build-sdl/include/SDL_config.h').read_text()
for macro in ('SDL_VIDEO_DRIVER_MALI','SDL_VIDEO_DRIVER_KMSDRM'):
    assert '#define '+macro+' 1' in config,macro
for macro,name in [('SDL_VIDEO_DRIVER_KMSDRM_DYNAMIC','libdrm.so.2'),('SDL_VIDEO_DRIVER_KMSDRM_DYNAMIC_GBM','libgbm.so.1')]:
    assert '#define '+macro+' "'+name+'"' in config,(macro,name)
closure = {}
pending = list(files)
while pending:
    name = pending.pop()
    if name in closure:
        continue
    assert name in providers, 'unresolved DSO ' + name
    p = providers[name]
    record = elf(p)
    assert all(n in providers for n in record['needed']), (name,record['needed'])
    for dep, required in record['version_needs'].items():
        assert dep in providers, 'missing version provider: ' + dep
        definitions = out('readelf', '-VW', str(providers[dep])).split('Version needs section')[0]
        available_versions = set(re.findall(r'Name: (\S+)', definitions))
        assert set(required) <= available_versions, (name, dep, required, sorted(available_versions))
    deps = set()
    todo = list(record['needed'])
    while todo:
        dep = todo.pop()
        if dep in deps:
            continue
        deps.add(dep)
        todo.extend(elf(providers[dep])['needed'])
    available = symbols(p, True)
    for dep in deps:
        available |= symbols(providers[dep], True)
    # Dynamic undefined strong symbols must match an actual provider, including version.
    unresolved = []
    for line in out('readelf','--dyn-syms','-W',str(p)).splitlines():
        f = line.split()
        if len(f) >= 8 and f[4] == 'GLOBAL' and f[6] == 'UND' and f[7] not in available:
            unresolved.append(f[7])
    assert not unresolved, (name, unresolved)
    versions = sorted(set(re.findall(r'GLIBC_[0-9.]+', out('readelf','-VW',str(p)))))
    assert all(tuple(map(int,v[6:].split('.'))) <= (2,35) for v in versions), (name, versions)
    record['glibc_versions'] = versions
    record['origin'] = 'bundled' if p.is_relative_to(runtime) else 'harvested ABI, historical BaseOS 1.4.0 archive'
    closure[name] = record
    pending.extend(record['needed'])
options={x['name']:x['value'] for x in json.loads((W/'build-mesa/meson-info/intro-buildoptions.json').read_text())}
for key,value in {'gallium-drivers':['panfrost'],'egl-lib-suffix':'','gbm':'enabled','egl':'enabled','gles1':'disabled','gles2':'enabled','llvm':'disabled','tools':[]}.items(): assert options[key]==value,(key,options[key])
commands=(W/'build-mesa/compile_commands.json').read_text()
for source in ('kmsro_drm_winsys.c','renderonly.c','pipe_loader_drm.c','pan_resource.c'):
    assert source in commands,source
# Mesa 26 GBM links libgallium directly; pipe-loader's static kmsro fallback
# accepts sun4i-drm, without old separate *_dri.so modules or aliases.
loader=(F/'source/mesa-26.2.5/src/gallium/auxiliary/pipe-loader/pipe_loader_drm.c').read_text()
assert 'get_driver_descriptor("kmsro")' in loader
ro=(F/'source/mesa-26.2.5/src/gallium/winsys/kmsro/drm/kmsro_drm_winsys.c').read_text()
assert 'panfrost_drm_screen_create_renderonly' in ro and 'panfrost_create_kms_dumb_buffer_for_resource' in ro
renderonly=(F/'source/mesa-26.2.5/src/gallium/auxiliary/renderonly/renderonly.c').read_text()
assert 'drmPrimeHandleToFD' in renderonly and 'drmPrimeFDToHandle' in renderonly
sdl_exports=symbols(files['libSDL2-2.0.so.0'],True)
from elf_exports import exports, compare
baseline_path=ROOT/'work/mainline/frontend-abi/stock-sdl-76de58c8.json'
stock=json.loads(baseline_path.read_text())
assert stock['sha256']=='76de58c80f9a6d901fc5563711b9edc869a759c9446c5a6cd798f4c6774f7577'
assert stock['bytes']==8026720 and stock['elf_machine']==183
assert stock['binary_retained'] is False
assert stock['export_count']==len(stock['exports'])==842
assert stock['soname']==elf(files['libSDL2-2.0.so.0'])['soname'][0]
rebuilt_exports=exports(files['libSDL2-2.0.so.0'])
missing,mismatches=compare(stock['exports'],rebuilt_exports)
assert not missing, ('missing exact stock ABI exports',missing)
assert not mismatches, ('stock ABI export metadata mismatch',mismatches)
baseline={'status':'passed','sha256':stock['sha256'],'bytes':stock['bytes'],
          'soname':stock['soname'],'symbols':stock['export_count'],
          'rebuilt_symbols':len(rebuilt_exports),'missing':missing,'metadata_mismatches':mismatches,
          'superset':True,'exact_metadata_match':True,
          'facts_path':str(baseline_path.relative_to(ROOT)),'facts_sha256':sha(baseline_path),
          'source':stock['source'],'source_path':stock['path'],'serial':stock['serial'],
          'binary_retained':False,'local_ce4b_used_as_baseline':False}
loader_reports={}
env=dict(os.environ,LD_LIBRARY_PATH=str(runtime)+':'+str(F/'harvested'))
for name in ('libEGL.so.1','libGLESv2.so.2','libgbm.so.1','libSDL2-2.0.so.0'):
    loader_reports[name]=subprocess.check_output([str(F/'harvested/ld-linux-aarch64.so.1'),'--list',str(files[name])],env=env,text=True)
actual_packages=dict(line.split('\t',1) for line in out('dpkg-query','-W').splitlines())
for line in (ROOT/'graphics/mesa/container-packages.lock').read_text().splitlines():
    package,version=line.split('=',1);assert actual_packages.get(package)==version,package
report={
 'compiler':out('gcc','--version'),'target':out('gcc','-dumpmachine').strip(),
 'package_lock_sha256':sha(ROOT/'graphics/mesa/container-packages.lock'),
 'source_pin':'d4a7d7503524cc469fe775242f3f925d4dd56c88',
 'source_archive_sha256':'6485a6178a4a1554a30d7f7f68b55c3af4d2a52daea489bb09d3b479626c4d1a',
 'mesa_pins':json.loads((ROOT/'graphics/mesa/pins.json').read_text()),
 'runtime_files':records,'runtime_unique_bytes':sum(x['bytes'] for x in records.values()),
 'runtime_links':{str(p.relative_to(runtime)):str(p.readlink()) for p in runtime.rglob('*') if p.is_symlink()},
 'closure':closure,'loader_reports':loader_reports,'glibc_max':max((v for r in closure.values() for v in r['glibc_versions']),key=lambda s:tuple(map(int,s[6:].split('.')))),
 'runtime_glibc_max':max((v for r in records.values() for names in r['version_needs'].values() for v in names if v.startswith('GLIBC_')),key=lambda s:tuple(map(int,s[6:].split('.')))),
 'sdl_export_count':len(sdl_exports),'baseline':baseline,
 'driver_checks':{'both_sdl_backends':True,'standard_egl_soname':True,'no_unconditional_vendor_symbols':True,'kmsro_renderonly_compiled':True,'dma_buf_export_import_compiled':True,'runtime_pairing_proven':False,'separate_dri_modules_required':False},
 'hardware_qualified':False,'durable_install_eligible':False,'boot_authorized':False,
 'menu_displayed':False,'visible_menu_time_proven':False,
 'limits':['Host build/ELF checks do not establish actual card discovery, render-node pairing, GPU rendering, KMS flips, orientation, menu, input, audio, suspend or timing','Conservative glFinish producer/consumer handoffs serialize GPU; latency unmeasured','Trace JSONL adds file IO overhead when enabled; no framebuffer hashes or menu identity'],
}
(W/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS AArch64 ELF closure/standard EGL/dual SDL; runtime bytes',report['runtime_unique_bytes'],'max',report['glibc_max'],'baseline',baseline)
