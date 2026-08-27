#!/usr/bin/env python3
"""the container's display, and the window manager that makes it usable.

WHY THERE IS ONE AT ALL: the canvas face serves this display, and until now nothing on it could be
moved, resized or raised. chrome came up wherever it asked to and stayed there, so a person who
took the mouse at /canvas could use the page and nothing around it -- and a browser sized for a
phone was not expressible at all, since only a window manager can reshape a window somebody else
mapped.

TWO KINDS OF CHECK, and the file says which is which because they see different things:

  RECIPE -- what the Dockerfile and the entrypoint SAY. cheap, always runs, and cannot tell you
  whether the image was ever rebuilt from them.

  ARTIFACT -- what the IMAGE DOES: a real Xvfb, the real window manager, a real client window,
  asked whether it is managed. this is the half that catches a recipe nobody built. it SKIPS when
  the image predates the Dockerfile, because "you have not rebuilt yet" is not a defect -- and it
  FAILS rather than skips when a current image is missing the window manager, which is the case
  that would otherwise ship.
"""
import json
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker-entrypoint.sh"
JWMRC = ROOT / "display" / "jwmrc"
# where the Dockerfile installs our config, and the one path the entrypoint may name.
JWMRC_IN_IMAGE = "/etc/persona/jwmrc"
IMAGE = os.environ.get("PERSONA_IMAGE", "khimaros/persona:latest")
# a scratch display number for the artifact checks: inside the container, and not :88, so this
# never touches a running deployment's screen even if somebody bind-mounts /tmp/.X11-unix.
TEST_DISPLAY = ":77"
PASS = FAIL = 0
# what SAT OUT, named in the summary line and not only in the body. a suite that ends "14
# passed" while the half that would have caught a stale image quietly skipped is how an untested
# image ships -- the number at the bottom is the only line anybody reads.
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


# --- recipe: what the files say -------------------------------------------------------------

def recipe_checks():
    dockerfile = DOCKERFILE.read_text()
    entrypoint = ENTRYPOINT.read_text()

    check("the image installs a window manager", "jwm" in dockerfile,
          "without one, nothing on the canvas display can be moved, resized or raised")
    check("persona ships its own window manager config", JWMRC.exists(),
          f"{JWMRC} -- the distro default carries a tray, a root menu and four desktops")
    check("the Dockerfile installs that config where the entrypoint looks for it",
          JWMRC_IN_IMAGE in dockerfile and JWMRC_IN_IMAGE in entrypoint,
          f"both must name {JWMRC_IN_IMAGE}, or the wm silently falls back to the distro default")

    if not JWMRC.exists():
        return
    # COMMENTS ARE NOT CONFIG, and a check that cannot tell them apart is a check that fires on
    # the file explaining why the thing it forbids is forbidden. that is not hypothetical: the
    # first version of this test failed on jwmrc's own comment about `<Key>` grabs.
    rc = re.sub(r"<!--.*?-->", "", JWMRC.read_text(), flags=re.S)

    # XTEST DELIVERS KEYSTROKES TO THE FOCUSED WINDOW. with no wm, focus is PointerRoot -- whatever
    # the pointer is over -- which is what the canvas's input path has always relied on. a
    # click-to-focus wm changes that rule under it, so the focus model is load-bearing rather than
    # cosmetic and is stated in the file rather than inherited.
    check("focus follows the mouse", "<FocusModel>sloppy</FocusModel>" in rc,
          "click-to-focus would send canvas keystrokes to a window the person is not pointing at")

    # every <Key> is a grab. a wm that owns alt+F4 or a bare arrow key eats input aimed at the page
    # underneath it, and on a shared screen the agent types through the same path.
    check("the window manager grabs no keys", "<Key" not in rc,
          "a bound key is a grab, and a grab takes keystrokes away from the page")

    # this display is a canvas, not a desktop. a tray, a pager and a root menu are furniture that
    # would be captured, encoded and sent to whoever is watching, forever.
    for furniture in ("<Tray", "<RootMenu", "<Dock", "<Pager"):
        check(f"no {furniture[1:].lower()} on the canvas display", furniture not in rc,
              "everything drawn here is captured and streamed; furniture costs bandwidth for good")
    check("one desktop, not four", 'width="1"' in rc and 'height="1"' in rc,
          "a second desktop is a place a window can be lost where nobody watching can reach it")

    # the ORDER of the three cases in the display bootstrap is the whole of this check, and it is
    # asked of EVERY call site rather than the first: there are two (adopt an existing display,
    # start a new one) and checking one of them is how a second would drift.
    lines = entrypoint.splitlines()
    starts = [i for i, line in enumerate(lines)
              if "start_window_manager" in line and "()" not in line]
    check("the entrypoint starts the window manager", bool(starts))
    if not starts:
        return

    def line_of(needle):
        return next((i for i, line in enumerate(lines) if needle in line), None)

    forwarded, spawn, ready = (line_of("using the forwarded display"),
                               line_of('Xvfb "${PERSONA_DISPLAY}"'),
                               line_of("started Xvfb on"))
    check("the window manager starts only on the display we own",
          forwarded is not None and all(i < forwarded for i in starts),
          "a forwarded desktop already has a window manager; a second one fights it over every "
          "window on the screen, including windows that are not ours")
    # two legal places: BEFORE the spawn (the adopt branch, where the display already answers) and
    # AFTER the readiness wait (the branch that just started one). between them is the race.
    check("no window manager is started before its display exists",
          spawn is not None and ready is not None and all(i < spawn or i > ready for i in starts),
          "an X server takes a moment to bind its socket, and a wm that loses that race exits "
          "into a log nobody reads")

    # the whole agent runs unprivileged (hmux-drop). a wm holding the display as root would be the
    # one thing on it that does not.
    check("the window manager runs unprivileged, like everything else",
          "setpriv" in entrypoint or "hmux-drop" in entrypoint.split("start_window_manager")[-1],
          "chrome, pi and every face run as hmux; the wm should not be the exception")


# --- artifact: what the image does ----------------------------------------------------------

# started, then asked. every question is one a client of this display would ask: is anything
# managing it, does it speak the parts of ewmh the canvas needs, and does a window that maps get a
# frame put around it.
PROBE = f"""
set -e
command -v jwm >/dev/null 2>&1 || {{ echo "PROBE no-wm"; exit 0; }}
Xvfb {TEST_DISPLAY} -screen 0 800x600x24 -nolisten tcp >/tmp/probe-xvfb.log 2>&1 &
for _ in $(seq 1 50); do [ -e /tmp/.X11-unix/X{TEST_DISPLAY[1:]} ] && break; sleep 0.1; done
export DISPLAY={TEST_DISPLAY}
jwm -f {JWMRC_IN_IMAGE} >/tmp/probe-jwm.log 2>&1 &
# _NET_SUPPORTED AND NOT _NET_SUPPORTING_WM_CHECK, measured rather than assumed: jwm 2.4.6 lists
# the check atom among the things it supports and does not actually set it on the root, so waiting
# for that one waits out the whole timeout against a wm that is up and working.
for _ in $(seq 1 50); do xprop -root _NET_SUPPORTED 2>/dev/null | grep -q _NET_CLIENT_LIST && break; sleep 0.1; done
echo "PROBE supported $(xprop -root _NET_SUPPORTED 2>&1 | tr -d '\\n')"
xmessage -geometry 200x100+50+50 hello >/tmp/probe-xmessage.log 2>&1 &
for _ in $(seq 1 50); do xprop -root _NET_CLIENT_LIST 2>/dev/null | grep -q '0x' && break; sleep 0.1; done
echo "PROBE clients $(xprop -root _NET_CLIENT_LIST 2>&1 | tr -d '\\n')"
# A FRAME IS THE QUESTION, not "is it on screen". a managed window is reparented into a frame the
# wm owns, and the top edge of that frame is the titlebar somebody drags. an unmanaged window is a
# child of the root with nothing to grab.
id=$(xwininfo -root -tree 2>/dev/null | awk '/xmessage/{{print $1; exit}}')
echo "PROBE window ${{id:-none}}"
echo "PROBE parent $(xwininfo -id "$id" -children 2>/dev/null | grep 'Parent window id' | tr -d '\\n')"
echo "PROBE extents $(xprop -id "$id" _NET_FRAME_EXTENTS 2>&1 | tr -d '\\n')"
echo "PROBE root $(xwininfo -root 2>/dev/null | grep 'Window id' | tr -d '\\n')"
echo "PROBE focus-model $(grep -o '<FocusModel>[a-z]*</FocusModel>' {JWMRC_IN_IMAGE} 2>/dev/null)"
"""


def docker():
    return shutil.which("docker") or shutil.which("podman")


def image_predates_recipe(cli):
    """true when the image was built before the Dockerfile last changed.

    THE POINT OF ASKING: a recipe check goes green the moment the file is edited, and the image
    that actually ships is built minutes or hours later by somebody else. an artifact check run
    against yesterday's image would fail for a reason that is not a defect -- so it says so and
    stands down, rather than either lying or crying wolf.
    """
    out = subprocess.run([cli, "image", "inspect", IMAGE, "-f", "{{json .Created}}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    created = json.loads(out.stdout.strip() or '""')
    if not created:
        return None
    # docker prints RFC3339; compare as epoch seconds via date(1)-free parsing.
    stamp = created.replace("Z", "+00:00")
    from datetime import datetime, timezone
    try:
        built = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    recipe = datetime.fromtimestamp(
        max(DOCKERFILE.stat().st_mtime, ENTRYPOINT.stat().st_mtime), tz=timezone.utc)
    return built < recipe


def artifact_checks():
    cli = docker()
    if not cli:
        skip("no docker/podman; the image was not asked anything")
        return
    predates = image_predates_recipe(cli)
    if predates is None:
        skip(f"no local {IMAGE}; the display was not checked for real")
        return
    if predates:
        skip(f"{IMAGE} predates the Dockerfile; rebuild for the artifact half to mean anything")
        return

    out = subprocess.run([cli, "run", "--rm", "--entrypoint", "bash", IMAGE, "-c", PROBE],
                         capture_output=True, text=True, timeout=180)
    probe = {}
    for line in out.stdout.splitlines():
        if not line.startswith("PROBE "):
            continue
        parts = line.split(" ", 2)
        probe[parts[1]] = parts[2] if len(parts) > 2 else ""

    if "no-wm" in probe:
        check("the built image carries the window manager", False,
              "the Dockerfile says jwm and the image does not have it -- this image is stale, "
              "or the install line did not survive the build")
        return

    supported = probe.get("supported", "")
    check("something is managing the display", "_NET_SUPPORTED" in supported,
          "an unmanaged display advertises nothing at all")

    # the ones the canvas itself will call: enumerate what is on the display, reshape a window into
    # a phone, raise the one being looked at. a wm without them can decorate but cannot be driven.
    for atom in ("_NET_CLIENT_LIST", "_NET_MOVERESIZE_WINDOW", "_NET_ACTIVE_WINDOW",
                 "_NET_FRAME_EXTENTS"):
        check(f"the window manager advertises {atom}", atom in supported,
              "the canvas listing and the size presets are built on these")

    check("a window that maps is listed", "0x" in probe.get("clients", ""),
          f"_NET_CLIENT_LIST: {probe.get('clients', '(empty)')}")

    # a frame is what a person drags. no frame, no dragging, whatever else is true.
    window, parent, root = probe.get("window", ""), probe.get("parent", ""), probe.get("root", "")
    root_id = root.split("Window id:")[-1].strip().split()[0] if "Window id:" in root else ""
    check("a mapped window is reparented into a frame",
          window not in ("", "none") and bool(root_id) and root_id not in parent,
          f"window {window} parent {parent!r} root {root_id!r} -- a window still parented to the "
          "root is a window nobody can move")

    # THE TITLEBAR, ASKED FOR BY HEIGHT. reparenting alone would be satisfied by a frame of zero
    # thickness, which is a window with nothing to grab -- so this reads the extents the wm
    # publishes and requires a top edge.
    extents = probe.get("extents", "")
    top = 0
    if "=" in extents:
        parts = [p.strip() for p in extents.split("=")[-1].split(",")]
        top = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
    check("the frame has a titlebar to drag", top > 0,
          f"_NET_FRAME_EXTENTS: {extents or '(unset)'} -- left,right,top,bottom; top is the title")

    check("the image's window manager uses the focus model we shipped",
          "sloppy" in probe.get("focus-model", ""),
          f"{probe.get('focus-model', '(no config found)')} -- click-to-focus would break canvas typing")


def main():
    print("--- recipe: what the Dockerfile and entrypoint say ---")
    recipe_checks()
    print("\n--- artifact: what the image does ---")
    artifact_checks()
    tail = f", {len(SKIPPED)} SKIPPED" if SKIPPED else ""
    print(f"\n=== display: {PASS} passed, {FAIL} failed{tail} ===")
    for reason in SKIPPED:
        print(f"    skipped: {reason}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
