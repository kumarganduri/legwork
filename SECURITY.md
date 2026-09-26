# Security

Legwork runs code nobody reviewed: a repo's install steps and a wrapper an
LLM wrote from its README. The sandbox and the pre-install scan are what
stand between that code and your machine, so reports about either are the
most useful thing you can send.

## Report privately

Use **[Report a vulnerability](https://github.com/kumarganduri/legwork/security/advisories/new)**
(GitHub private vulnerability reporting) for:

- **Sandbox escapes:** sandboxed code reading your home directory,
  writing outside its workdir, reaching the network while a wrapper is
  running or serving, or seeing environment variables it shouldn't
  (especially `LEGWORK_LLM_API_KEY`).
- **Key leaks:** your model key ending up in a wrapper, manifest, log or
  PR description despite the scrub.
- **Anything that makes Legwork run code outside the sandbox.**

Please include the Legwork version, your OS and version, and a minimal repo
or command that reproduces it. You'll get a reply within a week.

## Open an issue instead

- **Scanner bypasses.** The obfuscation scan is a heuristic and bypasses
  are expected; a public issue with the technique (not a live payload)
  helps improve it.
- **A malicious repo in the wild** that Legwork didn't catch: open an issue
  naming the repo, and report it to GitHub too. Please don't attach the
  payload.

## Known, disclosed limits

These are documented in the [README](README.md#risks-stated-plainly) and
aren't vulnerabilities by themselves: installs run with network on,
prompt injection from a README isn't mitigated, abstract Unix sockets are
reachable during install on desktop Linux, and the scan covers the
repo's own Python and JavaScript/TypeScript source, not what installers
download.
