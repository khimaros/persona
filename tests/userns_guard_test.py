#!/usr/bin/env python3
"""the container refuses to boot when it would chown the HOST's /work and /data away from them.

WHY THERE IS ONE AT ALL: docker-compose.yml bind-mounts ./work and ./data, host directories in
the deployment. The base image's `hmux-drop` runs `chown -R hmux:hmux /work /data` whenever it
starts as root, and under ROOTLESS podman `hmux` (uid 1000 in the container) is a SUBUID on the
host -- 100999, not you. So a start without `--userns=keep-id:uid=1000,gid=1000 --user=1000:1000`
rewrites the whole tree to a uid the host cannot read, recoverable only via `podman unshare`.
The failure is silent and reads as the agent having lost its memory, which is why a refusal beats
any amount of documentation. Production runs podman-compose 1.0.3, which ignores `user:` and
`userns_mode:` in the compose file, so those flags are passed as --podman-run-args and are exactly
the thing somebody forgets.

THE GUARD FIRES ON THREE SIGNALS TOGETHER, and the third is what keeps it honest:
  - we are root, and
  - the userns is rootless (uid_map maps container 0 to a NON-ZERO host uid), and
  - /work is a BIND MOUNT, not a named volume.
Drop the third and this would refuse the README's own standalone `podman run -v persona-work:/work`
recipe, where the chown lands inside podman's storage and harms nobody.

TWO KINDS OF CHECK, as in display_test.py, because they see different things:

  RECIPE -- what the entrypoint and the compose files SAY. cheap, always runs, and cannot tell
  you whether the image was ever rebuilt from them.

  ARTIFACT -- what the IMAGE DOES, asked by starting it three ways and looking at what survived.
  this is the half that catches a guard nobody built. it SKIPS when the image predates the
  entrypoint, because "you have not rebuilt yet" is not a defect.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker-entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
COMPOSE_EVAL = ROOT / "docker-compose.eval.yml"
IMAGE = os.environ.get("PERSONA_IMAGE", "khimaros/persona:latest")
# the marker the guard prints. asserted from BOTH halves, so the message and the test cannot
# drift apart the way a prose-only contract does.
REFUSAL = "persona: refusing to start"
# what the operator has to be told to do, since being told only "no" is a worse bug than the one
# this prevents.
MUST_NAME = ["--userns", "keep-id", "--user"]
PASS = FAIL = 0
SKIPPED = []


def skip(reason):
    SKIPPED.append(reason)
    print(f"SKIP: {reason}")


def check(desc, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS: {desc}")
    else:
        FAIL += 1
        print(f"FAIL: {desc}")
        if detail:
            print(f"  {detail}")


# --- recipe: what the files say ---------------------------------------------------------------

def recipe_checks():
    entry = ENTRYPOINT.read_text()
    compose = COMPOSE.read_text()
    compose_eval = COMPOSE_EVAL.read_text()

    # the state this protects. if these ever go back to named volumes the guard is pointless, and
    # a guard that protects nothing is worse than none -- it reads as cover.
    check("compose bind-mounts ./work and ./data",
          "./work:/work" in compose and "./data:/data" in compose,
          "the guard exists because /work and /data are HOST directories; named volumes need none")
    check("the eval still replaces them with named volumes",
          "persona-work:/work" in compose_eval and "persona-data:/data" in compose_eval,
          "without this an eval run writes the live workspace")

    check("the entrypoint refuses rather than repairs", REFUSAL in entry,
          "auto-correcting the uid mapping from inside the container is not possible; say no")
    for flag in MUST_NAME:
        check(f"the refusal names `{flag}`", flag in entry,
              "an operator who reads only this message must be able to fix it")

    # ORDER IS THE WHOLE POINT: hmux-drop does the chown, so a guard that runs after the handoff
    # has already lost. asserted positionally, and against the EXEC rather than any mention of the
    # name -- the first draft matched the guard's own comment explaining what hmux-drop does, which
    # put "the handoff" above the guard and reddened a correct implementation.
    guard_at = entry.find(REFUSAL)
    handoff = next((i for i, line in enumerate(entry.splitlines())
                    if line.startswith("exec ") and "hmux-drop" in line), None)
    drop_at = -1 if handoff is None else len("\n".join(entry.splitlines()[:handoff]))
    check("the guard runs BEFORE the handoff to hmux-drop",
          guard_at != -1 and drop_at != -1 and guard_at < drop_at,
          "hmux-drop is what chowns; refusing after it has run protects nothing")

    # the three signals, each named in the file it is read from.
    check("the guard reads the userns mapping", "uid_map" in entry,
          "rootful docker maps 0 -> 0 and must keep chowning + dropping; only rootless is the trap")
    check("the guard distinguishes a bind mount from a named volume", "mountinfo" in entry,
          "without this it refuses the README's own `podman run -v persona-work:/work` recipe")

    # and the image must not have quietly acquired a USER, which would change which branch of
    # hmux-drop runs and silently make this whole guard dead code.
    check("the image still starts as root, so the guard is reachable",
          not any(line.strip().startswith("USER ") for line in DOCKERFILE.read_text().splitlines()),
          "a USER directive would skip hmux-drop's chown branch and this guard with it")


# --- artifact: what the image does -------------------------------------------------------------

def cli():
    return shutil.which("podman") or shutil.which("docker")


def image_predates_recipe(runtime):
    out = subprocess.run([runtime, "image", "inspect", IMAGE, "-f", "{{json .Created}}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    created = json.loads(out.stdout.strip() or '""')
    if not created:
        return None
    try:
        built = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None
    recipe = datetime.fromtimestamp(ENTRYPOINT.stat().st_mtime, tz=timezone.utc)
    return built < recipe


KEEP_ID = ["--user=1000:1000", "--userns=keep-id:uid=1000,gid=1000"]


def run_image(runtime, extra_args, mounts, cmd="true"):
    """start the container for real and return (exit code, combined output)."""
    argv = [runtime, "run", "--rm", *extra_args]
    for src, dst in mounts:
        argv += ["-v", f"{src}:{dst}"]
    # a forwarded DISPLAY takes the entrypoint's "already have a screen" branch, so no Xvfb has to
    # come up for a check that is not about the display.
    argv += ["-e", "DISPLAY=:0", IMAGE, "sh", "-c", cmd]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    return out.returncode, out.stdout + out.stderr


def fresh_state(runtime, tmp, name):
    """a private ./work + ./data pair for ONE case.

    per-case AND NOT SHARED, because the very failure under test rewrites their ownership: the
    first draft reused one pair, case A's chown left /data owned by a subuid, and case B then
    failed on `ln: Permission denied` -- a red that had nothing to do with what B asserts. a case
    that can be poisoned by an earlier one is not evidence about itself.
    """
    base = tmp / name
    work, data = base / "work", base / "data"
    for d in (work, data):
        d.mkdir(parents=True, exist_ok=True)
    (work / "marker").write_text("")
    return work, data, [(work, "/work"), (data, "/data")]


def reclaim(runtime, tmp):
    """take back anything a run chowned to a subuid, so the temp dir can be removed at all.

    scoped to the directory THIS test made. inside `podman unshare` uid 0 is the invoking user, so
    chowning to 0 is the inverse of the mapping that locked us out -- the same recovery an operator
    would run after hitting this for real.
    """
    if runtime.endswith("podman"):
        subprocess.run([runtime, "unshare", "chown", "-R", "0:0", str(tmp)],
                       capture_output=True)


def artifact_checks(tmp):
    runtime = cli()
    if not runtime:
        skip("no podman/docker; the image was not asked anything")
        return
    predates = image_predates_recipe(runtime)
    if predates is None:
        skip(f"no local {IMAGE}; the guard was not exercised for real")
        return
    if predates:
        skip(f"{IMAGE} predates docker-entrypoint.sh; rebuild for the artifact half to mean anything")
        return
    if runtime.endswith("docker"):
        skip("rootful docker maps 0 -> 0, so the rootless trap cannot be reproduced here")
        return

    # A. the trap: rootless, no flags, host directories. must refuse, and must leave them alone.
    work, _, mounts = fresh_state(runtime, tmp, "a")
    own = (work / "marker").stat().st_uid
    code, out = run_image(runtime, [], mounts)
    check("A: a flagless rootless start is REFUSED", code != 0,
          f"exit {code}; it would have chowned {work} to a subuid")
    check("A: the refusal says what to do",
          REFUSAL in out and all(f in out for f in MUST_NAME),
          f"got: {out.strip()[-400:]!r}")
    marker = work / "marker"
    still = marker.stat().st_uid if marker.exists() else None
    check("A: the host directory is untouched", still == own,
          f"marker went from uid {own} to {still} -- the chown ran anyway")

    # B. the supported path. the guard must be silent, or it is just an outage.
    _, _, mounts = fresh_state(runtime, tmp, "b")
    code, out = run_image(runtime, KEEP_ID, mounts)
    check("B: keep-id + user:1000 still boots", code == 0 and REFUSAL not in out,
          f"exit {code}: {out.strip()[-400:]!r}")

    # C. THE README'S OWN RECIPE, flagless but on a NAMED VOLUME, where the chown is harmless.
    # this is the check that stops the guard from being a blunt "root is forbidden".
    vol = "persona-userns-guard-test"
    subprocess.run([runtime, "volume", "rm", "-f", vol], capture_output=True)
    subprocess.run([runtime, "volume", "create", vol], capture_output=True, check=True)
    vol_data = f"{vol}-data"
    subprocess.run([runtime, "volume", "rm", "-f", vol_data], capture_output=True)
    subprocess.run([runtime, "volume", "create", vol_data], capture_output=True, check=True)
    try:
        code, out = run_image(runtime, [], [(vol, "/work"), (vol_data, "/data")])
        check("C: a flagless start on NAMED VOLUMES is allowed", code == 0 and REFUSAL not in out,
              f"exit {code}: {out.strip()[-400:]!r} -- this is the README's standalone recipe")
    finally:
        subprocess.run([runtime, "volume", "rm", "-f", vol, vol_data], capture_output=True)


def main():
    import tempfile
    print("--- recipe: what the entrypoint and compose say ---")
    recipe_checks()
    print("\n--- artifact: what the image does ---")
    with tempfile.TemporaryDirectory(dir=os.environ.get("PERSONA_TEST_TMP") or None) as tmp:
        try:
            artifact_checks(Path(tmp))
        finally:
            # a run that chowned its way out of our reach still has to be removable, or a single
            # red leaves litter no later run can clean up.
            reclaim(cli() or "podman", Path(tmp))
    tail = f", {len(SKIPPED)} SKIPPED" if SKIPPED else ""
    print(f"\n=== userns guard: {PASS} passed, {FAIL} failed{tail} ===")
    for reason in SKIPPED:
        print(f"  SKIPPED: {reason}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
