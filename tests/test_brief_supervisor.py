"""Process isolation and explicit enablement; no subprocesses actually launched."""
import os
from unittest.mock import patch,Mock
import unittest
import run_web_with_briefs as launcher
import shared_brief_worker as worker


class SupervisorTests(unittest.TestCase):
    def test_disabled_worker_is_never_started(self):
        with patch.dict(os.environ,{'SHARED_BRIEF_AUTOMATION_ENABLED':'0','PORT':'8080'}),patch.object(launcher,'supervise',return_value=0) as run:
            self.assertEqual(launcher.main(),0)
            self.assertIsNone(run.call_args.args[1])
        with patch.dict(os.environ,{'SHARED_BRIEF_AUTOMATION_ENABLED':'0'}),patch.object(worker,'configuration',side_effect=AssertionError()):
            self.assertEqual(worker.main(),0)

    def test_worker_restart_does_not_restart_web_and_shutdown_cleans_children(self):
        class Process:
            def __init__(self,code=None):self.returncode=code;self.terminated=False
            def poll(self):return self.returncode
            def terminate(self):self.returncode=0;self.terminated=True
            def wait(self,timeout):return self.returncode
        web,failed,replacement=Process(),Process(1),Process()
        seconds=[0]
        def sleep(_):
            seconds[0]+=60
            if seconds[0]>=180:web.returncode=0
        spawn=Mock(side_effect=[web,failed,replacement])
        self.assertEqual(launcher.supervise(['web'],['worker'],popen=spawn,sleep=sleep,clock=lambda:seconds[0]),0)
        self.assertEqual(spawn.call_args_list[0].args[0],['web'])
        self.assertEqual(spawn.call_count,3)
        self.assertTrue(replacement.terminated)
        self.assertFalse(web.terminated)

    def test_invalid_worker_configuration_does_not_open_or_create_databases(self):
        with patch.dict(os.environ,{'SHARED_BRIEF_AUTOMATION_ENABLED':'1','DB_PATH':'sqlite:///:memory:'}), \
             patch.object(worker.operations,'initialize',side_effect=AssertionError()):
            self.assertEqual(worker.main(),1)
