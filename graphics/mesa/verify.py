"""Validate real staged AArch64 ELF closure against harvested ABI providers."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path('/src')
os.umask(0o022)
W = ROOT / 'work/mainline/graphics'
(W/'mesa-verification.json').unlink(missing_ok=True)

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

runtime = W / 'runtime/usr/lib'
if runtime.parent.parent.exists():
    shutil.rmtree(runtime.parent.parent)
runtime.mkdir(parents=True)
for p in sorted((W / 'devel/usr/lib').rglob('*')):
    if '.so' not in p.name or 'shim' in p.name or not (p.is_file() or p.is_symlink()):
        continue
    target = runtime / p.relative_to(W / 'devel/usr/lib')
    target.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink():
        target.symlink_to(p.readlink())
    else:
        shutil.copyfile(p, target)
files = {p.name: p for p in runtime.rglob('*') if p.is_file()}
providers = dict(files)
providers.update({p.name: p for p in (W / 'harvested').iterdir()})
assert not any('swrast' in p.name or 'softpipe' in p.name for p in runtime.rglob('*')), 'software rasterizer staged'
records = {p.name: elf(p) for p in runtime.rglob('*') if p.is_file() and not p.is_symlink()}
assert set(records) == {'libdrm.so.2.125.0','libgallium-26.2.5.so','libGLESv1_CM.so.1.1.0','libGLESv2.so.2.0.0','libEGL_mesa.so.1.0.0','libgbm.so.1.0.0','dri_gbm.so'}, 'unexpected runtime DSO inventory'
assert files['libEGL_mesa.so.1'].resolve().name.startswith('libEGL_mesa.so.')
assert elf(files['libEGL_mesa.so.1'])['soname'] == ['libEGL_mesa.so.1']
assert not (runtime / 'libEGL.so.1').exists()
assert elf(files['libEGL_mesa.so.1'])['symbolic_binding'], 'EGL private binding missing'
for lib, names in {'libEGL_mesa.so.1': ['eglGetProcAddress','eglGetDisplay','eglCreateContext','eglCreateWindowSurface','eglCreatePbufferSurface','eglSwapBuffers'], 'libGLESv2.so.2':['glGetString','glDrawArrays','glReadPixels','glDispatchCompute'], 'libgbm.so.1':['gbm_create_device','gbm_surface_create']}.items():
    assert set(names) <= symbols(files[lib], True), lib
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
# Execute loader dependency resolution with harvested providers, never device libraries.
env = dict(os.environ, LD_LIBRARY_PATH=str(runtime)+':'+str(W/'harvested'))
loader = W/'harvested/ld-linux-aarch64.so.1'
loader.chmod(0o755)
loader_reports = {}
for name in ('libEGL_mesa.so.1','libGLESv2.so.2','libgbm.so.1'):
    loader_reports[name] = subprocess.check_output([str(loader),'--list',str(files[name])],env=env,text=True)
query = subprocess.run(['sh', str(ROOT/'graphics/mesa/query.sh')], timeout=30, capture_output=True, text=True)
query_result = {'status':'passed' if query.returncode == 0 else 'unresolved', 'exit_code':query.returncode, 'diagnostics':query.stdout+query.stderr, 'report':(W/'noop-query.txt').read_text() if (W/'noop-query.txt').exists() else '', 'hardware':False, 'rendering_proof':False, 'performance_proof':False}
options = json.loads((W/'build-mesa/meson-info/intro-buildoptions.json').read_text())
settings = {o['name']:o['value'] for o in options}
for key, expected in {'gallium-drivers':['panfrost'],'platforms':[],'vulkan-drivers':[],'llvm':'disabled','glvnd':'disabled','egl-lib-suffix':'_mesa','egl-native-platform':'surfaceless','egl':'enabled','gbm':'enabled','gles2':'enabled','video-codecs':[]}.items():
    assert settings[key] == expected, (key,settings[key],expected)
assert not any('libstdc++' in dep for record in records.values() for dep in record['needed']), 'dynamic C++ runtime leaked'
repro = W/'reproducibility.json'
reproducibility = json.loads(repro.read_text()) if repro.exists() else {'status':'pending'}
if reproducibility.get('runtime_sha256') != {n:r['sha256'] for n,r in records.items()}:
    reproducibility = {'status':'not established for current outputs'}
report = {
    'reproducibility':reproducibility,
    'noop_query':query_result,
    'pan_gpu_id':{'input':'7093','parser_base':16,'parsed_hex':'0x7093','normalized_product':'PAN_PROD_ID(7,0,3)','model':'G31','source':['src/panfrost/drm-shim/panfrost_noop.c:23','src/panfrost/model/pan_model.h:25','src/panfrost/model/pan_model.c:79']},
    'recipe_sha256':{str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'graphics/mesa').iterdir()) if p.is_file()},
    'static_libstdcpp_sha256':sha(Path(out('g++','-print-file-name=libstdc++.a').strip())),
    'builder_sha256':sha(ROOT/'tools/build-h700-mesa.py'),
    'harvested_abi_source':json.loads((W/'harvested-abi.json').read_text()),
    'scope':'host-only Linux AArch64 build and harvested ABI validation',
    'hardware':False,'joined':False,'deploy':False,'durable':False,
    'pins':json.loads((ROOT/'graphics/mesa/pins.json').read_text()),
    'command':'python3 tools/build-h700-mesa.py',
    'build_os':out('cat','/etc/os-release'), 'target':out('gcc','-dumpmachine').strip(),
    'meson_compilers':json.loads((W/'build-mesa/meson-info/intro-compilers.json').read_text()),
    'compiler':out('gcc','--version'), 'linker':out('ld','--version'),
    'container_packages_sha256':sha(W/'container-packages.tsv'),
    'python_packages':(W/'python-packages.txt').read_text(),
    'runtime_files':records,'runtime_unique_bytes':sum(r['bytes'] for r in records.values()),
    'closure':closure,'harvested_loader_resolution':loader_reports,
    'symbol_closure_passed':True,'abi_passed':True,
    'meson_options':{k:settings[k] for k in ('auto_features','buildtype','strip','gallium-drivers','vulkan-drivers','platforms','egl','egl-lib-suffix','egl-native-platform','gbm','gles1','gles2','opengl','glx','glvnd','llvm','mesa-clc','video-codecs','gallium-va','gallium-rusticl','xmlconfig','expat','zlib','zstd','shader-cache','tools','cpp_link_args')},
    'limits':['Harvested archive ABI, not fresh live-device evidence','No rendering, hardware, timing, performance or frontend compatibility proof'],
}
(W/'mesa-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS: runtime ELF closure and harvested ABI; bytes',report['runtime_unique_bytes'])
