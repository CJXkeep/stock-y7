"""Offline UI contracts: terminal risk cannot re-enable, untrusted text is escaped."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is needed for frontend verification")
class TrendFrontendTests(unittest.TestCase):
    def test_controls_and_safe_rendering(self):
        module = (Path(__file__).resolve().parents[1] / "dashboard/js/trend.js").as_uri()
        script = "import assert from 'node:assert/strict';\n"
        script += f"import {{controls, table, number}} from {json.dumps(module)};\n"
        script += """
assert.equal(controls({exists: false}).enable, true);
assert.equal(controls({exists: false}).create, false);
assert.equal(controls({exists: true, enabled: false, risk: {status: 'halted'}}).enable, true);
assert.equal(controls({exists: true, enabled: true, risk: {status: 'failed'}}).run, false);
assert.equal(controls({exists: true, enabled: false}).run, true);
assert.equal(controls({exists: true, state: {positions: {'600000': {}}}}).create, true);
assert.equal(controls({exists: true, enabled: true}).pause, false);
assert.equal(controls({exists: true}, true).run, true);
assert.equal(number(null), '—');
assert.equal(number(NaN), '—');
const html = table(['Name'], [['<img src=x onerror=alert(1)>']]);
assert(!html.includes('<img'));
assert(html.includes('&lt;img'));
assert(table([], [], '<script>').includes('&lt;script&gt;'));
"""
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
