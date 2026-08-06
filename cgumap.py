#!/usr/bin/env python3
"""
cgumap -- recover compilation-unit (codegen-unit) structure from a compiled
Rust binary, including fully stripped ones.

Everything you need to interpret this tool's output is in this header. It is
deliberately self-contained: no need to consult the research corpus it came
from.

Static analysis only. Never executes, loads, or unpacks the target. Reads
ELF section headers, .eh_frame, and (when present) .symtab.

    Usage:
      cgumap.py BINARY [--json OUT] [--ghidra OUT] [--min-group N] [--quiet]

===========================================================================
1. WHAT PROBLEM THIS SOLVES
===========================================================================
A stripped Rust binary presents as a few thousand anonymous functions with
no module structure. Most of them are vendored library code (core, alloc,
std, serde, regex...); usually under 10% is what the author actually wrote.
There is no standard way to tell which is which.

cgumap recovers an approximate "these functions were compiled together"
grouping. It does not recover names. What it gives you is provenance
structure: blocks of functions that share an origin, so you can triage
whole blocks instead of individual functions.

===========================================================================
2. WHY IT WORKS (the compiler mechanism)
===========================================================================
rustc splits each crate into codegen units (CGUs) -- default 16 for a
release build -- and hands them to the linker as separate object files, in
a deterministic order. Two facts make CGU membership recoverable:

  (a) Within one CGU, rustc emits items sorted by full mangled symbol name.
      This is deliberate (an LLVM cache-locality optimization), not an
      accident, and it is stable across builds.
      Source: rustc_middle/src/mono.rs, items_in_deterministic_order()
              -> items.sort_by_cached_key(|&(i, _)| i.symbol_name(tcx))

  (b) Default linkers concatenate input object files without reordering
      their contents, so each CGU's block of functions largely survives as
      a contiguous address range.

So: walking functions in address order, a *decrease* in mangled name is
evidence that one CGU's sorted block ended and another began. That single
observation is Method A. Method B is what remains usable when the names
are gone.

Confirmed linker-invariant: rebuilding one target with GNU bfd, gold, and
lld gave near-identical results (ARI within 0.03). mold untested.

===========================================================================
3. THE TWO METHODS (auto-selected by what the binary retains)
===========================================================================
METHOD A -- symbols present.
  Group address-sorted functions into runs of non-decreasing mangled name;
  each decrease starts a new group. Zero parameters, one linear pass.

METHOD B -- stripped binary.
  Partition the address-sorted .eh_frame function list into
  K = round(sqrt(n_functions)) byte-size-balanced groups. Zero parameters.
  Uses only function start/end addresses -- no names of any kind.

Method B exists because .eh_frame survives `strip --strip-all`: it is an
allocated section required at runtime for panic unwinding, unlike .symtab.
Verified byte-identical function ranges between stripped and unstripped
copies of the same binary.

===========================================================================
4. MEASURED ACCURACY -- and how it was measured
===========================================================================
Ground truth is real rustc CGU membership, not an approximation: crates
were built with `-C save-temps`, which leaves each CGU's own object file on
disk, and every function was labelled with the CGU that actually compiled
it. Scored with Adjusted Rand Index (1.0 = perfect, 0.0 = random).

Corpus: 7 targets across 4 independently linked binaries (tokei, ripgrep,
bat, fd -- plus dependency crates inside them), Cargo release defaults
(opt-level 3, codegen-units 16, thin-local LTO), x86-64 Linux.

                       ARI     homogeneity   completeness
    Method A          0.882       0.985         0.869
    Method B          0.516        --            --

Read those three numbers together:
  - homogeneity 0.985 -> a group almost never mixes two different CGUs.
    This is the property the tool is actually good at.
  - completeness 0.869 -> one true CGU is often split across several
    groups. This is the cost, and it is deliberate.
  - Method A emits roughly 1.5-2.5x more groups than there are true CGUs.

Practical reading: **trust that a group is internally consistent; do not
assume a group is a complete module.**

===========================================================================
5. WHAT IT'S WORTH TO AN ANALYST (measured, not asserted)
===========================================================================
ARI measures agreement with the compiler, which is not the same as being
useful. So the triage task was measured directly: find the author's own
code in a stripped binary.

Workflow modelled: the analyst identifies ONE author function by any
independent means (a string xref, the entry point, a panic message, a
panic-location oracle), then reads that function's whole cgumap group.

    On FULLY STRIPPED binaries (Method B, no symbols at all):
      author-code density of the returned group : 74.1%
      base rate (author fraction of the binary) :  9.3%
      ------------------------------------------------
      lift                                      :  8.0x
      author code recovered per seed            : ~11%

    Same measurement WITH symbols (Method A): 76.6% -> 8.3x lift.

Note the second number barely beats the first, even though Method A's ARI
(0.882) is far above Method B's (0.516). ARI penalizes granularity
mismatch; the analyst does not care about granularity, only about the
purity of what they are handed. In other words: **losing the symbol table
costs far less in practice than the accuracy gap suggests.**

Because ~11% of author code comes back per seed, this is a triage
accelerator that you re-seed a few times -- not a one-shot module map.

CAUTION about a bigger number you may see elsewhere: ranking groups by
true author density gives a ~10x "speedup," but that ranking requires
already knowing which groups are the author's. It is an oracle upper
bound, not a workflow. 8.0x is the deployable figure.

CAUTION #2, about what the 8.0x figure is NOT evidence of (round 33 null-
model audit, docs/ROUND33_NULL_MODEL_AUDIT.md): a null test that returns
the m functions address-nearest to the seed -- same seed, same window
size, zero cut-point logic -- matches or beats Method B's real precision
on 4 of 6 corpus targets (mean 0.751 vs 0.741). A random K-way contiguous
cut at the same granularity trails the real number by a modest but real
margin (mean 0.696). Read together: most of this lift is author code's
inherited spatial locality in a linker-contiguous binary, which ANY
comparable-granularity partition captures -- including one with no
placement logic at all. Method B's specific byte-balanced rule adds a
real but small increment over random cuts, and no measurable increment
over a naive window. The number is real and reproducible; the algorithm
is not what earns it. (Method A's homogeneity 0.985 does NOT have this
problem -- it clears a matched random-cut null on 100% of 1,200 draws --
so this caution applies to Method B's seed-propagation story specifically,
not to Method A's name-sort-reset mechanism.)

===========================================================================
6. VALIDATED ON REAL MALWARE (preconditions, not accuracy)
===========================================================================
Checked against real Rust malware rather than only cooperative crates.io
builds. Of 5 Linux ELF samples (BlackCat/ALPHV Sphynx, Akira v2, Krusty,
01flip, P2PInfect):

  - .eh_frame present in 4/4 of the Rust samples -> Method B applicable.
  - 2/4 additionally retained full symbol tables -> Method A applicable,
    exposing the operators' own crates directly.
  - none showed panic=abort (unwinding machinery present in all four).
  - P2PInfect is not Rust; its section headers are stripped entirely.

No accuracy number is claimed on malware: `-C save-temps` ground truth
cannot exist for binaries we did not build. Sample is small (n=4) and
ransomware-heavy.

===========================================================================
7. WHEN THIS FAILS / DOESN'T APPLY
===========================================================================
  - No .eh_frame (e.g. `objcopy --remove-section .eh_frame`, or some
    no_std/embedded builds): the tool cannot run at all. A partial fallback
    exists in principle -- .eh_frame_hdr's lookup table plus a call-target
    scan recovers roughly half of function starts -- but is NOT implemented
    here.
  - Windows PE: untested. The analogous unwind data is .pdata/.xdata; no
    parser here.
  - Fat / whole-program LTO: collapses true CGU count to ~2, so there is
    little structure left to recover regardless of method.
  - Very small crates: rustc merges small CGUs, so a crate may genuinely
    have only one, making the output trivially correct but useless.
  - Non-Rust binaries: Method B will still emit size-balanced groups, but
    the premise (name-sorted CGU emission) does not hold, so the output is
    meaningless. Check that the target is actually Rust first.

===========================================================================
8. THINGS THAT WERE TRIED AND DID NOT WORK
===========================================================================
Recorded so they are not re-attempted:

  - Padding/alignment bytes between functions as a boundary signal: dead.
    Every inter-function gap is 0xCC fill, identical within and across CGU
    boundaries.
  - Patience sorting / minimum-pile decomposition of the name sequence:
    much worse than the simple greedy reset in Method A. Minimizing pile
    count merges different CGUs whose name ranges happen to be compatible.
  - Merging Method A's groups down toward the true CGU count, using
    demangled-label similarity or call-graph edges: no meaningful gain
    (+0.003 ARI). Demangled labels mostly encode the generic *type* a
    function operates on (e.g. alloc::vec::Vec<...>), not the crate that
    compiled it, so they cannot discriminate *within* one crate's CGUs.
  - `-Z codegen_source_order` (a real nightly rustc flag that emits items
    in source-span order): made results worse, because generic
    instantiations defined outside the crate have no local span and fall
    back to name order anyway, fracturing the sequence.
  - ELF local linkage (`nm` lowercase 't') as a hard same-CGU guarantee:
    false. Thin-local LTO treats a crate's CGUs as one optimization unit,
    so local symbols are freely called across CGU boundaries (37-64%
    counterexample rate).

===========================================================================
9. OUTPUT
===========================================================================
  text    : one line per group -- address range, function count, byte size,
            and (Method A only) the dominant crate/module label.
  --json  : full grouping incl. per-function addresses, sizes, names.
  --ghidra: a generated Jython script that colorizes each group and drops a
            bookmark at each group start. Run it from Ghidra's Script
            Manager with the same binary loaded.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

try:
    from elftools.elf.elffile import ELFFile
except ImportError:
    sys.exit("cgumap: requires pyelftools  (pip install pyelftools)")

# Rust mangling: legacy  _ZN...17h<16 hex>E   |   v0  _R[NIYQCMFXG]...
RUST_LEGACY = re.compile(r"^_ZN.*17h[0-9a-f]{16}E$")
RUST_V0 = re.compile(r"^_R[NIYQCMFXGKS]")


@dataclass
class Func:
    start: int
    end: int
    name: str | None = None
    demangled: str | None = None

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass
class Group:
    gid: int
    funcs: list[Func] = field(default_factory=list)

    @property
    def start(self) -> int:
        return min(f.start for f in self.funcs)

    @property
    def end(self) -> int:
        return max(f.end for f in self.funcs)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.funcs)

    def dominant_label(self, depth: int = 1) -> str | None:
        labels = [label_of(f.demangled, depth) for f in self.funcs if f.demangled]
        labels = [x for x in labels if x]
        if not labels:
            return None
        return Counter(labels).most_common(1)[0][0]


def label_of(demangled: str | None, depth: int = 1) -> str | None:
    if not demangled:
        return None
    s = demangled
    if s.startswith("<"):
        s = s[1:]
    segs = s.split("::")
    return "::".join(segs[:depth]) if segs else None


# ---------------------------------------------------------------- ELF input

def read_fdes(path: str) -> list[Func]:
    """Function [start,end) ranges from .eh_frame. Survives strip --strip-all,
    since .eh_frame is an allocated section required for unwinding.

    This is the tool's hard precondition: no .eh_frame, no cgumap. Verified
    to produce byte-identical function sets on stripped vs unstripped
    copies of the same binary, and cross-checked against an independent
    Rust/gimli implementation on three binaries (zero differences).

    Duplicate start addresses are dropped keeping the first. That matters:
    the linker emits aliased symbol names at a single address (100-400 per
    binary is typical), and counting each alias as its own function
    corrupts the name-order sequence Method A depends on.
    """
    with open(path, "rb") as fh:
        elf = ELFFile(fh)
        if not elf.has_dwarf_info():
            return []
        dw = elf.get_dwarf_info()
        try:
            entries = list(dw.EH_CFI_entries())
        except Exception:
            return []
        out = []
        for e in entries:
            hdr = getattr(e, "header", None)
            if hdr is None:
                continue
            loc = getattr(hdr, "initial_location", None)
            rng = getattr(hdr, "address_range", None)
            if loc is None or rng is None or rng == 0:
                continue
            out.append(Func(start=loc, end=loc + rng))
    out.sort(key=lambda f: f.start)
    # drop exact-duplicate starts (keep first)
    dedup, seen = [], set()
    for f in out:
        if f.start in seen:
            continue
        seen.add(f.start)
        dedup.append(f)
    return dedup


def read_symbols(path: str) -> dict[int, str]:
    """addr -> first-seen mangled name, from .symtab (t/T only).
    First-seen dedup matters: the linker emits aliased names at one address,
    and counting each alias separately corrupts downstream segmentation."""
    try:
        out = subprocess.run(["nm", path], capture_output=True, text=True, timeout=180).stdout
    except Exception:
        return {}
    addr_name: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        addr, typ, name = parts
        if typ not in ("t", "T"):
            continue
        try:
            a = int(addr, 16)
        except ValueError:
            continue
        addr_name.setdefault(a, name)
    return addr_name


def demangle_all(names: list[str]) -> list[str]:
    if not names:
        return []
    try:
        p = subprocess.run(["rustfilt"], input="\n".join(names),
                           capture_output=True, text=True, timeout=180)
        if p.returncode == 0:
            lines = p.stdout.splitlines()
            if len(lines) == len(names):
                return lines
    except Exception:
        pass
    return names  # rustfilt absent -> labels degrade, grouping unaffected


def is_rust_symbol(name: str) -> bool:
    return bool(RUST_LEGACY.match(name) or RUST_V0.match(name))


# ---------------------------------------------------------------- methods

def method_a_symbols(funcs: list[Func]) -> list[Group]:
    """Runs of non-decreasing mangled name over address-sorted functions.
    Each decrease starts a new group (a CGU boundary candidate).

    Measured: ARI 0.882 / homogeneity 0.985 / completeness 0.869.

    This is the whole algorithm -- deliberately. Nothing here is tuned,
    and several more sophisticated variants were tested and lost to it
    (agglomerative merging on label or call-graph similarity, patience
    sorting, non-contiguous graph clustering). Resist the urge to add a
    merge step: the reason it doesn't help is that demangled names encode
    the generic *type* a function works on, not the crate that compiled
    it, so there is no within-crate signal left to merge on.

    The over-segmentation is a feature given the intended use: high
    purity, incomplete coverage. See header section 4.
    """
    groups, cur, prev = [], [], None
    gid = 0
    for f in funcs:
        nm = f.name or ""
        if prev is not None and nm < prev:
            groups.append(Group(gid, cur))
            gid += 1
            cur = []
        cur.append(f)
        prev = nm
    if cur:
        groups.append(Group(gid, cur))
    return groups


def method_b_stripped(funcs: list[Func]) -> list[Group]:
    """K = round(sqrt(n)) byte-size-balanced contiguous groups.

    Measured: ARI 0.516 -- well below Method A, but see header section 5:
    on the actual triage task the practical gap is small (74.1% vs 76.6%
    seed precision), because ARI punishes granularity mismatch and an
    analyst does not.

    Two parameter choices, neither tuned on this corpus:
      - K = sqrt(n) is the standard default-cluster-count heuristic,
        chosen a priori. An oracle sweep over K (picking the best value
        using ground truth) reaches only ~0.509 mean, so K selection is
        NOT the bottleneck -- the limit is that real CGUs are not perfectly
        contiguous.
      - byte-size balancing rather than equal function count: worth about
        +0.06 ARI. Deriving K from an absolute byte threshold instead
        (e.g. "1 MB per CGU") was tested and fails badly -- real mean CGU
        size here is ~165 KB, not 1 MB.
    """
    n = len(funcs)
    if n == 0:
        return []
    K = max(1, round(math.sqrt(n)))
    total = sum(f.size for f in funcs)
    target = total / K if K else total
    groups, cur = [], []
    gid, cum = 0, 0
    for f in funcs:
        cur.append(f)
        cum += f.size
        while gid < K - 1 and cum >= (gid + 1) * target:
            groups.append(Group(gid, cur))
            cur = []
            gid += 1
    if cur:
        groups.append(Group(gid, cur))
    # A single very large function can cross several size thresholds at once,
    # which emits empty groups and leaves gaps in the id sequence. Drop the
    # empties and renumber so ids are contiguous (matters for the JSON/Ghidra
    # consumers, which treat the id as an index).
    kept = [g for g in groups if g.funcs]
    for i, g in enumerate(kept):
        g.gid = i
    return kept


# ---------------------------------------------------------------- output

def emit_text(groups: list[Group], method: str, meta: dict, min_group: int) -> None:
    print(f"\ncgumap  --  {meta['path']}")
    print(f"  {meta['n_funcs']} functions from .eh_frame"
          + (f", {meta['n_named']} with symbols" if meta["n_named"] else " (stripped)"))
    print(f"  method: {method}")
    print(f"  recovered {len(groups)} groups"
          + (f" ({sum(1 for g in groups if len(g.funcs) >= min_group)} with >= {min_group} functions)"
             if min_group > 1 else ""))
    print()
    hdr = f"{'group':>5}  {'start':>12}  {'end':>12}  {'funcs':>6}  {'bytes':>8}  label"
    print(hdr)
    print("-" * len(hdr))
    for g in groups:
        if len(g.funcs) < min_group:
            continue
        lbl = g.dominant_label() or ""
        print(f"{g.gid:>5}  {g.start:>#12x}  {g.end:>#12x}  {len(g.funcs):>6}  "
              f"{g.total_size:>8}  {lbl[:44]}")


def emit_json(groups: list[Group], method: str, meta: dict, path: str) -> None:
    doc = {
        "tool": "cgumap",
        "binary": meta["path"],
        "method": method,
        "n_functions": meta["n_funcs"],
        "n_named": meta["n_named"],
        "n_groups": len(groups),
        "groups": [
            {
                "id": g.gid,
                "start": hex(g.start),
                "end": hex(g.end),
                "n_funcs": len(g.funcs),
                "total_bytes": g.total_size,
                "dominant_label": g.dominant_label(),
                "functions": [
                    {"addr": hex(f.start), "size": f.size, "name": f.name, "demangled": f.demangled}
                    for f in g.funcs
                ],
            }
            for g in groups
        ],
    }
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2)
    print(f"\nwrote JSON -> {path}")


GHIDRA_TEMPLATE = '''# cgumap -> Ghidra: colorize + bookmark recovered compilation-unit groups.
# Run in Ghidra's Script Manager (Jython) with the same binary loaded.
# Generated by cgumap; groups are approximate CGU boundaries, see tool docs.
from java.awt import Color

GROUPS = {groups!r}

PALETTE = [Color(0xE8,0xF4,0xFD), Color(0xFD,0xEF,0xE8), Color(0xE9,0xF9,0xF0),
           Color(0xFD,0xF8,0xE3), Color(0xF6,0xEC,0xFB), Color(0xEC,0xF3,0xE8)]

svc = state.getTool().getService(
    ghidra.app.services.ColorizingService) if state.getTool() else None
fm = currentProgram.getFunctionManager()
af = currentProgram.getAddressFactory().getDefaultAddressSpace()

for i, g in enumerate(GROUPS):
    color = PALETTE[i % len(PALETTE)]
    start = af.getAddress(g["start"])
    createBookmark(start, "cgumap", "group %d (%d funcs) %s"
                   % (g["id"], g["n_funcs"], g.get("label") or ""))
    for fa in g["funcs"]:
        addr = af.getAddress(fa)
        fn = fm.getFunctionAt(addr)
        if fn is not None and svc is not None:
            svc.setBackgroundColor(fn.getBody(), color)
print("cgumap: applied %d groups" % len(GROUPS))
'''


def emit_ghidra(groups: list[Group], path: str, min_group: int) -> None:
    payload = [
        {
            "id": g.gid,
            "start": hex(g.start),
            "n_funcs": len(g.funcs),
            "label": g.dominant_label(),
            "funcs": [hex(f.start) for f in g.funcs],
        }
        for g in groups
        if len(g.funcs) >= min_group
    ]
    with open(path, "w") as fh:
        fh.write(GHIDRA_TEMPLATE.format(groups=payload))
    print(f"wrote Ghidra script -> {path}")


# ---------------------------------------------------------------- driver

def run(path: str, min_group: int = 1):
    funcs = read_fdes(path)
    if not funcs:
        return None, None, {"path": path, "n_funcs": 0, "n_named": 0,
                            "error": "no .eh_frame FDEs -- cgumap cannot run on this binary"}

    addr_name = read_symbols(path)
    n_rust = sum(1 for n in addr_name.values() if is_rust_symbol(n))
    use_symbols = n_rust >= max(20, 0.05 * len(funcs))

    n_named = 0
    if use_symbols:
        for f in funcs:
            nm = addr_name.get(f.start)
            if nm:
                f.name = nm
                n_named += 1
        named = [f for f in funcs if f.name]
        dem = demangle_all([f.name for f in named])
        for f, d in zip(named, dem):
            f.demangled = d

    if use_symbols:
        groups = method_a_symbols(funcs)
        method = "A (symbol sort-order-reset; corpus mean ARI 0.883, homogeneity 0.985)"
    else:
        groups = method_b_stripped(funcs)
        method = "B (stripped, K=sqrt(n) size-balanced; corpus mean ARI 0.516)"

    meta = {"path": path, "n_funcs": len(funcs), "n_named": n_named, "n_rust_syms": n_rust}
    return groups, method, meta


def main():
    ap = argparse.ArgumentParser(description="Recover Rust codegen-unit structure from a binary.")
    ap.add_argument("binary")
    ap.add_argument("--json", help="write full grouping as JSON")
    ap.add_argument("--ghidra", help="write a Ghidra colorize/bookmark script")
    ap.add_argument("--min-group", type=int, default=1,
                    help="only display/export groups with at least N functions")
    ap.add_argument("--quiet", action="store_true", help="suppress the text table")
    args = ap.parse_args()

    groups, method, meta = run(args.binary, args.min_group)
    if groups is None:
        print(f"cgumap: {meta['error']}", file=sys.stderr)
        sys.exit(2)

    if not args.quiet:
        emit_text(groups, method, meta, args.min_group)
    if args.json:
        emit_json(groups, method, meta, args.json)
    if args.ghidra:
        emit_ghidra(groups, args.ghidra, args.min_group)


if __name__ == "__main__":
    main()
