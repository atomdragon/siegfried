#!/usr/bin/env python3
"""Explicit live gate audit. Temporarily loads scripts and opens two test dialogs.

Run manually with session access. Never suspends, installs or writes user config.
All telemetry uses a temporary Vault. Restores original focus when still present.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from siegfried.daemon.focus import FocusDBusAdapter, FocusTracker
from siegfried.storage.vault import Vault


def main():
    import dbus
    import dbus.service
    os.environ['SIEGFRIED_ENABLE_KWIN'] = '1'
    processes, loaded, results = [], [], {}
    token = 'siegfried_f511_' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=token) as temp:
        base = Path(temp)
        vault = Vault(base / 'vault.jsonl')
        tracker = FocusTracker(vault)
        adapter = FocusDBusAdapter(tracker)
        original = []
        client = None
        try:
            assert adapter.start(), 'adapter startup failed'
            client = dbus.SessionBus(private=True)
            scripting = dbus.Interface(client.get_object('org.kde.KWin', '/Scripting'),
                                       'org.kde.kwin.Scripting')

            class Audit(dbus.service.Object):
                @dbus.service.method('org.siegfried.GateAudit', in_signature='s',
                                     out_signature='', sender_keyword='sender')
                def Remember(self, value, sender=None):
                    if str(sender) == adapter._kwin_owner:
                        original.append(str(value))
            audit = Audit(adapter._bus, '/GateAudit')

            def load(path, suffix):
                name = token + suffix + uuid.uuid4().hex[:8]
                sid = int(scripting.loadScript(str(path), name, signature="ss"))
                assert sid >= 0, 'script load failed'
                loaded.append(name)
                script = dbus.Interface(client.get_object('org.kde.KWin', f'/Scripting/Script{sid}'),
                                        'org.kde.kwin.Script')
                script.run()
                return name

            def snippet(code):
                path = base / (uuid.uuid4().hex + '.js')
                path.write_text(code)
                name = load(path, path.stem)
                time.sleep(0.15)
                assert scripting.unloadScript(name)
                loaded.remove(name)

            def wait_app(app):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if tracker.snapshot()['active_app'] == app:
                        return
                    time.sleep(.02)
                raise AssertionError('expected focus not observed: ' + str(app) + ' actual=' + str(tracker.snapshot()) + ' last=' + str(adapter._last_app) + ' running=' + str(adapter.is_running()))

            def activate(process, app):
                snippet('var ws = workspace.windowList(); for (var i=0;i<ws.length;i++) {'
                        f'if (ws[i].pid === {process.pid}) {{ ws[i].minimized=false; '
                        'workspace.activeWindow=ws[i]; break; }}')
                wait_app(app)

            snippet('if (workspace.activeWindow) callDBus("org.siegfried.FocusWatcher", '
                    '"/GateAudit", "org.siegfried.GateAudit", "Remember", '
                    'String(workspace.activeWindow.internalId));')
            assert original, 'original focus not captured'
            watcher = load(ROOT / 'scripts/kwin_focus_watcher.js', '_watcher')
            for args in (["kdialog", "--title", "Siegfried F5.1.1 A", "--msgbox", "Temporary audit A"],
                         ["zenity", "--info", "--title=Siegfried F5.1.1 B", "--text=Temporary audit B"]):
                processes.append(subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                                   stderr=subprocess.DEVNULL))
                time.sleep(.8)
                assert processes[-1].poll() is None, 'test application exited'
            a, b = processes
            activate(a, 'kdialog')
            assert adapter.stop(discard=True)
            assert adapter.start()  # Start measured phase without startup intervals.
            assert scripting.unloadScript(watcher)
            loaded.remove(watcher)
            watcher = load(ROOT / 'scripts/kwin_focus_watcher.js', '_watcher')
            wait_app('kdialog')
            start = time.monotonic()
            time.sleep(.7)
            count = len(list(vault.read_events()))
            snippet('for (var i=0;i<8;i++) callDBus("org.siegfried.FocusWatcher", '
                    '"/FocusWatcher", "org.siegfried.FocusWatcher", "WindowChanged", "kdialog");')
            assert len(list(vault.read_events())) == count
            results['duplicates_trusted_dbus'] = 'PASS (induced from KWin)'
            activate(b, 'zenity')
            switch = time.monotonic()
            time.sleep(.8)
            activate(a, 'kdialog')
            end = time.monotonic()
            events = list(vault.read_events())[-2:]
            assert [e.data['aplicacion'] for e in events] == ['kdialog', 'zenity']
            expected = [switch - start, end - switch]
            measured = [e.data['duracion'] for e in events]
            assert all(abs(x-y) < .4 for x, y in zip(expected, measured))
            results['two_app_durations'] = {'measured_seconds': measured,
                                           'wall_monotonic_reference': expected,
                                           'tolerance_seconds': .4}
            snippet('workspace.activeWindow = null;')
            wait_app(None)
            results['null_focus'] = 'PASS (KWin activeWindow=null)'
            # Another same-UID connection must not be authorized as KWin.
            proxy = dbus.Interface(client.get_object(adapter.DBUS_SERVICE_NAME, '/FocusWatcher'),
                                   adapter.DBUS_INTERFACE)
            assert not proxy.WindowChanged('firefox')
            assert not proxy.WindowChanged('a' * 4096)
            try:
                assert not proxy.WindowChanged(dbus.Int32(12), signature='i')
            except dbus.DBusException:
                pass
            results['same_uid_spoof_and_bad_signature'] = 'PASS'
            assert adapter.stop(discard=True)
            assert not client.name_has_owner(adapter.DBUS_SERVICE_NAME)
            activate_without_wait = ('var ws=workspace.windowList(); for(var i=0;i<ws.length;i++) '
                                     f'if(ws[i].pid==={b.pid}) workspace.activeWindow=ws[i];')
            snippet(activate_without_wait)
            time.sleep(.3)
            assert adapter.start()
            # Recovery explicitly reloads watcher to sample unchanged active app.
            assert scripting.unloadScript(watcher)
            loaded.remove(watcher)
            watcher = load(ROOT / 'scripts/kwin_focus_watcher.js', '_watcher')
            wait_app('zenity')
            results['disconnect_restart_reload'] = 'PASS'
            assert scripting.unloadScript(watcher)
            loaded.remove(watcher)
            assert not scripting.isScriptLoaded(watcher)
            before = tracker.snapshot()['active_app']
            snippet('var ws=workspace.windowList(); for(var i=0;i<ws.length;i++) '
                    f'if(ws[i].pid==={a.pid}) workspace.activeWindow=ws[i];')
            assert tracker.snapshot()['active_app'] == before
            tracker.set_active(False)
            count = len(list(vault.read_events()))
            tracker.on_window_changed('firefox')
            assert len(list(vault.read_events())) == count
            assert adapter.stop()
            assert not client.name_has_owner(adapter.DBUS_SERVICE_NAME)
            assert not any(t and t.is_alive() for t in (adapter._thread, adapter._worker))
            results['disable_unload_resource_cleanup'] = 'PASS'
            for event in vault.read_events():
                assert set(event.data) == {'aplicacion', 'categoria', 'duracion'}
                assert all(secret not in json.dumps(event.to_dict()) for secret in
                           ('Temporary audit', 'Siegfried F5.1.1', str(base), 'https://'))
            results['persisted_privacy'] = 'PASS'
            results['suspend_real'] = 'PENDING; no suspend requested or performed'
        finally:
            if adapter.is_running() or adapter._bus is not None:
                adapter.stop(discard=True)
            if client is not None:
                if original:
                    try:
                        snippet('var ws=workspace.windowList(); for(var i=0;i<ws.length;i++) '
                                'if(String(ws[i].internalId)===' + json.dumps(original[0]) +
                                ') workspace.activeWindow=ws[i];')
                    except Exception:
                        results['restore_focus'] = 'FAILED'
                    else:
                        results['restore_focus'] = 'attempted by opaque window id'
                for name in reversed(loaded):
                    scripting.unloadScript(name)
                if 'scripting' in locals():
                    assert all(not scripting.isScriptLoaded(name) for name in loaded)
                client.close()
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            results['test_process_cleanup'] = all(p.poll() is not None for p in processes)
            print(json.dumps(results, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
