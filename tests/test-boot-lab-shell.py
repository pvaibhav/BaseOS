#!/usr/bin/env python3
"""Exercise lab.sh's completion protocol without contacting a device."""
import subprocess
import json
import os
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/h700-boot-speed"))
import lab


def local_shell(command, timeout):
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, timeout=timeout)
    return subprocess.CompletedProcess(result.args, result.returncode,
                                       result.stdout.decode(), result.stderr.decode())


class BootLabShell(unittest.TestCase):
    def test_successful_output_without_trailing_newline(self):
        with patch.object(lab, "raw_shell", side_effect=local_shell) as transport:
            self.assertEqual(lab.sh("printf '  captured text  '", 5), "captured text")
        self.assertEqual(transport.call_args.args[1], 5)

    def test_crlf_output(self):
        with patch.object(lab, "raw_shell", side_effect=local_shell):
            self.assertEqual(lab.sh("printf 'first\\r\\nsecond\\r\\n'"), "first\nsecond")

    def test_successful_command_with_no_output(self):
        with patch.object(lab, "raw_shell", side_effect=local_shell):
            self.assertEqual(lab.sh(":"), "")

    def test_caller_can_explicitly_allow_optional_command_failure(self):
        with patch.object(lab, "raw_shell", side_effect=local_shell):
            self.assertEqual(lab.sh("printf 'optional output'; false || true"), "optional output")

    def test_remote_nonzero_exit_is_not_hidden_by_completion_marker(self):
        with patch.object(lab, "raw_shell", side_effect=local_shell):
            with self.assertRaisesRegex(RuntimeError, "exited with status 127"):
                lab.sh("printf 'helper: not found\\n'; (exit 127)")

    def test_explicit_remote_exit_still_reports_its_status(self):
        with patch.object(lab, "raw_shell", side_effect=local_shell):
            with self.assertRaisesRegex(RuntimeError, "exited with status 7"):
                lab.sh("exit 7")

    def test_marker_like_output_is_preserved(self):
        with patch.object(lab, "raw_shell", side_effect=local_shell):
            self.assertEqual(lab.sh("printf '__LAB_END__\\n__LAB_END__:0\\n'"),
                             "__LAB_END__\n__LAB_END__:0")

    def test_output_ending_in_old_marker_cannot_prove_completion(self):
        result = subprocess.CompletedProcess([], 0, "capture interrupted __LAB_END__\r\n", "")
        with patch.object(lab, "raw_shell", return_value=result):
            with self.assertRaises(RuntimeError):
                lab.sh("printf 'capture interrupted __LAB_END__'")

    def test_incomplete_adb_output_is_rejected(self):
        result = subprocess.CompletedProcess([], 0, "partial capture\r\n", "")
        with patch.object(lab, "raw_shell", return_value=result):
            with self.assertRaises(RuntimeError):
                lab.sh("read-something")

    def test_completion_must_be_a_complete_final_line_with_a_valid_status(self):
        corruptions = {
            "truncated": lambda output: output[:-1],
            "trailing output": lambda output: output + "unexpected output\n",
            "non-numeric status": lambda output: output.rsplit(":", 1)[0] + ":zero\n",
            "invalid status": lambda output: output.rsplit(":", 1)[0] + ":256\n",
        }
        for name, corrupt in corruptions.items():
            def incomplete_transport(command, timeout):
                result = local_shell(command, timeout)
                return subprocess.CompletedProcess(result.args, 0, corrupt(result.stdout), "")

            with self.subTest(name=name), patch.object(lab, "raw_shell", side_effect=incomplete_transport):
                with self.assertRaises(RuntimeError):
                    lab.sh("printf 'captured text'")

    def test_transport_failure_is_rejected_even_with_complete_remote_output(self):
        def failed_transport(command, timeout):
            result = local_shell(command, timeout)
            return subprocess.CompletedProcess(result.args, 1, result.stdout, "device offline")

        with patch.object(lab, "raw_shell", side_effect=failed_transport):
            with self.assertRaisesRegex(RuntimeError, "device offline"):
                lab.sh("printf 'complete capture'")

class BootLabRestart(unittest.TestCase):
    def run_script(self, *, card="rw", root="rw", data="rw", fail_mount="", fail_restart=False, frontend=True, fail_after_change=False):
        with tempfile.TemporaryDirectory(prefix="boot-lab-restart-") as directory:
            work = Path(directory)
            mounts, events = work / "mounts", work / "events"
            modes = {"/": root, "/data": data}
            if card is not None:
                modes["/mnt/sdcard"] = card
            mounts.write_text("".join(f"/dev/test {path} ext4 {mode},noatime 0 0\n" for path, mode in modes.items()))
            tool = work / "fake-command"
            tool.write_text("#!" + sys.executable + "\n" + r'''
import json, os, sys
from pathlib import Path
name=Path(sys.argv[0]).name
mounts=Path(os.environ['FAKE_MOUNTS']); events=Path(os.environ['FAKE_EVENTS'])
def log(value):
 with events.open('a') as f:f.write(json.dumps(value)+'\n')
def modes():return {line.split()[1]:line.split()[3].split(',')[0] for line in mounts.read_text().splitlines()}
if name=='ps':
 if os.environ['FAKE_FRONTEND']=='1':print('123 nextui.elf\n456 minarch.elf')
elif name=='mount':
 mode=sys.argv[2].split(',')[1];point=sys.argv[3];log(['mount',mode,point])
 failing=mode=='ro' and point==os.environ.get('FAIL_MOUNT')
 if not failing or os.environ.get('FAIL_AFTER_CHANGE')=='1':
  lines=[line.split() for line in mounts.read_text().splitlines()]
  for line in lines:
   if line[1]==point:line[3]=mode+',noatime'
  mounts.write_text(''.join(' '.join(line)+'\n' for line in lines))
 if failing:sys.exit(1)
elif name=='restart':log(['restart',modes()]);sys.exit(1 if os.environ['FAIL_RESTART']=='1' else 0)
else:log([name,*sys.argv[1:]])
''')
            tool.chmod(0o755)
            for name in ("mount", "dd", "sync", "ps", "kill", "restart"):
                (work / name).symlink_to(tool)
            restart = str(work / "restart")
            command = lab._restart_shell(restart if fail_restart else "exec " + restart)
            for name in ("mount", "dd", "sync"):
                command = command.replace("/bin/" + name, str(work / name))
            command = command.replace("kill -", str(work / "kill") + " -").replace("/proc/mounts", str(mounts))
            env = dict(os.environ, PATH=str(work) + os.pathsep + os.environ["PATH"], FAKE_MOUNTS=str(mounts), FAKE_EVENTS=str(events), FAIL_MOUNT=fail_mount, FAIL_RESTART=str(int(fail_restart)), FAKE_FRONTEND=str(int(frontend)), FAIL_AFTER_CHANGE=str(int(fail_after_change)))
            result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, env=env)
            observed = [json.loads(line) for line in events.read_text().splitlines()]
            after = {line.split()[1]: line.split()[3].split(",")[0] for line in mounts.read_text().splitlines()}
            return result, observed, after, modes

    def test_success_quiesces_frontend_card_before_restart(self):
        result, events, after, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        restart = next(e for e in events if e[0] == "restart")
        self.assertEqual(restart[1], {"/": "ro", "/data": "ro", "/mnt/sdcard": "ro"})
        self.assertEqual([e for e in events if e[0] == "mount"], [["mount", "ro", "/mnt/sdcard"], ["mount", "ro", "/data"], ["mount", "ro", "/"]])
        self.assertEqual([e for e in events if e[:2] == ["kill", "-CONT"]], [])
        self.assertEqual(after, restart[1])

    def test_each_remount_failure_restores_only_changed_mounts_and_resumes(self):
        for point in ("/mnt/sdcard", "/data", "/"):
            with self.subTest(point=point):
                result, events, after, before = self.run_script(fail_mount=point)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("RESTART_FAILED", result.stdout)
                self.assertFalse(any(e[0] == "restart" for e in events))
                self.assertEqual(after, before)
                self.assertEqual([e[2] for e in events if e[:2] == ["kill", "-CONT"]], ["123", "456"])
                self.assertNotIn(["mount", "rw", point], events)

    def test_failed_mount_that_changed_state_is_restored(self):
        result, events, after, before = self.run_script(fail_mount="/mnt/sdcard", fail_after_change=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(["mount", "rw", "/mnt/sdcard"], events)
        self.assertEqual(after, before)

    def test_returned_restart_restores_mounts_and_resumes_frontend(self):
        result, events, after, before = self.run_script(fail_restart=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RESTART_FAILED_COMMAND_RETURNED", result.stdout)
        self.assertEqual(after, before)
        self.assertEqual([e for e in events if e[:2] == ["mount", "rw"]], [["mount", "rw", "/"], ["mount", "rw", "/data"], ["mount", "rw", "/mnt/sdcard"]])
        self.assertEqual([e[2] for e in events if e[:2] == ["kill", "-CONT"]], ["123", "456"])

    def test_readonly_initial_modes_stay_readonly(self):
        result, events, after, before = self.run_script(card="ro", root="ro", data="ro", fail_restart=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(after, before)
        self.assertFalse(any(e[0] in ("mount", "dd") for e in events))

    def test_unmounted_card_and_no_frontend_need_no_card_actions(self):
        result, events, _, _ = self.run_script(card=None, frontend=False)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(any(e[0] == "kill" or e[0] == "mount" and e[-1] == "/mnt/sdcard" for e in events))


if __name__ == "__main__":
    unittest.main()
