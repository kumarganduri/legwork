# Threat model

Legwork runs code nobody reviewed: a repo's install steps, and a wrapper a
model wrote from a README a stranger wrote. This page says what that code
can and can't reach, how that compares with Docker or a VM, and which
escapes have been found and fixed so far. The short version is in the
README's [Risks](README.md#risks-stated-plainly).

## What a tool can and can't do

A tool goes through three phases. **Install** runs the repo's install
steps, with network on because package installs need it. **Self-test**
runs the wrapper once, with network off. **Serve** runs it for your AI, by
default with no network and none of your files.

| | Install | Self-test | Serve | Serve with `--allow-net` |
|---|---|---|---|---|
| Your home folder | ✗ | ✗ | only folders you grant, read-only | same |
| Secret folders (`~/.ssh`, `~/.aws`, keychains, shell startup files, AI client configs, `~/.legwork.env`) | ✗ | ✗ | ✗, can't be granted | ✗ |
| System folders (`/usr`, `/opt`, `/etc`) | read | read | read | read |
| `/tmp`, macOS's per-user temp folder, `/Volumes` (external drives) | macOS: read; Linux: an empty private `/tmp` | same | same | same |
| Writing files | its own folder only | its own folder only | its own folder only | its own folder only |
| The internet | ✓ | ✗ | ✗ | ✓ |
| Services on this machine over TCP (`localhost`: databases, dev servers, debug ports) | macOS: ✗; **Linux: ✓** (see below) | ✗ | ✗ | ✓ |
| Unix sockets (SSH agent, Docker, password managers, X11) | ✗ | ✗ | ✗ | ✗ |
| macOS system services (clipboard, opening or scripting apps) | ✗ (the 13 that builds and tools need are allowed) | same | same | same |
| Your environment variables, model key, GitHub token | ✗ (it gets `PATH`, `HOME` and `TMPDIR` pointing into its own folder) | ✗ | ✗ | ✗ |
| Other processes | only the ones it started | same | same | same |
| What your AI sees | the install's result | the self-test's result | the tool's replies, labelled as untrusted and size-capped | same |

"Its own folder" is the build folder under `~/.legwork` (or
`LEGWORK_HOME`). Files a tool makes there are copied by the hub to
`~/Legwork/outputs/<tool>/`, outside the sandbox: never through a link,
never a hard-linked file, never executable, and on macOS marked with the
quarantine flag that downloaded files get.

**A granted folder is readable in full**, including any `.env` file or key
inside it, and a tool with both a folder and network could upload what it
reads. The refused places are a list of known credential locations, so it
can miss one your setup uses.

**Every grant is yours.** The folders and network you start the hub with
are a ceiling: an install can ask for those or less, and your client
shows you what it asks for before it runs (in clients that ask; see the
README's client table).

## Known gaps

- **Installs on Linux can reach `localhost`.** bubblewrap shares the
  host's network namespace during install so packages can download, and
  that includes TCP services listening on this machine. macOS blocks them
  with a profile rule. Unix sockets, including abstract ones, are blocked
  on both.
- **Installs on Linux architectures other than x86-64 and ARM64** can
  reach abstract Unix sockets: the seccomp filter that blocks them covers
  those two.
- **On macOS, a process that detaches itself (`setsid`) can outlive its
  install.** It stays in the sandbox, with the install's network access,
  until you log out. Linux kills it.
- **Installers run with network on.** Malicious install code can do
  anything inside its own folder and send what it finds there to the
  internet. Its own folder holds nothing of yours.
- **Prompt injection isn't mitigated.** A README can steer the model that
  writes the wrapper, and a tool's output can try to steer your AI. Both
  are labelled as untrusted text; neither label is a guarantee.
- **The pre-install scan is an obfuscation tripwire, not a malware
  scanner.** It catches the hiding patterns seen in real payloads and a
  determined author can get past it. It reads the repo's own source, not
  what installers download. The sandbox is the protection; the scan is an
  early warning.
- **Cached wrappers pin their top-level package**, not every dependency.

## Compared with Docker or a VM

| | Legwork | Docker | A VM |
|---|---|---|---|
| Isolation | the OS's own sandbox (`sandbox-exec` on macOS, bubblewrap + seccomp on Linux) | Linux namespaces, inside a VM on macOS | a separate kernel |
| Reads your files | only the folders you grant, read-only | only what you mount | only what you share |
| Startup per call | none (a process) | a container | a machine |
| Native macOS tools (Xcode's Python, system frameworks) | ✓ | ✗ (Linux inside) | with a macOS guest |
| Kernel bugs | exposed, as with any process | exposed (shared kernel) | contained |

A VM is the stronger boundary. Legwork trades that for running each tool
as a native process with no setup. If you need the VM's guarantee, run
Legwork inside one.

## Escapes found and fixed

Each fix has a regression test that fails on the old code.

| Date | Found by | What sandboxed code could do | Test |
|---|---|---|---|
| 2026-09-26 | our review | macOS install: connect to any Unix socket (SSH agent, Docker) | `test_install_cannot_reach_local_unix_sockets` |
| 2026-09-26 | our review | macOS: reach every system service, e.g. read the clipboard | `test_only_allowlisted_system_services_are_reachable` |
| 2026-09-27 | our review | Linux install: connect to abstract Unix sockets (X11) | `test_install_cannot_reach_abstract_unix_sockets` |
| 2026-09-27 | our review | grant `~/.ssh` when it didn't exist yet | `test_grants_refuse_home_and_secret_folders_whether_or_not_they_exist` |
| 2026-10-02 | pre-launch QA | a granted folder whose name looked like profile syntax rewrote the macOS sandbox rules | `test_a_granted_path_cannot_rewrite_the_sandbox_rules` |
| 2026-10-02 | pre-launch QA | rewrite the saved macOS profile before the next launch | `test_the_profile_never_touches_disk` |
| 2026-10-02 | pre-launch QA | read the hub's stdin, the AI client's messages | `test_sandboxed_code_cannot_read_the_callers_stdin` |
| 2026-10-02 | pre-launch QA | grant `~/.SSH` or `/USERS/you` (macOS ignores case) | `test_your_home_folder_is_refused_in_any_letter_case` |
| 2026-10-03 | independent review and Codex | plant a fake `sandbox-exec`/`bwrap` that the next step ran **outside the sandbox** | `test_a_launcher_planted_by_install_code_is_never_run` |
| 2026-10-03 | independent review and Codex | plant a link so Legwork overwrote one of your files | `test_legworks_own_files_in_the_workdir_are_written_without_following_links` |
| 2026-10-03 | independent review | grant shell startup files, AI client configs, or home spelled through `/System/Volumes/Data` | `test_grants_refuse_home_and_secret_folders_whether_or_not_they_exist`, `test_your_home_folder_is_refused_through_the_data_volume` |
| 2026-10-03 | security review | macOS install: connect to `localhost` services | `test_installs_reach_the_internet_but_not_services_on_this_machine` |
| 2026-10-03 | security review | Linux install: open sockets through io_uring, past the Unix-socket filter | `test_filter_turns_io_uring_off` |

Found one? See [SECURITY.md](SECURITY.md). Escapes are credited in the
release notes.
