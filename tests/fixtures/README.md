# Fixture: defanged copy of a real malicious payload

`ai_data_extractor_payload.py` is `extractors/extract.py` from the public
GitHub repo `kruzovic7/ai-data-extractor`, captured 2026-09-22 during
Legwork's validation spike (see
`docs/designs/legwork-validation-spike-2026-09-22.md`), **with its payload
destroyed**.

The original hid its import names behind XOR and carried a large encrypted
blob that it decrypted and `exec()`'d into the module's namespace at import
time. During the spike, running it on a real machine hit `RuntimeError:
win32 only`, so the hidden payload executed far enough to reach a platform
gate.

Before this repo was published (2026-09-26), every number in the file's 37
integer-list literals (6,937 values: the encrypted blob, the keys and the
XOR-encoded names) was replaced with random bytes. The code around them is
untouched, so the scanner still sees exactly the technique it was built
against, but the names now decode to garbage and there is no payload left
to decrypt. The live version was never pushed; the git history was
rewritten before the first push.

It's still a test corpus only: `legwork/obfuscation_scanner.py` and
`tests/test_obfuscation_scanner.py` read it as text and parse it with
`ast.parse`. There is no reason to import or run it.
