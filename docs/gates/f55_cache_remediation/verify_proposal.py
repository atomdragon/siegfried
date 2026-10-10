#!/usr/bin/env python3
"""Temporary-HOME tests only. Never executes REPL, notifications or real cleanup."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO/'tools'))
import check_boot_cache_f55 as c
PROPOSAL = Path(__file__).resolve().parent
MANIFEST = json.loads((PROPOSAL/'cache-inventory.json').read_text())
REAL = Path(MANIFEST['root'])


def snapshot(target, *, caches=False, proposed=False):
    target.mkdir(mode=0o700)
    fd=c.directory(REAL)
    try: _,_,raw=c.walk(fd)
    finally:os.close(fd)
    for rel in MANIFEST['allowed_source_hashes']:
        dest=target/rel;dest.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        data=raw[rel]
        if proposed and rel=='src/siegfried/integrations/boot_briefing.py':
            data=data.replace(b"[sys.executable, '-S',",b"[sys.executable, '-B', '-S',",1)
        if proposed and rel=='src/siegfried/integrations/briefing_kde.py':
            data=data.replace(b"'-e', sys.executable, str(self.cli)",b"'-e', sys.executable, '-B', str(self.cli)",1)
        dest.write_bytes(data);dest.chmod(0o700 if rel in ('scripts/boot_hook.py','bin/siegfried') else 0o600)
    if caches:
        for rel in MANIFEST['cache_files']:
            dest=target/rel;dest.parent.mkdir(mode=0o775,exist_ok=True);dest.parent.chmod(0o775)
            dest.write_bytes(raw[rel]);dest.chmod(0o600)


def env(home):
    return {'HOME':str(home),'SIEGFRIED_HOME':str(home/'runtime'),'XDG_RUNTIME_DIR':str(home/'run'),
            'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}


def run(argv, home):
    p=subprocess.run(argv,env=env(home),capture_output=True,timeout=10,umask=0o002)
    assert p.returncode==0,'temporary process failed'
    return p


def count(root):
    return len(list(root.rglob('*.pyc')))


def worker(root,home):
    code="import sys;sys.path.insert(0,"+repr(str(root/'src'))+");from siegfried.integrations.boot_briefing import bounded_worker;bounded_worker("+repr(str(root))+",['--probe-worker','--socket-path',"+repr(str(home/'missing.sock'))+"],2)"
    run(['/usr/bin/python3','-B','-c',code],home)


def manifest_for(root):
    fd=c.directory(root)
    try:
        st=os.fstat(fd);files,dirs,raw=c.walk(fd)
    finally:os.close(fd)
    m=copy.deepcopy(MANIFEST);m['root']=str(root)
    m['root_identity']=[st.st_dev,st.st_ino,st.st_uid,st.st_gid,st.st_mode & 0o777]
    m['cache_files']={p:files[p] for p in MANIFEST['cache_files']}
    m['cache_directories']={p:dirs[p] for p in MANIFEST['cache_directories']}
    m['source_directories']={p:dirs[p] for p in MANIFEST['source_directories']}
    return m


def reject(root,m):
    try:
        fd,*_=c.validate(root,m);os.close(fd)
    except (OSError,ValueError):return
    raise AssertionError('unsafe cleanup accepted')


def main():
    results={}
    with tempfile.TemporaryDirectory(prefix='siegfried-cache-fixture-') as folder:
        tmp=Path(folder);home=tmp/'home';home.mkdir(mode=0o700)
        root=tmp/'unpatched';snapshot(root)
        run(['/usr/bin/python3',str(root/'scripts/boot_hook.py'),'--dry-run'],home)
        results['unpatched_top_level_recreates_caches']=count(root)>0
        root=tmp/'top_b_only';snapshot(root)
        run(['/usr/bin/python3','-B',str(root/'scripts/boot_hook.py'),'--dry-run'],home)
        assert count(root)==0
        worker(root,home)
        results['parent_B_does_not_protect_unpatched_worker']=count(root)>0
        root=tmp/'patched';snapshot(root,proposed=True)
        run(['/usr/bin/python3','-B',str(root/'scripts/boot_hook.py'),'--dry-run'],home)
        worker(root,home)
        results['patched_top_level_and_actual_probe_worker_no_cache']=count(root)==0
        # Patch the installer only in this temporary repository and install into a temporary HOME.
        (root/'tools').mkdir(mode=0o700)
        code=(REPO/'tools/install_boot_briefing.py').read_text().replace("argv = [sys.executable, str(hook)]","argv = [sys.executable, '-B', str(hook)]",1)
        (root/'tools/install_boot_briefing.py').write_text(code)
        code="import sys;sys.path.insert(0,"+repr(str(root))+");from pathlib import Path;from tools.install_boot_briefing import manage_autostart;manage_autostart(Path("+repr(str(home))+"),dry_run=False,repository=Path("+repr(str(root))+"))"
        run(['/usr/bin/python3','-B','-c',code],home)
        entry=home/'.config/autostart/org.siegfried.BootBriefing.desktop'
        argv=shlex.split(next(x[5:] for x in entry.read_text().splitlines() if x.startswith('Exec=')))
        assert argv[1]=='-B' and 'Hidden=false\n' in entry.read_text()
        p=subprocess.run(['desktop-file-validate',str(entry)],capture_output=True,timeout=5);assert p.returncode==0
        run(argv+['--dry-run'],home)
        installed=home/'.local/share/siegfried-boot';worker(installed,home)
        results['temporary_home_installer_kde_desktop_and_workers']=count(installed)==0
        # Verify the real launcher argv without starting Konsole or REPL.
        code="import sys;sys.path.insert(0,"+repr(str(installed/'src'))+");from unittest.mock import patch;from siegfried.integrations.briefing_kde import REPLLauncher;x=REPLLauncher("+repr(str(installed))+ ");assert '-B' in x.argv;\nwith patch('subprocess.Popen') as p:\n assert x();assert p.call_count==1;assert '-B' in p.call_args.args[0]\n"
        run(['/usr/bin/python3','-B','-c',code],home)
        results['launcher_B_verified_without_executing_repl']=True
        # Exact cleanup of copied caches. Nothing in the real snapshot is modified.
        root=tmp/'cleanup';snapshot(root,caches=True);m=manifest_for(root)
        outside=tmp/'foreign';outside.write_bytes(b'preserve')
        fd,_,_,raw=c.validate(root,m)
        backupdir=tmp/'backup';backupdir.mkdir(mode=0o700)
        try:c.apply(fd,m,raw,backupdir/'cache-backup.json')
        finally:os.close(fd)
        assert count(root)==0 and not list(root.rglob('__pycache__')) and outside.read_bytes()==b'preserve'
        fd=c.directory(root)
        try: files,_,_=c.walk(fd)
        finally:os.close(fd)
        assert all(files[p]['sha256'] in hashes for p,hashes in m['allowed_source_hashes'].items())
        assert json.loads((backupdir/'cache-backup.json').read_text())['bytecode_base64'].keys()==m['cache_files'].keys()
        results['exact_33_file_6_directory_fixture_cleanup_backup_sources_external_preserved']=True
        for case in ('changed_cache','symlink','hardlink','unknown_file','replaced_directory'):
            root=tmp/case;snapshot(root,caches=True);m=manifest_for(root)
            rel=next(iter(m['cache_files']));p=root/rel
            if case=='changed_cache':p.write_bytes(p.read_bytes()+b'x')
            if case=='symlink':p.unlink();p.symlink_to(outside)
            if case=='hardlink':os.link(p,tmp/'additional_hardlink')
            if case=='unknown_file':(root/'unknown.bin').write_bytes(b'preserve')
            if case=='replaced_directory':
                d=p.parent;d.rename(d.with_name('__pycache__old'));d.mkdir(mode=0o775)
            reject(root,m);assert outside.read_bytes()==b'preserve'
            results['reject_'+case]=True
        root=tmp/'active_hook';snapshot(root,caches=True);m=manifest_for(root)
        with patch.object(c,'hooks',side_effect=ValueError('active_boot_hook')):reject(root,m)
        results['reject_active_hook']=True
    assert all(results.values())
    (PROPOSAL/'fixture-results.json').write_text(json.dumps({'status':'PASS','results':results,'scope':'Temporary HOME/packages only; no REPL/notifications or production mutation'},indent=2)+'\n')
    print(json.dumps({'status':'PASS','checks':len(results),'production_changed':False}))


if __name__=='__main__':main()
