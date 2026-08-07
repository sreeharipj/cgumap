# cgumap

Recovers rustc codegen-unit (CGU) structure from a compiled Rust binary —
including fully stripped ones.

rustc splits every crate into codegen units before handing them to LLVM, and
sorts each unit's functions by mangled symbol name before emitting it. That
sort order survives the linker. Walking a binary in address order, a drop in
name order marks a CGU boundary. The result is groups of functions that were
compiled together — not names, but provenance structure you can triage in
blocks instead of one function at a time.

Static analysis only. Never executes, loads, or unpacks the target.

## Usage

```
python3 cgumap.py BINARY [--json OUT] [--ghidra OUT] [--min-group N] [--quiet]
```

```
$ python3 cgumap.py ./hexyl
cgumap  --  ./hexyl
  2014 functions from .eh_frame, 2014 with symbols
  method: A (symbol sort-order-reset; corpus mean ARI 0.883, homogeneity 0.985)
  recovered 86 groups

group         start           end   funcs     bytes  label
----------------------------------------------------------
    0       0x4aa10       0x4aa36       1        38  _start
    1       0x4ab00       0x58e67      56     57857  core
    2       0x58e70       0x5c789      37     14420  core
    3       0x5c790       0x6266c      83     23782  clap_builder
...
```

## Requirements

- Python 3.10+
- `pyelftools` — `pip install -r requirements.txt`
- `nm` (binutils) and `rustfilt`, optional — used for labelling only; the
  grouping itself works without them

## How it works

Two methods, auto-selected by what the binary retains:

- **Method A** (symbols present) — group address-sorted functions into runs
  of non-decreasing mangled name; each decrease is a CGU boundary candidate.
  Zero parameters, one linear pass.
- **Method B** (stripped) — `.eh_frame` survives `strip --strip-all`, so
  function boundaries are recoverable even with no symbol table. Partition
  that list into `K = round(sqrt(n))` byte-size-balanced groups.

Measured against real rustc ground truth (`-C save-temps` object files, not
an approximation) across 71 independently built binaries: median ARI 0.942,
homogeneity 0.998 — on rustc ≥ 1.91. Older toolchains score lower on
completeness (median ARI 0.858, more and smaller groups) but purity is
essentially unaffected. Full numbers, nulls, and the version cliff are in
the write-up below.

## Contents

| path | what's in it |
|---|---|
| `cgumap.py` | the tool |
| `results/` | CSV/JSON backing the headline numbers in the post |
| `figures/` | the plots from the post |

## Write-up

[Blog.]([(https://sreeharipj.github.io/posts/cgumap/))

## License

MIT
