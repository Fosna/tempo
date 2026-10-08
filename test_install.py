"""Tests for the installer. Everything happens in temporary directories."""

import contextlib
import io
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

import install


class InstallCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        root = self._dir.name
        self.bin = os.path.join(root, "bin")
        self.skills = os.path.join(root, "skills")
        self.here = os.path.join(root, "clone")
        os.makedirs(os.path.join(self.here, ".claude", "skills", "tempo"))
        open(os.path.join(self.here, "tempo.py"), "w").close()
        for name, value in (("BIN", self.bin), ("SKILLS_DIR", self.skills), ("HERE", self.here)):
            patcher = mock.patch.object(install, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(install, "_nudge_problem", return_value=None)
        self.nudge = patcher.start()
        self.addCleanup(patcher.stop)

    def run_cmd(self, fn):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = fn(None)
        return code, out.getvalue()

    @property
    def shim(self):
        return os.path.join(self.bin, "tempo")

    @property
    def link(self):
        return os.path.join(self.skills, "tempo")


class TestInstall(InstallCase):
    def test_install_writes_shim_and_links_skill(self):
        code, out = self.run_cmd(install.cmd_install)
        self.assertEqual(code, 0)
        self.assertTrue(os.stat(self.shim).st_mode & stat.S_IXUSR)
        self.assertTrue(install.is_ours(self.shim))
        self.assertEqual(
            os.path.realpath(self.link),
            os.path.realpath(os.path.join(self.here, ".claude", "skills", "tempo")),
        )
        self.assertIn("linked", out)

    def test_shim_runs_this_clones_tempo_py(self):
        install.write_shim(python="/usr/bin/python3")
        with open(self.shim) as f:
            body = f.read()
        self.assertIn("exec '/usr/bin/python3' '%s'" % os.path.join(self.here, "tempo.py"), body)
        self.assertEqual(install.shim_script(self.shim), os.path.join(self.here, "tempo.py"))

    def test_paths_with_quotes_survive(self):
        weird = os.path.join(self._dir.name, "it's here")
        install.write_shim(python="/usr/bin/python3", here=weird)
        self.assertEqual(install.shim_script(self.shim), os.path.join(weird, "tempo.py"))

    def test_reinstall_is_idempotent(self):
        self.run_cmd(install.cmd_install)
        code, out = self.run_cmd(install.cmd_install)
        self.assertEqual(code, 0)
        self.assertIn("already linked", out)

    def test_refuses_a_foreign_tempo_on_the_path(self):
        os.makedirs(self.bin)
        with open(self.shim, "w") as f:
            f.write("#!/bin/sh\necho someone else's tempo\n")
        with self.assertRaises(SystemExit) as cm:
            install.write_shim()
        self.assertIn("not written by tempo", str(cm.exception))
        with open(self.shim) as f:
            self.assertIn("someone else's", f.read())

    def test_a_real_directory_at_the_skill_path_is_left_alone(self):
        os.makedirs(self.link)
        self.assertIn("left alone", install.link_skill("tempo"))
        self.assertFalse(os.path.islink(self.link))

    def test_a_link_to_another_clone_is_repointed(self):
        other = os.path.join(self._dir.name, "other")
        os.makedirs(other)
        os.makedirs(self.skills)
        os.symlink(other, self.link)
        self.assertEqual(install.link_skill("tempo"), "repointed")
        self.assertEqual(
            os.path.realpath(self.link),
            os.path.realpath(os.path.join(self.here, ".claude", "skills", "tempo")),
        )

    def test_missing_skill_in_the_clone_is_reported(self):
        with mock.patch.object(install, "SKILLS", ["tempo", "analytics"]):
            self.assertIn("missing in this clone", install.link_skill("analytics"))

    def test_notes_when_bin_is_not_on_the_path(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}):
            _, out = self.run_cmd(install.cmd_install)
        self.assertIn("not on your PATH", out)

    def test_install_passes_on_nudge_trouble_without_failing(self):
        self.nudge.return_value = "nudge is not on the PATH"
        code, out = self.run_cmd(install.cmd_install)
        self.assertEqual(code, 0)
        self.assertIn("nudge is not on the PATH", out)


class TestUninstall(InstallCase):
    def test_removes_what_install_wrote_and_nothing_else(self):
        self.run_cmd(install.cmd_install)
        state = os.path.join(self._dir.name, "state")
        os.makedirs(state)
        code, out = self.run_cmd(install.cmd_uninstall)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.shim))
        self.assertFalse(os.path.lexists(self.link))
        self.assertTrue(os.path.isdir(state))

    def test_leaves_a_foreign_shim_and_a_foreign_link(self):
        os.makedirs(self.bin)
        with open(self.shim, "w") as f:
            f.write("#!/bin/sh\necho other\n")
        elsewhere = os.path.join(self._dir.name, "elsewhere")
        os.makedirs(elsewhere)
        os.makedirs(self.skills)
        os.symlink(elsewhere, self.link)
        _, out = self.run_cmd(install.cmd_uninstall)
        self.assertTrue(os.path.exists(self.shim))
        self.assertTrue(os.path.islink(self.link))
        self.assertIn("not written by tempo", out)
        self.assertIn("points outside this clone", out)

    def test_sibling_clone_with_a_shared_prefix_is_not_ours(self):
        sibling = self.here + "-old"
        os.makedirs(os.path.join(sibling, ".claude", "skills", "tempo"))
        os.makedirs(self.skills)
        os.symlink(os.path.join(sibling, ".claude", "skills", "tempo"), self.link)
        self.run_cmd(install.cmd_uninstall)
        self.assertTrue(os.path.islink(self.link))

    def test_clean_machine(self):
        code, out = self.run_cmd(install.cmd_uninstall)
        self.assertEqual(code, 0)
        self.assertIn("nothing to uninstall", out)


class TestStatus(InstallCase):
    def test_healthy_after_install(self):
        self.run_cmd(install.cmd_install)
        code, out = self.run_cmd(install.cmd_status)
        self.assertEqual(code, 0)
        self.assertIn("installed", out)

    def test_not_installed(self):
        code, out = self.run_cmd(install.cmd_status)
        self.assertEqual(code, 1)
        self.assertIn("shim missing", out)
        self.assertIn("skill not linked", out)
        self.assertIn("install.py", out)  # names the repair command

    def test_a_moved_clone_is_noticed(self):
        install.write_shim(here=os.path.join(self._dir.name, "gone"))
        _, out = self.run_cmd(install.cmd_status)
        self.assertIn("did the clone move", out)

    def test_nudge_trouble_shows_up(self):
        self.run_cmd(install.cmd_install)
        self.nudge.return_value = "nudge is unhealthy: daemon not loaded"
        code, out = self.run_cmd(install.cmd_status)
        self.assertEqual(code, 1)
        self.assertIn("nudge is unhealthy", out)


class TestPathHint(InstallCase):
    """Off-PATH shims look like 'never installed'; say exactly how to fix it."""

    def hint(self, shell, path="/usr/bin:/bin"):
        return install.path_hint({"PATH": path, "SHELL": shell})

    def test_quiet_when_on_path(self):
        self.assertIsNone(self.hint("/bin/zsh", "/usr/bin:" + self.bin))

    def test_zsh_uses_zshenv_not_zshrc(self):
        # Claude Code's shell is non-interactive and never reads .zshrc
        self.assertIn(">> ~/.zshenv", self.hint("/bin/zsh"))

    def test_bash_uses_bash_profile(self):
        self.assertIn(">> ~/.bash_profile", self.hint("/bin/bash"))

    def test_unknown_shell_falls_back_to_the_shim_path(self):
        self.assertIn(self.shim, self.hint("/usr/bin/fish"))

    def test_status_passes_it_on(self):
        self.run_cmd(install.cmd_install)
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin", "SHELL": "/bin/zsh"}):
            code, out = self.run_cmd(install.cmd_status)
        self.assertEqual(code, 0)
        self.assertIn(">> ~/.zshenv", out)


class TestNudgeProblem(unittest.TestCase):
    def test_missing(self):
        with mock.patch("install.nudges.find", return_value=None):
            self.assertIn("not installed", install._nudge_problem())

    def test_unhealthy_and_healthy(self):
        bad = subprocess.CompletedProcess([], 1, stdout="daemon  not loaded\n", stderr="")
        good = subprocess.CompletedProcess([], 0, stdout="daemon  loaded\n", stderr="")
        with mock.patch("install.nudges.find", return_value="/x/nudge"):
            with mock.patch("install.subprocess.run", return_value=bad):
                self.assertIn("unhealthy", install._nudge_problem())
            with mock.patch("install.subprocess.run", return_value=good):
                self.assertIsNone(install._nudge_problem())


if __name__ == "__main__":
    unittest.main()
