# Security

Author: JunyoungJung · 2026-10-22

Use dedicated test accounts with the least privileges needed. Do not use production credentials or place credentials, local profiles, raw device logs, screenshots, or project knowledge in this repository or an issue.

The default account backend is a private local file, **not encrypted at rest**. File permissions do not protect against the same macOS user, malware, backups, or a sync service that can read that file. Existing Keychain entries remain an explicit migration source; installing this plugin does not move accounts.

Only a locally reviewed and trusted login profile may select an app, environment, and fields. A profile is an input contract, not executable project instructions. The helper checks the selected account scope, profile trust, release inventory, helper, and runner identities before input. A successful command is not proof of authentication or input cleanup. If cleanup is unconfirmed, stop capture, recording, tree collection, and repeat submissions until the condition is resolved.

The standard `agent-device fill --text-stdin` path can still leave raw text in iOS XCTest logs or `.xcresult`; it is not a replacement for this plugin's isolated login procedure. The isolated procedure reduces capture and diagnostic paths but does not establish that the app, operating system, or remote authentication server never stores input. Real-device diagnostics require separate validation.

The secure iOS runner source is checked against the official npm 0.21.30 source inventory in `mobile_qa/agent-device-0.21.30.json`, whose tarball was verified using npm SHA-512 integrity metadata. The release inventory also pins the local helper and profile interpreter. A matching version string alone does not authorize changed runner source. Each isolated session uses a separate listener token and encryption nonce. The listener token is carried in the private temporary xctestrun and HTTP request header; authenticated responses must declare `X-Agent-Device-Runner-Auth: required`.

Builds, caches, and raw evidence belong within the verified folder selected at setup, on internal or external storage. Missing or mismatched storage, unwritable paths, and symbolic links stop the operation; there is no automatic fallback outside the selected folder. Additional project or host storage policies still apply.

Project policy and reusable knowledge stay in the project-specific user data store, separate from the plugin installation. Plugin install directories contain no mutable account or project state.

Public packaging rejects absolute user-home and mounted-volume paths and excludes local account, credential, configuration, and build records. Project-specific rules belong in the local project store; the public release checker does not embed company or project name lists. Source review and secret scanning remain necessary because a path check cannot detect every kind of private project information.

For a suspected vulnerability, stop the affected flow and keep the original evidence local. Use GitHub's private vulnerability reporting for [Jimmy-Jung/mobile-qa](https://github.com/Jimmy-Jung/mobile-qa) if enabled, or contact its maintainer privately. Do not post secrets or reproductions containing real accounts in public issues.
