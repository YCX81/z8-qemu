# Publication boundary

An initial publication must start with reviewed Git history. Do not push a branch
of the private research repository, even if its latest tree excludes binaries.
Every reachable commit and staged blob belongs in the review.

Intended distribution: independently authored emulator/tool source, formulas, interface addresses/masks,
sample-identification hashes, synthetic input generators, tests, documentation
and required open-source license notices.

Excluded payloads: original or decrypted Nikon programs, firmware containers, camera
memory/flash dumps, keys, calibration images, extracted fonts/icons, copied
program instruction arrays, private photos, original UI screenshots and
runtime artifacts. Shader recognition uses hashes. The color knot coordinates
and indirect table selectors use formulas rather than embedded ROM tables.
Register masks and event mappings express interoperability behavior; they are
not a distribution of the original driver implementation.

`parameter_input.py` reads a default body from the user's local MAIN and creates
an explicit diagnostic ROM image only in the local run directory. No such
image is tracked or downloaded. Small synthetic version/selector/trailer
inputs are identified as assumptions, not recovered factory values.

The allowlist/manifest audit is an additional guard, not an automatic origin
or licensing determination. Maintainers must manually inspect new arrays,
fixtures, documentation and imports before refreshing it. A `.py` or `.c`
extension alone does not establish that a file is safe to publish.

## Known history residual and review categories

The Z8 profile at public commit `950dff15817d14849bdcb921965fb46af0228c5d`
contains `entry_prefix_hex`, a copied 32-byte program prefix. The current-tree
cleanup removes the field and relies on the full MAIN length and SHA-256.
Older reachable commits still contain the field. The existing audit's large-hex
threshold did not detect it; its previous success must not be described as a
complete content-origin review. No history rewrite or force-push is performed
as part of this cleanup.

Review interface addresses and behavior descriptions, instruction-level
explanations, copied original bytes, and key-recovery descriptions separately.
An allowlisted path or a research purpose does not settle all questions about
the content. Do not attach raw firmware or private runtime material to document
the residual. Record the path, field name and affected commit identity instead.

## Derived MAIN recipes and instruction-level material

The source tree publishes reusable firmware modification recipes in
`z8_picture_control.py`, `z8_slots_core.py` and their image builders. Keystone
assembles authored replacement code; the tools also read original instructions
and tables from the user's identity-checked MAIN at runtime. A recipe is a
separate publication category from an emulator model or an unmodified-program
launch. It needs source, attribution and distribution review in its own right.

Local derived MAIN, owned-region files and experiment patch receipts can contain
original firmware bytes as well as replacements. They remain excluded artifacts.
They are **not camera update files and must not be written to a camera**. No
update-container generation, physical compatibility or device safety is verified.
The absence of a flashing tool does not settle the publication questions.

The current tree is not free of original instruction-level material. The
2026-10-13 targeted review found 30 matching word guards in the writer observer:
20 literal words and 10 branch encodings computed from addresses. A Picture
Control prologue guard and associated test literals are further examples.
See the [technical review](z8-public-review-20261013.md) for paths and scope;
that review does not reproduce the bytes and is not an exhaustive provenance
or legal assessment. This finding is separate from the older 32-byte profile
prefix in Git history. The intended exclusions above are not a claim that all
existing or historical content already complies with them.

Pending work: replace embedded original-word guards with reads from the locally
supplied, fully identity-checked MAIN; retain address/span/mapping validation and
test negative cases before changing runtime admission. Review tests, recipes,
instruction explanations, key-recovery documentation and historical residuals
separately. Do not relabel a manifest refresh as completion of that review.
No history rewrite or force-push is included in this work.
