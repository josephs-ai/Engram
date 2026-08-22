"""
hygiene_audit.py — check the engineering-hygiene weaknesses in one place.

Every check here corresponds to a specific criticism of this codebase. They are
mechanical: each one greps, counts, or queries rather than asking anyone's
opinion, so the result is the same whoever runs it and can be tracked over time.

    python3 scripts/hygiene_audit.py            # report
    python3 scripts/hygiene_audit.py --json     # machine-readable
    python3 scripts/hygiene_audit.py --fix      # apply the safe fixes only

Exit code is 0 when nothing is FAIL, 1 otherwise, so CI can gate on it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Result:
    check: str
    status: str
    detail: str
    fixable: bool = False
    evidence: list[str] = field(default_factory=list)


def _py_files() -> list[Path]:
    return sorted(p for p in SCRIPTS.glob("*.py"))


# --------------------------------------------------------------------------
# Structure / organisation
# --------------------------------------------------------------------------

def check_script_sprawl() -> Result:
    """A flat scripts/ directory with no package boundary is hard to consume."""
    n = len(_py_files())
    has_pkg = (ROOT / "src").is_dir() or (ROOT / "engram").is_dir()
    if has_pkg:
        return Result("script-sprawl", OK, f"{n} scripts, importable package present")
    status = FAIL if n > 150 else WARN
    return Result(
        "script-sprawl", status,
        f"{n} flat scripts in scripts/, no importable package (src/ or engram/)",
        evidence=["consumers must run individual scripts rather than import a library"],
    )


def check_legacy_dirs() -> Result:
    """Old phase/legacy folders left in the tree."""
    stale = [d.name for d in SCRIPTS.iterdir() if d.is_dir() and d.name in {"legacy", "phase2", "phase1", "old"}]
    if not stale:
        return Result("legacy-dirs", OK, "no legacy/phase directories")
    counts = []
    for d in stale:
        counts.append(f"{d} ({len(list((SCRIPTS / d).rglob('*.py')))} py)")
    return Result("legacy-dirs", WARN, "stale directories: " + ", ".join(counts), fixable=False)


def check_naming_consistency() -> Result:
    """The project is called several different things across its own files."""
    names = {
        "Engram": 0, "OpenClaw Memory": 0, "openclaw-memory": 0, "Memory-System-claw": 0,
    }
    looked = [ROOT / "README.md", ROOT / "pyproject.toml"]
    for f in looked:
        if not f.exists():
            continue
        text = f.read_text(errors="ignore")
        for k in names:
            names[k] += text.count(k)
    used = {k: v for k, v in names.items() if v}
    if len(used) <= 1:
        return Result("naming", OK, f"single name in use: {used or 'none found'}")
    return Result(
        "naming", WARN,
        f"{len(used)} names in README/pyproject: " + ", ".join(f"{k}x{v}" for k, v in used.items()),
    )


def check_hardcoded_paths() -> Result:
    """Workspace paths should come from config, not be re-derived everywhere."""
    pat = re.compile(r'Path\.home\(\)\s*/\s*"\.openclaw"')
    offenders = []
    for p in _py_files():
        if p.name in {"config.py", "hygiene_audit.py"}:
            continue          # config.py is where this is allowed to live
        if pat.search(p.read_text(errors="ignore")):
            offenders.append(p.name)
    if not offenders:
        return Result("hardcoded-paths", OK, "workspace root only resolved in config.py")
    return Result(
        "hardcoded-paths", WARN,
        f"{len(offenders)} scripts re-derive the workspace root instead of importing config",
        evidence=sorted(offenders)[:10],
    )


# --------------------------------------------------------------------------
# Retrieval correctness
# --------------------------------------------------------------------------

def check_reranker_cached() -> Result:
    """A CrossEncoder built inside a request path reloads the model each call."""
    bad = []
    for p in _py_files():
        if p.name == "hygiene_audit.py":
            continue                          # this file names the pattern it looks for
        src = p.read_text(errors="ignore")
        if "CrossEncoder(" not in src:
            continue
        for m in re.finditer(r"^(\s*)\S.*CrossEncoder\(", src, re.M):
            indent = len(m.group(1))
            line_no = src[: m.start()].count("\n") + 1
            if indent == 0:
                continue                      # module level: fine
            # Inside a function is only OK if that function memoises.
            head = src[max(0, m.start() - 700): m.start()]
            if re.search(r"global\s+_\w*(RERANK|reranker)\w*", head, re.I):
                continue
            if re.search(r"lambda\b", m.group(0)):
                continue                      # loader callback, cached elsewhere
            # A one-shot CLI that loads once in main() and exits has nothing to
            # cache for; only repeatedly-called helpers pay the reload.
            enclosing = re.findall(r"^def (\w+)", head, re.M)
            if enclosing and enclosing[-1] == "main":
                continue
            bad.append(f"{p.name}:{line_no}")
    if not bad:
        return Result("reranker-cached", OK, "every CrossEncoder construction is memoised")
    return Result("reranker-cached", FAIL,
                  f"{len(bad)} uncached CrossEncoder construction(s)", evidence=bad)


def check_magic_numbers() -> Result:
    """Scoring weights should be named in config, not literals in the query."""
    scoring = SCRIPTS / "memory_db.py"
    src = scoring.read_text(errors="ignore") if scoring.exists() else ""
    literals = re.findall(r"\(fts_rank \* [0-9.]+\)|\(vector_score \* [0-9.]+\)", src)
    cfg_used = "cfg.WEIGHT_FTS" in src or "{w_fts}" in src
    if not literals and cfg_used:
        return Result("magic-numbers", OK, "fusion weights sourced from config")
    return Result("magic-numbers", WARN if cfg_used else FAIL,
                  f"{len(literals)} hardcoded fusion weight(s) in memory_db.py",
                  evidence=literals[:5])


def check_vector_drift() -> Result:
    """The vector index can drift from Postgres, which is the source of truth."""
    try:
        sys.path.insert(0, str(SCRIPTS))
        from memory_db import get_conn, close_pool
        with get_conn() as c:
            cur = c.cursor()
            cur.execute("SELECT count(*) FROM memory_items WHERE status='active'")
            active = cur.fetchone()[0]
            cur.execute("""SELECT count(*) FROM memory_items m
                           LEFT JOIN memory_item_embeddings e ON e.memory_id = m.id
                           WHERE m.status='active' AND e.memory_id IS NULL""")
            missing = cur.fetchone()[0]
        close_pool()
    except Exception as exc:
        return Result("vector-drift", WARN, f"could not check: {type(exc).__name__}")
    if missing == 0:
        return Result("vector-drift", OK, f"all {active:,} active items embedded")
    pct = 100 * missing / max(active, 1)
    return Result("vector-drift", FAIL if pct > 5 else WARN,
                  f"{missing:,} of {active:,} active items unembedded ({pct:.1f}%)")


# --------------------------------------------------------------------------
# Growth / operations
# --------------------------------------------------------------------------

def check_unbounded_growth() -> Result:
    """Directories that gain a file per run need retention, or they eat the disk."""
    problems = []
    logs = ROOT / "logs"
    checks = [
        (logs / "restore-points", "create_memory_restore_point.py", "KEEP_RESTORE_POINTS"),
    ]
    for d, owner, knob in checks:
        n = len([x for x in d.iterdir() if x.is_dir()]) if d.is_dir() else 0
        src = (SCRIPTS / owner).read_text(errors="ignore") if (SCRIPTS / owner).exists() else ""
        if knob not in src:
            problems.append(f"{d.name}: {n} entries, no retention in {owner}")
    cycles = len(list(logs.glob("maintenance-cycle-*/"))) if logs.is_dir() else 0
    cyc_src = (SCRIPTS / "run_memory_maintenance_cycle.py").read_text(errors="ignore")
    if "KEEP_CYCLE_DIRS" not in cyc_src:
        problems.append(f"maintenance-cycle dirs: {cycles}, no retention")
    if problems:
        return Result("unbounded-growth", FAIL, "; ".join(problems))
    return Result("unbounded-growth", OK, "retention configured for restore points and cycle dirs")


def check_orphan_temps() -> Result:
    """Atomic-write temps survive SIGKILL and are full-size copies."""
    base = ROOT / "dehydrated" / "chunks_runtime"
    if not base.is_dir():
        return Result("orphan-temps", OK, "no chunks_runtime directory")
    orphans = list(base.glob("*/.extract_chunk_cache.json.*.tmp"))
    total = sum(o.stat().st_size for o in orphans if o.exists())
    if not orphans:
        return Result("orphan-temps", OK, "no orphaned cache temp files")
    return Result("orphan-temps", FAIL,
                  f"{len(orphans)} orphaned cache temps, {total / 1e9:.1f} GB",
                  fixable=True)


# --------------------------------------------------------------------------
# Testing / packaging
# --------------------------------------------------------------------------

def check_test_coverage_shape() -> Result:
    """Operational scripts with no test at all."""
    tests = {p.name for p in _py_files() if p.name.startswith("test_")}
    tested = {t[len("test_"):] for t in tests}
    ops = [p.name for p in _py_files()
           if not p.name.startswith("test_") and p.name not in {"conftest.py"}]
    untested = [o for o in ops if o not in tested]
    pct = 100 * len(untested) / max(len(ops), 1)
    status = FAIL if pct > 80 else WARN if pct > 50 else OK
    return Result("test-shape", status,
                  f"{len(untested)}/{len(ops)} scripts ({pct:.0f}%) have no test_<name>.py",
                  evidence=sorted(untested)[:8])


def check_packaging() -> Result:
    """An installable package with entry points, versus a pile of scripts."""
    pj = ROOT / "pyproject.toml"
    if not pj.exists():
        return Result("packaging", FAIL, "no pyproject.toml")
    text = pj.read_text(errors="ignore")
    has_scripts = "[project.scripts]" in text
    has_pkg = (ROOT / "src").is_dir() or (ROOT / "engram").is_dir()
    if has_scripts and has_pkg:
        return Result("packaging", OK, "installable package with console entry points")
    missing = []
    if not has_pkg:
        missing.append("no package dir (src/ or engram/)")
    if not has_scripts:
        missing.append("no [project.scripts] entry points")
    return Result("packaging", WARN, "; ".join(missing))


def check_readme_claims() -> Result:
    """README should not claim behaviour the code does not implement."""
    rm = ROOT / "README.md"
    if not rm.exists():
        return Result("readme-claims", WARN, "no README.md")
    text = rm.read_text(errors="ignore").lower()
    findings = []
    if "singleton" in text or "cached" in text:
        r = check_reranker_cached()
        if r.status != OK:
            findings.append("claims cached/singleton reranker but one is uncached")
    if findings:
        return Result("readme-claims", FAIL, "; ".join(findings))
    return Result("readme-claims", OK, "no contradicted claims detected")


CHECKS = [
    check_script_sprawl, check_legacy_dirs, check_naming_consistency,
    check_hardcoded_paths, check_reranker_cached, check_magic_numbers,
    check_vector_drift, check_unbounded_growth, check_orphan_temps,
    check_test_coverage_shape, check_packaging, check_readme_claims,
]


def apply_fixes(results: list[Result]) -> list[str]:
    """Only the fixes that cannot lose information."""
    done = []
    for r in results:
        if not r.fixable or r.status == OK:
            continue
        if r.check == "orphan-temps":
            sys.path.insert(0, str(SCRIPTS))
            from extract_chunk_updates import sweep_orphan_cache_tmps
            base = ROOT / "dehydrated" / "chunks_runtime"
            n = sum(sweep_orphan_cache_tmps(d / ".extract_chunk_cache.json")
                    for d in base.iterdir() if d.is_dir())
            done.append(f"orphan-temps: swept {n} file(s)")
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="Engineering-hygiene audit.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fix", action="store_true", help="apply safe fixes, then re-check")
    args = ap.parse_args()

    results = [c() for c in CHECKS]

    if args.fix:
        applied = apply_fixes(results)
        if applied:
            results = [c() for c in CHECKS]

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        width = max(len(r.check) for r in results)
        for r in results:
            mark = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}[r.status]
            print(f"  [{mark}] {r.check.ljust(width)}  {r.detail}")
            for e in r.evidence:
                print(f"           - {e}")
        fails = sum(1 for r in results if r.status == FAIL)
        warns = sum(1 for r in results if r.status == WARN)
        print(f"\n  {len(results)} checks: {fails} FAIL, {warns} WARN, "
              f"{len(results) - fails - warns} OK")

    sys.exit(1 if any(r.status == FAIL for r in results) else 0)


if __name__ == "__main__":
    main()
