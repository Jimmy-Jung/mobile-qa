# Third-party notices

Author: JunyoungJung · 2026-10-22

## agent-device 0.21.30

The iOS build helper copies selected runner and snapshot-presentation sources from a separately installed `agent-device` 0.21.30 into the user's selected external build folder and applies local account-input changes. The plugin does not bundle the upstream CLI, Apple SDKs, Android SDKs, or compiled runners. Preserve this notice when distributing the modified sources or binaries.

Upstream: [Callstack agent-device, v0.21.30](https://github.com/callstack/agent-device/tree/v0.21.30)

```text
MIT License

Copyright (c) 2026 Callstack

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The snapshot-bridge and its idb-derived code are not copied or bundled by this plugin. If that scope changes, include the bridge's separate `LICENSE.idb` and review its dependencies before distributing it.

Pillow and NumPy are installed separately under their own licenses; their source or binaries are not included in this plugin archive.
