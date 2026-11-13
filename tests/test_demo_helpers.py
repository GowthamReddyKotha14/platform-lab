import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import demo  # noqa: E402


def test_read_release_matches_repo_file():
    assert demo.read_release() == {"VERSION": "v1", "ERROR_RATE": "0", "EXTRA_LATENCY_MS": "0"}


def test_write_release_changes_values_and_keeps_comments(tmp_path):
    f = tmp_path / "release.yaml"
    shutil.copy(demo.RELEASE, f)
    before = f.read_text()
    demo.write_release({"VERSION": "v2-bad", "ERROR_RATE": "0.3"}, f)
    after = f.read_text()
    assert demo.read_release(f) == {"VERSION": "v2-bad", "ERROR_RATE": "0.3", "EXTRA_LATENCY_MS": "0"}
    assert [l for l in after.splitlines() if l.startswith("#")] == [l for l in before.splitlines() if l.startswith("#")]
    ops = yaml.safe_load(after)                              # still a valid JSON-6902 patch
    env = {e["name"]: e["value"] for e in ops[1]["value"]}
    assert env["ERROR_RATE"] == "0.3" and isinstance(env["ERROR_RATE"], str)   # k8s env values must be strings
    demo.write_release({"VERSION": "v1", "ERROR_RATE": "0"}, f)
    assert f.read_text() == before                           # round-trips exactly


def test_next_version():
    assert demo.next_version("v1") == "v2"
    assert demo.next_version("v2-bad") == "v3"
    assert demo.next_version("v3", "-bad") == "v4-bad"
