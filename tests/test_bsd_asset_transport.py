from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_bsd_transport_preserves_pinned_asset_and_other_https_requests():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    transport = (
        Path(__file__).resolve().parents[1]
        / "tools/bsd/visual/github_asset_transport.mjs"
    )
    subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            """
import assert from 'node:assert/strict';
import https from 'node:https';
import * as namespace from 'node:https';

const calls = [];
const request = {};
https.get = (...args) => { calls.push(args); return request; };
await import(process.argv[1]);

const callback = () => {};
const pinned = 'https://github.com/anyvm-org/anyvm/releases/download/v0.7.2/anyvm.py';
assert.equal(namespace.get(pinned, callback), request);
assert.equal(calls[0][0], 'https://api.github.com/repos/anyvm-org/anyvm/releases/assets/589232152');
assert.equal(calls[0][1].headers.Accept, 'application/octet-stream');
assert.equal(calls[0][1].headers['User-Agent'], 'docking-bsd-ci');
assert.equal(calls[0][2], callback);

const options = { timeout: 1000, headers: { 'X-Test': 'preserved' } };
namespace.get(pinned, options, callback);
assert.equal(calls[1][1].timeout, 1000);
assert.equal(calls[1][1].headers['X-Test'], 'preserved');
assert.equal(calls[1][2], callback);
assert.deepEqual(options, { timeout: 1000, headers: { 'X-Test': 'preserved' } });

// Leave other versions, VM images, and normal HTTPS traffic untouched.
for (const url of [
  'https://github.com/anyvm-org/anyvm/releases/download/v0.7.5/anyvm.py',
  'https://api.github.com/repos/anyvm-org/freebsd-builder/releases',
]) {
  namespace.get(url, options, callback);
  assert.deepEqual(calls.at(-1), [url, options, callback]);
}
""",
            transport.as_uri(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
