"""F5.3 hermetic tests: temporary HOME/runtime, fixtures, no host desktop/network."""
from dataclasses import replace
from datetime import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from siegfried.core.briefing import BriefingContext, compose_briefing, context_from_session
from siegfried.core.rest import RestEstimate, RestStatus, ClockSample, estimate_rest
from siegfried.daemon.focus import FocusTracker
from siegfried.daemon.session import SessionController
from siegfried.integrations.briefing_files import BriefingLease, read_critical_task
from siegfried.integrations.briefing_kde import KDEBriefingPresenter, REPLLauncher, fallback_notification
from siegfried.integrations.boot_briefing import run_briefing, bounded_worker
from siegfried.integrations.weather import fetch_weather, parse_weather, NoRedirect, PublicHTTPSConnection, WEATHER_URL
from siegfried.storage.paths import SiegfriedPaths
from siegfried.storage.initialization import ensure_user_runtime

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('boot_installer', REPO/'tools/install_boot_briefing.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def context(hour=8, **kwargs):
    return BriefingContext(now=datetime(2026,10,9,hour), **kwargs)


def rest():
    return estimate_rest(ClockSample(1700000000,10,100,'a'), ClockSample(1700028800,11,28900,'a'),
                         cause='suspend', continuous_evidence=True)


class TestAComposition(unittest.TestCase):
    def test_morning(self): self.assertIn('Buenos días, Señor.', compose_briefing(context()))
    def test_afternoon(self): self.assertIn('Buenas tardes', compose_briefing(context(13)))
    def test_night(self): self.assertIn('Buenas noches', compose_briefing(context(23)))
    def test_empty_context(self): self.assertIn('no está confirmado', compose_briefing(context()))
    def test_ready(self): self.assertIn('está preparado', compose_briefing(context(available=True)))
    def test_allowed_title(self): self.assertIn('Joven', compose_briefing(context(title='Joven')))
    def test_malformed_title(self): self.assertNotIn('PRIVATE', compose_briefing(context(title='PRIVATE')))
    def test_authorized_task(self):
        self.assertIn('Diseño seguro', compose_briefing(context(critical_task='Diseño seguro', expose_task=True, unlocked=True)))
    def test_task_denied_by_default(self): self.assertNotIn('PRIVATE', compose_briefing(context(critical_task='PRIVATE', unlocked=True)))
    def test_locked_task_omitted(self): self.assertNotIn('PRIVATE', compose_briefing(context(critical_task='PRIVATE', expose_task=True)))
    def test_malformed_task(self):
        for task in ['https://private', 'Bearer abc', 'sk-secret', 'x\ny', '<b>x</b>', '$(touch x)', 'x; rm', 'x'*101, {'title':'PRIVATE'}]:
            self.assertNotIn('Tarea prioritaria', compose_briefing(context(critical_task=task, expose_task=True, unlocked=True)))
    def test_invalid_source(self):
        with self.assertRaises(ValueError): compose_briefing(None)
        with self.assertRaises(ValueError): compose_briefing(BriefingContext(now='private'))
    def test_invalid_rest_object_is_omitted(self):
        self.assertNotIn('descanso', compose_briefing(context(rest={'private':8}, unlocked=True)))
    def test_no_llm_or_network_during_composition(self):
        with patch('urllib.request.urlopen') as net, patch('subprocess.Popen') as proc:
            compose_briefing(context())
            net.assert_not_called(); proc.assert_not_called()


class TestBRest(unittest.TestCase):
    def test_estimated(self): self.assertIn('7 h 35 min', compose_briefing(context(rest=rest(), unlocked=True)))
    def test_insufficient(self): self.assertNotIn('descanso', compose_briefing(context(rest=RestEstimate(RestStatus.INSUFFICIENT_DATA), unlocked=True)))
    def test_not_applicable(self): self.assertNotIn('descanso', compose_briefing(context(rest=RestEstimate(RestStatus.NOT_APPLICABLE), unlocked=True)))
    def test_invalid(self): self.assertNotIn('descanso', compose_briefing(context(rest=RestEstimate(RestStatus.INVALID_INTERVAL), unlocked=True)))
    def test_locked_rest_omitted(self): self.assertNotIn('descanso', compose_briefing(context(rest=rest())))
    def test_restart_does_not_invent(self):
        controller = SessionController(FocusTracker())
        result = context_from_session(controller, now=datetime.now())
        self.assertEqual(result.rest.status, RestStatus.INSUFFICIENT_DATA)
        self.assertNotIn('descanso', compose_briefing(result))
    def test_internal_session_estimate_reused(self):
        controller=SessionController(FocusTracker())
        controller.begin(ClockSample(1700000000,10,100,'a'))
        controller.initialize(dict(suspended=False,shutting_down=False,kde_locked=False,locked_hint=False,active=True), ClockSample(1700000000,10,100,'a'))
        controller.rest_estimate = rest()
        self.assertIn('7 h 35 min', compose_briefing(context_from_session(controller, now=datetime.now())))
    def test_forged_estimated_numbers(self):
        for elapsed,minutes in [(28800,float('nan')),(float('inf'),455),(5400,999),(True,455),(10**10000,455)]:
            self.assertNotIn('descanso', compose_briefing(context(rest=RestEstimate(RestStatus.ESTIMATED,elapsed,minutes),unlocked=True)))
    def test_no_confirmed_sleep_claim(self): self.assertNotIn('Dormiste', compose_briefing(context(rest=rest(),unlocked=True)))


class TestCWeather(unittest.TestCase):
    def response(self, raw=b'+22\xc2\xb0C Partly Cloudy', **kwargs):
        response=MagicMock(); response.__enter__.return_value=response
        response.status=kwargs.get('status',200); response.geturl.return_value=kwargs.get('url',WEATHER_URL)
        response.headers={'Content-Type':kwargs.get('type','text/plain; charset=utf-8')};response.read.return_value=raw
        opener=MagicMock();opener.open.return_value=response
        return opener,response
    def test_valid(self):
        opener,response=self.response();self.assertIn('22',fetch_weather(opener))
        self.assertEqual(opener.open.call_args.kwargs['timeout'],.8);response.read.assert_called_once_with(513)
    def test_timeout(self):
        opener=MagicMock();opener.open.side_effect=TimeoutError('PRIVATE');self.assertIsNone(fetch_weather(opener))
    def test_tls_error(self):
        opener=MagicMock();opener.open.side_effect=ssl.SSLError('PRIVATE');self.assertIsNone(fetch_weather(opener))
    def test_http_errors(self):
        for status in (403,404,500,503):
            opener,_=self.response(status=status);self.assertIsNone(fetch_weather(opener))
    def test_urllib_error(self):
        opener=MagicMock();opener.open.side_effect=urllib.error.URLError('PRIVATE');self.assertIsNone(fetch_weather(opener))
    def test_bad_utf8(self): self.assertIsNone(parse_weather(b'\xff\xfe'))
    def test_oversized(self): self.assertIsNone(parse_weather(b'x'*513))
    def test_malformed(self):
        for value in [b'<html>22 C</html>',b'PRIVATE',b'\x1b[31m+22\xc2\xb0C',b'http://private +22\xc2\xb0C']:
            self.assertIsNone(parse_weather(value))
    def test_wrong_content_type(self): self.assertIsNone(parse_weather(b'+22\xc2\xb0C','text/html'))
    def test_wrong_declared_charset(self): self.assertIsNone(parse_weather(b'+22\xc2\xb0C','text/plain; charset=latin-1'))
    def test_final_destination_changed(self):
        opener,_=self.response(url='http://127.0.0.1');self.assertIsNone(fetch_weather(opener))
    def test_redirects_rejected(self):
        for url in ['https://wttr.in/Lima','http://wttr.in','https://127.0.0.1','https://evil.invalid']:
            with self.assertRaises(ValueError): NoRedirect().redirect_request(None,None,None,None,None,url)
    def test_private_dns_rejected(self):
        for address in ['127.0.0.1','10.0.0.1','169.254.169.254','::1']:
            with patch('socket.getaddrinfo', return_value=[(socket.AF_INET,socket.SOCK_STREAM,0,'',(address,443))]), patch('socket.socket') as sock:
                with self.assertRaises(ValueError): PublicHTTPSConnection('wttr.in').connect()
                sock.assert_not_called()
    def test_destination_not_configurable(self):
        with self.assertRaises(ValueError): PublicHTTPSConnection('localhost').connect()
    def test_offline(self):
        opener=MagicMock();opener.open.side_effect=OSError('PRIVATE');self.assertIsNone(fetch_weather(opener))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        self.run=self.home/'run';self.run.mkdir(mode=0o700)
        self.paths=SiegfriedPaths(base_dir=self.home/'base',runtime_dir=self.run)
    def tearDown(self): self.temp.cleanup()
    def presenter(self, native=True, actions=True):
        p=MagicMock();p.start.return_value=native;p.send.return_value=True
        p.unlocked=True;p.actions=actions;p.inhibited=False
        p.notification_id=123;p.opened=False;p.disconnected=False
        return p
    def hook(self,p=None,**kwargs):
        window = kwargs.pop('action_window', .01)
        with patch('siegfried.integrations.boot_briefing.session_key',return_value='a'*64), patch('siegfried.integrations.boot_briefing.bounded_worker',return_value=b''):
            return run_briefing(self.paths,REPO,presenter=p or self.presenter(),action_window=window,**kwargs)


class TestDKDE(Fixture):
    def native(self):
        p=KDEBriefingPresenter(MagicMock());p.dbus=MagicMock();p.dbus.UInt32=int;p.dbus.Boolean=bool
        p.owner=':1.5';p.saver_owner=':1.6';p.notification_id=123;p.bus=MagicMock();p.loop=MagicMock();p.actions=True
        p._call=MagicMock(return_value=123)
        return p
    def test_send(self):
        p=self.native()
        with patch.object(p,'refresh_unlocked',side_effect=lambda:setattr(p,'unlocked',True)):
            self.assertTrue(p.send('Hello <safe>','generic'))
        args=p._call.call_args.args[-1];self.assertIn('&lt;safe&gt;',args[4]);self.assertEqual(args[5],['open','Abrir sesión'])
    def test_action_launch_once(self):
        p=self.native()
        with patch.object(p,'refresh_unlocked',return_value=True):
            p._action(123,'open',':1.5');p._action(123,'open',':1.5')
        p.on_open.assert_called_once();p.loop.quit.assert_called()
    def test_no_automatic_open(self):
        p=self.presenter();self.hook(p);p.on_open.assert_not_called()
    def test_false_sender_and_wrong_id(self):
        p=self.native();p._action(123,'open',':1.99');p._action(124,'open',':1.5');p._action(123,'evil',':1.5')
        p.on_open.assert_not_called()
    def test_locked_action_denied(self):
        p=self.native()
        with patch.object(p,'refresh_unlocked',return_value=False):p._action(123,'open',':1.5')
        p.on_open.assert_not_called()
    def test_lock_retracts_private_text(self):
        p=self.native();p.fallback='generic'
        with patch.object(p,'send') as send:p._lock_changed(True,':1.6')
        send.assert_called_once_with('generic','generic',expiry_ms=12000)
    def test_name_loss_prevents_action(self):
        p=self.native();p._owner_changed('org.freedesktop.Notifications',':1.5',':1.9');p._action(123,'open',':1.5')
        p.on_open.assert_not_called();self.assertTrue(p.disconnected)
    def test_without_actions_does_not_fake_button(self):
        p=self.presenter(actions=False);self.hook(p);p.wait.assert_not_called();p.close.assert_called_once()
    def test_missing_dbus(self):
        with patch.dict(os.environ,{'DBUS_SESSION_BUS_ADDRESS':'unix:path=/test'}),patch.dict(sys.modules,{'dbus':None}):
            self.assertFalse(KDEBriefingPresenter().start())
    def test_missing_gi(self):
        with patch.dict(os.environ,{'DBUS_SESSION_BUS_ADDRESS':'unix:path=/test'}),patch.dict(sys.modules,{'gi':None}):
            self.assertFalse(KDEBriefingPresenter().start())
    def test_headless(self):
        with patch.dict(os.environ,{'DBUS_SESSION_BUS_ADDRESS':''}):self.assertFalse(KDEBriefingPresenter().start())
    def test_fallback(self):
        with patch('siegfried.integrations.briefing_kde.DesktopNotificationSender') as cls,patch('builtins.print') as out:
            cls.return_value.send.return_value=False;self.assertFalse(fallback_notification('safe'));out.assert_called_once()
    def test_duplicates(self):
        p=self.presenter();self.hook(p);result=self.hook(p);self.assertTrue(result['duplicate']);self.assertEqual(p.send.call_count,1)
    def test_close_resources(self):
        p=self.native();match=MagicMock();p.matches=[match];p.close();match.remove.assert_called_once();self.assertIsNone(p.bus)
    def test_launcher_fixed_argv(self):
        launcher=REPLLauncher(REPO)
        with patch.object(launcher,'available',return_value=True),patch('subprocess.Popen') as child:
            self.assertTrue(launcher('safe-token'))
        self.assertEqual(child.call_args.args[0],[launcher.konsole,'--separate','-e',sys.executable,'-B',str(REPO/'bin/siegfried')]);self.assertNotIn('shell',child.call_args.kwargs)
    def test_untrusted_launcher_refused(self):
        launcher=REPLLauncher(REPO)
        with patch.object(launcher,'available',return_value=False),patch('subprocess.Popen') as child:
            self.assertFalse(launcher());child.assert_not_called()


class TestEInstall(Fixture):
    def test_desktop_valid(self):
        content=installer.desktop_content(self.home/'future',planned=True)
        file=self.home/'entry.desktop';file.write_text(content)
        result=subprocess.run(['desktop-file-validate',str(file)],capture_output=True,timeout=2)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('OnlyShowIn=KDE;',content);self.assertIn('Hidden=false',content)
    def test_install_idempotent(self):
        first=installer.manage_autostart(self.home,dry_run=False)
        second=installer.manage_autostart(self.home,dry_run=False)
        self.assertTrue(first['installed']);self.assertTrue(second['unchanged'])
        self.assertEqual((self.home/'.config/autostart'/installer.ENTRY).stat().st_mode&0o777,0o600)
    def test_disable_enable(self):
        installer.manage_autostart(self.home,dry_run=False)
        installer.manage_autostart(self.home,operation='disable',dry_run=False)
        entry=self.home/'.config/autostart'/installer.ENTRY;self.assertIn('Hidden=true',entry.read_text())
        installer.manage_autostart(self.home,operation='enable',dry_run=False);self.assertIn('Hidden=false',entry.read_text())
    def test_uninstall(self):
        installer.manage_autostart(self.home,dry_run=False)
        installer.manage_autostart(self.home,operation='uninstall',dry_run=False)
        self.assertFalse((self.home/'.config/autostart'/installer.ENTRY).exists());self.assertFalse((self.home/'.local/share/siegfried-boot').exists())
    def test_uninstall_missing_no_writes(self):
        installer.manage_autostart(self.home,operation='uninstall',dry_run=False);self.assertEqual(list(self.home.iterdir()),[self.run])
    def test_dry_run_no_writes(self):
        installer.manage_autostart(self.home,dry_run=True);self.assertEqual(list(self.home.iterdir()),[self.run])
    def test_parent_symlink(self):
        (self.home/'.config').symlink_to(self.run)
        with self.assertRaises((OSError,ValueError)):installer.manage_autostart(self.home,dry_run=False)
    def test_foreign_entry_preserved(self):
        folder=self.home/'.config/autostart';folder.mkdir(parents=True,mode=0o700)
        file=folder/installer.ENTRY;file.write_text('PRIVATE');file.chmod(0o600)
        with self.assertRaises(ValueError):installer.manage_autostart(self.home,dry_run=False)
        self.assertEqual(file.read_text(),'PRIVATE')
    def test_entry_symlink_rejected(self):
        folder=self.home/'.config/autostart';folder.mkdir(parents=True,mode=0o700);(folder/installer.ENTRY).symlink_to(self.home/'target')
        with self.assertRaises((OSError,ValueError)):installer.manage_autostart(self.home,dry_run=False)
    def test_unsafe_home(self):
        with self.assertRaises(ValueError):installer.manage_autostart(Path('relative'))
        self.home.chmod(0o777)
        try:
            with self.assertRaises(ValueError):installer.manage_autostart(self.home)
        finally:self.home.chmod(0o700)
    def test_uninstall_preserves_foreign_package_file(self):
        installer.manage_autostart(self.home,dry_run=False)
        extra=self.home/'.local/share/siegfried-boot/PRIVATE';extra.write_text('keep');extra.chmod(0o600)
        with self.assertRaises(ValueError):installer.manage_autostart(self.home,operation='uninstall',dry_run=False)
        self.assertEqual(extra.read_text(),'keep')
    def test_conflicting_options_preserved(self):
        installer.manage_autostart(self.home,dry_run=False)
        with self.assertRaises(ValueError):installer.manage_autostart(self.home,dry_run=False,weather=True)
    def test_snapshot_executable(self):
        installer.manage_autostart(self.home,dry_run=False)
        package=self.home/'.local/share/siegfried-boot'
        self.assertTrue(REPLLauncher(package).available())
        result=subprocess.run([sys.executable,'-S',str(package/'scripts/boot_hook.py'),'--dry-run'],capture_output=True,timeout=2)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_exec_quoting(self):
        quoted=installer.exec_quote('a b%$`"\\c');self.assertTrue(quoted.startswith('"'));self.assertIn('%%',quoted)
        with self.assertRaises(ValueError):installer.exec_quote('x\ny')


class TestFConcurrency(Fixture):
    def test_concurrent_lease(self):
        with BriefingLease(self.paths,'a'*64):
            with BriefingLease(self.paths,'a'*64) as second:self.assertTrue(second.duplicate)
    def test_concurrent_hooks_send_once_without_deadlock(self):
        entered,release=threading.Event(),threading.Event();results=[]
        p=self.presenter()
        def start():
            entered.set();release.wait(1);return True
        p.start.side_effect=start
        def run():results.append(run_briefing(self.paths,REPO,presenter=p,action_window=.01))
        with patch('siegfried.integrations.boot_briefing.session_key',return_value='a'*64),patch('siegfried.integrations.boot_briefing.bounded_worker',return_value=b''):
            first=threading.Thread(target=run);second=threading.Thread(target=run)
            first.start()
            try:
                self.assertTrue(entered.wait(1));second.start();second.join(1)
                self.assertFalse(second.is_alive())
            finally:release.set();first.join(1)
        self.assertFalse(first.is_alive());self.assertEqual(p.send.call_count,1)
        self.assertEqual(sum(bool(r.get('duplicate')) for r in results),1)
    def test_restart_same_session(self):
        with BriefingLease(self.paths,'a'*64) as lease:lease.mark_sent()
        with BriefingLease(self.paths,'a'*64) as lease:self.assertTrue(lease.duplicate)
        with BriefingLease(self.paths,'b'*64) as lease:self.assertFalse(lease.duplicate)
    def test_marker_permissions(self):
        with BriefingLease(self.paths,'a'*64) as lease:lease.mark_sent()
        self.assertEqual(self.paths.briefing_marker_file.stat().st_mode&0o777,0o600)
    def test_marker_symlink(self):
        self.paths.briefing_marker_file.symlink_to(self.home/'private')
        with self.assertRaises(OSError):
            with BriefingLease(self.paths,'a'*64):pass
    def test_marker_hardlink(self):
        private=self.home/'private';private.write_text('PRIVATE');private.chmod(0o600)
        os.link(private,self.paths.briefing_marker_file)
        with self.assertRaises(ValueError):
            with BriefingLease(self.paths,'a'*64):pass
        self.assertEqual(private.read_text(),'PRIVATE')
    def test_malformed_marker_not_overwritten(self):
        self.paths.briefing_marker_file.write_text('PRIVATE');self.paths.briefing_marker_file.chmod(0o600)
        result=self.hook();self.assertTrue(result['runtime_unavailable']);self.assertEqual(self.paths.briefing_marker_file.read_text(),'PRIVATE')
    def test_daemon_offline_budget(self):
        t=time.monotonic();out=bounded_worker(REPO,['--probe-worker','--socket-path',str(self.run/'absent')],.15)
        self.assertEqual(out,b'');self.assertLess(time.monotonic()-t,.4)
    def test_weather_slow_after_notification(self):
        p=self.presenter();order=[]
        p.send.side_effect=lambda *a,**k:order.append('send') or True
        def worker(repo,args,timeout):
            order.append('weather' if '--weather-worker' in args else 'probe');return b''
        with patch('siegfried.integrations.boot_briefing.session_key',return_value='a'*64),patch('siegfried.integrations.boot_briefing.bounded_worker',side_effect=worker):
            run_briefing(self.paths,REPO,presenter=p,weather=True,action_window=.01)
        self.assertLess(order.index('send'),order.index('weather'));p.close.assert_called_once()
    def test_weather_replaces_notification(self):
        p=self.presenter()
        with patch('siegfried.integrations.boot_briefing.session_key',return_value='a'*64),patch('siegfried.integrations.boot_briefing.bounded_worker',side_effect=[b'',b'+22\xc2\xb0C Sunny']):
            run_briefing(self.paths,REPO,presenter=p,weather=True,action_window=.01)
        self.assertEqual(p.send.call_count,2)
    def test_weather_does_not_reopen_closed_notification(self):
        p=self.presenter();p.dispatch_pending.side_effect=lambda:setattr(p,'notification_id',None)
        with patch('siegfried.integrations.boot_briefing.session_key',return_value='a'*64),patch('siegfried.integrations.boot_briefing.bounded_worker',side_effect=[b'',b'+22\xc2\xb0C Sunny']):
            run_briefing(self.paths,REPO,presenter=p,weather=True,action_window=.01)
        self.assertEqual(p.send.call_count,1)
    def test_weather_does_not_reopen_expired_notification(self):
        p=self.presenter()
        def worker(repo,args,timeout):
            if '--weather-worker' in args:
                time.sleep(.02);return b'+22\xc2\xb0C Sunny'
            return b''
        with patch('siegfried.integrations.boot_briefing.session_key',return_value='a'*64),patch('siegfried.integrations.boot_briefing.bounded_worker',side_effect=worker):
            run_briefing(self.paths,REPO,presenter=p,weather=True,action_window=.01)
        self.assertEqual(p.send.call_count,1)
    def test_worker_timeout_reaps_child(self):
        child=MagicMock();child.communicate.side_effect=[subprocess.TimeoutExpired('safe',.01),(b'',b'')];child.poll.return_value=None
        with patch('subprocess.Popen',return_value=child):self.assertEqual(bounded_worker(REPO,[],.01),b'')
        child.terminate.assert_called_once();child.stdout.close.assert_called_once()
    def test_real_slow_worker_deadline_and_cleanup(self):
        repo=self.home/'worker';(repo/'scripts').mkdir(parents=True)
        (repo/'scripts/boot_hook.py').write_text('import time\ntime.sleep(5)\n')
        original=subprocess.Popen;children=[]
        def spawn(*args,**kwargs):
            child=original(*args,**kwargs);children.append(child);return child
        start=time.monotonic()
        with patch('siegfried.integrations.boot_briefing.subprocess.Popen',side_effect=spawn):
            self.assertEqual(bounded_worker(repo,[],.04),b'')
        self.assertLess(time.monotonic()-start,.4)
        self.assertIsNotNone(children[0].poll());self.assertTrue(children[0].stdout.closed)
    def test_worker_kill_reaps_child(self):
        child=MagicMock();child.communicate.side_effect=[subprocess.TimeoutExpired('safe',.01),subprocess.TimeoutExpired('safe',.1),(b'',b'')];child.poll.return_value=None
        with patch('subprocess.Popen',return_value=child):bounded_worker(REPO,[],.01)
        child.kill.assert_called_once();self.assertEqual(child.communicate.call_count,3)
    def test_runtime_insecure_no_desktop(self):
        self.run.chmod(0o755)
        try:
            p=self.presenter();result=self.hook(p);self.assertTrue(result['runtime_unavailable']);p.start.assert_not_called()
        finally:self.run.chmod(0o700)
    def test_private_task_secure_read(self):
        ensure_user_runtime(self.paths)
        self.paths.active_agenda_file.write_text(json.dumps({'v':1,'critical_task':{'title':'safe'},'secondary_tasks':[],'backlog':[]}))
        self.assertEqual(read_critical_task(self.paths),'safe')
    def test_private_task_symlink(self):
        ensure_user_runtime(self.paths)
        self.paths.active_agenda_file.unlink();self.paths.active_agenda_file.symlink_to(self.home/'secret')
        self.assertIsNone(read_critical_task(self.paths))
        self.paths.active_agenda_file.unlink()
        self.paths.active_agenda_file.write_text('['*2000 + ']'*2000)
        self.paths.active_agenda_file.chmod(0o600)
        self.assertIsNone(read_critical_task(self.paths))
    def test_no_weather_by_default(self):
        p=self.presenter()
        with patch('siegfried.integrations.boot_briefing.session_key',return_value='a'*64),patch('siegfried.integrations.boot_briefing.bounded_worker',return_value=b'') as worker:
            run_briefing(self.paths,REPO,presenter=p,action_window=.01)
        self.assertEqual(worker.call_count,1);self.assertIn('--probe-worker',worker.call_args.args[1])
    def test_dry_run_no_native_or_workers(self):
        p=self.presenter();self.hook(p,dry_run=True);p.start.assert_not_called();self.assertFalse(self.paths.briefing_marker_file.exists())
    def test_clean_exception_shutdown(self):
        p=self.presenter();p.send.side_effect=OSError('PRIVATE')
        with patch('builtins.print') as output:
            self.hook(p)
        p.close.assert_called_once();self.assertNotIn('PRIVATE',str(output.call_args))
    def test_invalid_action_window(self):
        for value in [float('inf'),float('nan'),0,31]:
            with self.assertRaises(ValueError):self.hook(action_window=value)


if __name__=='__main__':unittest.main()
