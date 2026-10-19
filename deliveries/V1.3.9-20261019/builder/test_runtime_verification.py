from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import verify_release as verify


class VerificationTests(unittest.TestCase):
    def fixture(self):
        return (
            {"version": verify.NAV_VERSION},
            {"version": verify.VERSION, "navigation_version": verify.NAV_VERSION, "pnc_policy_skipped": False},
            {"navigation": {"dense_crowd": dict(verify.PROFILE), "task_failure_policy": dict(verify.FAILURE_POLICY)}},
        )

    def test_every_profile_and_recovery_field_is_verified(self):
        info, state, data = self.fixture()
        verify.verify_config(info, state, data)
        for group, fields in data["navigation"].items():
            for name, value in fields.items():
                with self.subTest(group=group, name=name):
                    changed = deepcopy(data)
                    changed["navigation"][group][name] = not value if isinstance(value, bool) else value + 1
                    with self.assertRaises(ValueError):
                        verify.verify_config(info, state, changed)
        for name in ("enabled", "require_low_speed"):
            changed = deepcopy(data)
            changed["navigation"]["dense_crowd"][name] = 1
            with self.assertRaises(ValueError):
                verify.verify_config(info, state, changed)

    def test_wrong_versions_and_skip_flag_are_rejected(self):
        info, state, data = self.fixture()
        for key in state:
            changed = dict(state)
            changed[key] = "invalid"
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify.verify_config(info, changed, data)

    def test_classpath_must_be_real_arguments_and_cannot_load_older_classes_first(self):
        current = verify.CURRENT
        args = [
            (current / "runtime/java/bin/java").as_posix(), "-cp",
            (current / "java/robot-local-navigation-java-all.jar").as_posix() + ":" +
            (current / "java/linksoul-agentsdk-1.4.0.jar").as_posix(),
            "com.agibot.aiem.nav.LingxiNavAgentApp",
            "--config", "/etc/sparkrobot/lingxi-nav/lingxi-nav.properties",
        ]
        verify.verify_java_args(args)
        execution = "{ path=" + args[0] + " ; argv[]=" + " ".join(args) + " ; ignore_errors=no ; }"
        self.assertEqual(verify.parse_execution(execution), args)
        for changed in (
            [args[0], "-Dreference=" + args[2], "-cp", "/overrides/old.jar", *args[3:]],
            [*args[:2], "/overrides/old.jar:" + args[2], *args[3:]],
            [*args, "-javaagent:/tmp/override.jar"],
            [args[0], "-cp", args[2] + ".old", *args[3:]],
        ):
            with self.assertRaises(ValueError):
                verify.verify_java_args(changed)


if __name__ == "__main__":
    unittest.main()
