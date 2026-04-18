"""aggregate pass/fail rates across multiple pytest junitxml runs.

usage: python3 evals/aggregate.py <xml>...
"""
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

PASS, FAIL = "pass", "fail"


def classify(tc):
    for child in tc:
        tag = child.tag.split("}")[-1]
        if tag in ("failure", "error"):
            return FAIL
        if tag == "skipped":
            return None
    return PASS


def load(path):
    """return {test_id: pass|fail} for one xml file."""
    out = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        tid = f"{tc.get('classname')}::{tc.get('name')}"
        verdict = classify(tc)
        if verdict is not None:
            out[tid] = verdict
    return out


def main(paths):
    runs = [load(p) for p in paths]
    n = len(runs)
    counts = defaultdict(lambda: [0, 0])  # test_id -> [pass, fail]
    for run in runs:
        for tid, verdict in run.items():
            counts[tid][0 if verdict == PASS else 1] += 1

    flaky, always_fail, always_pass = [], [], []
    for tid, (p, f) in counts.items():
        if f == 0:
            always_pass.append(tid)
        elif p == 0:
            always_fail.append(tid)
        else:
            flaky.append((tid, p, f))

    print(f"\n=== aggregate across {n} run(s), {len(counts)} unique tests ===\n")
    if always_fail:
        print(f"always fail ({len(always_fail)}):")
        for tid in sorted(always_fail):
            print(f"  {tid}")
        print()
    if flaky:
        print(f"flaky ({len(flaky)}):")
        for tid, p, f in sorted(flaky, key=lambda x: (x[2], x[0]), reverse=True):
            print(f"  {p}/{p+f} pass  {tid}")
        print()
    total_runs = sum(p + f for p, f in counts.values())
    total_pass = sum(p for p, _ in counts.values())
    rate = 100.0 * total_pass / total_runs if total_runs else 0.0
    print(f"overall: {total_pass}/{total_runs} pass ({rate:.1f}%), "
          f"{len(always_pass)} always-pass, {len(flaky)} flaky, {len(always_fail)} always-fail")
    return 1 if always_fail or flaky else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
