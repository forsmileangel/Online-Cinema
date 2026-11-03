import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "check-shared.py"


def load():
    spec = importlib.util.spec_from_file_location("check_shared", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedFilesTests(unittest.TestCase):
    def test_shared_files_match_the_sibling_repository(self):
        check = load()
        sibling = ROOT.parent / check.SIBLINGS[ROOT.name]
        if not sibling.is_dir():
            self.skipTest("the other site's repository is not checked out next to this one")
        drifted = check.drift(ROOT, sibling)
        self.assertEqual(drifted, [], "shared files differ between the two sites; copy the change to the other repo "
                                      "(python tools/check-shared.py --sync)")


if __name__ == "__main__":
    unittest.main()
