# Handover

## Next concrete task

Validate this pipeline against a legally user-provided USA `SLUS-01032` BIN/CUE without committing or copying it into the repository.

1. Import the CUE into a project outside this source tree.
2. Compare the catalogue inventory and extents against `dumpsxiso -l`/its extraction manifest from the pinned `mkpsxiso` version used by `dw_decomp`.
3. Record only non-proprietary facts: source hash supplied by the user, layout, file names/sizes/hashes if legally appropriate, parser discrepancies, and tool versions.
4. Investigate path-table traversal before claiming full inventory, because some PS1 discs can contain directories absent from ordinary directory-record walks.
5. Add a verified release-manifest mechanism (data file containing permitted hashes/metadata, not game bytes) and downgrade product-code-only matches to “probable” if comparison reveals revision variants.

After inventory parity, the next format task is to research `CHDAT/ALLTIM.TIM` in source code and tools, establish its container boundaries from reproducible evidence, and add a container parser that emits child resources with exact parent offset, size, hash, palette metadata, and decoder version. Do not search arbitrary byte streams for TIM magic and call the results mapped assets.

## Known engineering follow-ups

- Add ISO multi-extent/interleaved-file detection with explicit rejection.
- Hash the source again on reopen when the user requests verification; size/mtime is currently the fast stale guard and the original SHA-256 remains recorded.
- Move file hashing to streaming sector reads for very large extents.
- Add palette selection in the TIM GUI (decoder supports an index; UI currently previews palette 0).
- Parse PS-X EXE load/entry/size fields and show them distinctly from section analysis.
- Add catalogue migrations before schema version 2.
