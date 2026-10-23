import json
import subprocess
import sys


def test_version_prints_one_json_object():
    out = subprocess.run([sys.executable, "-m", "youtube_upload", "--version"], capture_output=True, text=True)
    assert out.returncode == 0
    assert json.loads(out.stdout)["ok"] is True
