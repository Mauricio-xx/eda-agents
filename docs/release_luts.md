# Releasing GF180 gm/ID LUTs

The GF180 gm/ID lookup tables (~73 MB of `.npz` files) are too large
to bundle in the Python wheel, so eda-agents downloads them on first
use from a GitHub Release asset. This doc is the maintainer-side
procedure for cutting a new LUT release.

## When to bump

- New sweep data (different `vds_max`, extra Vbs corners, added fT
  capacitance columns).
- PDK version bump that changes device parameters enough to require a
  resimulated LUT.
- LUT schema change (new keys consumed by `GmIdLookup`).

Do **not** bump for cosmetic changes that leave the `.npz` byte
content identical — clients already have the file cached.

## Procedure

1. Generate the new LUTs and place them in a staging directory.
   Canonical filenames: `gf180_nfet_03v3.npz`, `gf180_pfet_03v3.npz`.
2. Compute SHA256 of each file:
   ```bash
   sha256sum gf180_nfet_03v3.npz gf180_pfet_03v3.npz
   ```
3. Pick a new tag. Convention: `luts-vN` where `N` increments
   monotonically. Never reuse an old tag — clients have the old
   checksums cached.
4. Update `src/eda_agents/core/lut_fetcher.py`:
   - Bump `_RELEASE_TAG` to the new tag.
   - Replace `_CHECKSUMS` entries with the new SHA256 values.
5. Create the Release on GitHub:
   ```bash
   gh release create luts-vN gf180_nfet_03v3.npz gf180_pfet_03v3.npz \
     --title "gm/ID LUTs vN" \
     --notes "See docs/release_luts.md for the procedure."
   ```
6. Smoke-test on a clean machine:
   ```bash
   rm -rf ~/.cache/eda-agents
   python -c "from eda_agents.core.lut_fetcher import resolve_gmid_lut; \
              print(resolve_gmid_lut('gf180_nfet_03v3.npz'))"
   ```
   The path should land under `~/.cache/eda-agents/gmid_luts/`.
7. Commit the `lut_fetcher.py` changes, push, tag the repo with a
   code release (separate from the asset release tag), publish.

## Rollback

If a release is broken, delete the GitHub Release asset immediately
(clients will fail SHA256 verification and retry). Then cut the next
`luts-v(N+1)` with corrected assets and a code-release hotfix.
