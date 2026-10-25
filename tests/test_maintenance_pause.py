import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


spec = importlib.util.spec_from_file_location("maintenance_pause",
    Path(__file__).resolve().parents[1] / "scripts/pause_batch_for_recovery.py")
pause = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pause)


@unittest.skipUnless(os.name == "posix", "Maintenance pause uses POSIX process signals")
class MaintenancePauseTests(unittest.TestCase):
    def fixture(self, root):
        native = root / "native.py"
        native.write_text('''import os,signal,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1])
job=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
(root/"job.pid").write_text(str(job.pid))
for line in sys.stdin: pass
''')
        adapter = root / "adapter.py"
        adapter.write_text('''import signal,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1])
native=subprocess.Popen([sys.executable,str(root/"native.py"),str(root)],stdin=subprocess.PIPE)
(root/"native.pid").write_text(str(native.pid))
def drain(*args):
    native.stdin.close()
    native.wait(timeout=10)
    raise SystemExit(130)
signal.signal(signal.SIGTERM,drain)
while True: time.sleep(0.1)
''')
        supervisor = subprocess.Popen([sys.executable, "-c", '''
import subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1])
adapter=subprocess.Popen([sys.executable,str(root/"adapter.py"),str(root)])
(root/"adapter.pid").write_text(str(adapter.pid))
while True: time.sleep(0.1)
''', str(root)], start_new_session=True)
        deadline = time.monotonic() + 5
        while not (root / "job.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return supervisor, {name: int((root / f"{name}.pid").read_text()) for name in ("adapter", "native", "job")}

    def test_pause_preserves_detached_computation_and_never_uses_user_stop(self):
        self.exercise(False)

    def test_failed_safety_audit_thaws_native_and_supervisor_without_retiring_them(self):
        self.exercise(True)

    def exercise(self, reject):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor, pids = self.fixture(root)
            phases = []

            def audit(phase, rows):
                phases.append(phase)
                self.assertTrue("T" in rows[pids["native"]]["status"] if phase != "drained" else True)
                if reject:
                    raise RuntimeError("active tool has not reached a safe boundary")

            try:
                if reject:
                    with self.assertRaisesRegex(RuntimeError, "active tool"):
                        pause.quiesce(supervisor.pid, [pids["adapter"]], [pids["native"]], audit)
                    self.assertIsNone(supervisor.poll())
                    self.assertNotIn("T", pause.processes()[pids["native"]]["status"])
                else:
                    result = pause.quiesce(supervisor.pid, [pids["adapter"]], [pids["native"]], audit)
                    self.assertTrue(result["retired"])
                    supervisor.wait(timeout=5)
                    self.assertEqual(phases, ["frozen", "before-drain", "drained"])
                os.kill(pids["job"], 0)
                self.assertFalse(list(root.glob("*stop*")))
            finally:
                for pid in (supervisor.pid, *pids.values()):
                    try:
                        os.kill(pid, signal.SIGCONT)
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                supervisor.wait(timeout=5)
