#!/usr/bin/env python3
"""Explicit nonpersistent KDE notification/action test; no real Siegfried session."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO/'src'))
from siegfried.integrations.briefing_kde import KDEBriefingPresenter, REPLLauncher


def verify():
    with tempfile.TemporaryDirectory(prefix='siegfried-f53-') as folder:
        root=Path(folder);(root/'bin').mkdir(mode=0o700)
        marker=root/'opened'
        fake=root/'bin/siegfried'
        fake.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("opened")\n')
        fake.chmod(0o700)
        launcher=REPLLauncher(root)
        assert launcher.available(), 'safe test launcher unavailable'
        invoked=[]
        def action(token):
            env=dict(os.environ)
            if token:env['XDG_ACTIVATION_TOKEN']=token
            child=subprocess.run(launcher.argv, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2)
            invoked.append(child.returncode)
        presenter=KDEBriefingPresenter(action)
        start=time.monotonic()
        connection=None
        try:
            assert presenter.start(), 'native notification unavailable'
            connection=presenter.bus
            assert presenter.actions, 'actions unavailable'
            assert presenter.send('Buenos días, Señor. Prueba F5.3 sin información privada.',
                                  'Buenos días, Señor. Prueba F5.3 sin información privada.',expiry_ms=3000)
            notification_id=presenter.notification_id
            sent_ms=(time.monotonic()-start)*1000
            assert not marker.exists() and not invoked, 'automatic launch detected'
            def invoke():
                presenter._call(presenter.owner,'/org/freedesktop/Notifications',
                                'org.kde.NotificationManager','InvokeAction','us',(notification_id,'open'))
                return False
            presenter.sources.append(presenter.GLib.timeout_add(100,invoke))
            presenter.wait(3)
            assert invoked==[0] and marker.read_text()=='opened', 'test action did not run once'
            return {'native_notify':'PASS','native_action':'PASS','konsole_test_executable':'PASS',
                    'automatic_launch':False,'action_count':len(invoked),'sent_ms':round(sent_ms,3),
                    'visible_ms':None,'inhibited':presenter.inhibited,
                    'visual_validation':'PENDING; API acceptance is not visual appearance',
                    'real_siegfried_started':False,'autostart_modified':False}
        finally:
            presenter.close()
            assert not presenter.matches and presenter.bus is None
            assert connection is None or not connection.get_is_connected()


if __name__=='__main__':
    print(json.dumps(verify(),indent=2))
