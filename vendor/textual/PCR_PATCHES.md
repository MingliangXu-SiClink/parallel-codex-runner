# PCR Textual Vendor Notes

This directory contains the runtime source snapshot of
[`Textualize/textual`](https://github.com/Textualize/textual) at tag `v8.2.8`,
commit `1d99508b928a771b51e1a527319c6b87dcff9e05`. The upstream MIT license is
preserved in `LICENSE`.

PCR keeps `src/textual`, the upstream license, and these patch notes. Upstream
documentation, examples, tests, media, and development configuration are
intentionally omitted because they are not used at runtime. PCR packages the
source under its private `_vendor` namespace, activates it only for the TUI,
and does not depend on or overwrite the separately published `textual` wheel.
The installed private package also carries an exact copy of `LICENSE`.

## Local patches

- `src/textual/_unicode.py` adds dependency-free grapheme boundaries and CJK-aware word stops.
- `Input` and `TextArea` move, select, and delete whole grapheme clusters instead of splitting combining marks, variation selectors, emoji modifiers, or ZWJ sequences.
- CJK text gets one predictable Ctrl/Option word stop per grapheme when no whitespace-based word boundary exists.
- Mouse-to-column conversion and cursor styling snap to complete grapheme clusters.
- iTerm uses the IME-safe Kitty keyboard mode so committed Chinese text reaches the editor while modified keys remain distinguishable.
- `textual.__version__` is `8.2.8+pcr.1`, avoiding a runtime dependency on separate `textual` package metadata.

## Verification

Run PCR's complete test suite from the repository root:

```bash
python3 -m unittest
```
