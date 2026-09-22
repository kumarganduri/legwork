# Fixture: real malicious payload

`ai_data_extractor_payload.py` in this directory is a verbatim copy of
`extractors/extract.py` from the public GitHub repo
`kruzovic7/ai-data-extractor`, captured 2026-09-22 during Legwork's
validation spike (see `docs/designs/legwork-validation-spike-2026-09-22.md`
in this repo).

It contains XOR-obfuscated import names and a large encrypted blob that
gets decrypted and `exec()`'d into the module's global namespace at import
time. Running it (importing it, or running `python extract.py` from its
real repo) already triggered a `RuntimeError: win32 only` on a real machine
during the spike — meaning the hidden payload executed far enough to hit a
platform gate. What it does on Windows was not investigated.

**Do not import or execute this file.** It exists purely as a static-
analysis test corpus for `legwork/obfuscation_scanner.py`, which only ever
reads it as text and parses it with `ast.parse` — never imports or execs
it. Tests in `tests/test_obfuscation_scanner.py` do the same.
