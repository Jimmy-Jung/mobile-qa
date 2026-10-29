# Local account storage

Author: JunyoungJung · 2026-10-22
Updated: 2026-10-29

Test accounts are stored as plaintext JSON in `~/.config/agent-test-accounts/`. The directory uses mode `0700` and files use `0600`. The same macOS user and the AI's local tools can read these values. `get ALIAS` intentionally returns the username and password for use with ordinary device or browser input tools. Values read or entered through tools may remain in host tool history.

Registration, retrieval, updates, and checks run with Python alone. Native signing, trusted login profiles, release inventory checks, encrypted IPC, and input-cleanup checks are not prerequisites for this workflow. Existing native helpers remain optional compatibility tools and keep their own implementation requirements.

Do not include real accounts in the plugin package, source repository, replies, or shared reports. Keep account files outside version control. Atomic writes and shared file locks preserve existing account data during concurrent use. A successful storage or input command does not prove app authentication; verify the signed-in screen or server response before reporting login success.
