#!/usr/bin/env python3
"""nothing in the browser image reports on what it is doing.

WHY THERE IS ONE AT ALL: both halves of this image phone home BY DEFAULT and neither says so at
the point of use. browser-use constructs a posthog client at import time unless
`ANONYMIZED_TELEMETRY` is falsy (browser_use/telemetry/service.py) and derives cloud sync to
api.browser-use.com from the same flag, so a run that browses on someone's behalf ships event
names, durations and error text to eu.i.posthog.com. chrome's own set -- UMA, variations, domain
reliability, safebrowsing, autofill, translate -- is larger and quieter still. MEASURED on
khimaros/browser-use built 2026-09-15: `ProductTelemetry()._posthog_client` was a live
posthog.Posthog instance and the image had no chrome policy directory at all.

TWO KINDS OF CHECK, following tests/display_test.py, because they see different things:

  RECIPE -- what the Dockerfile, the policy file and browser-head SAY. cheap, always runs, and
  cannot tell you whether the image was ever rebuilt from them.

  ARTIFACT -- what the IMAGE DOES: the real interpreter asked what it resolved, and the real
  telemetry service asked whether it built a client. it SKIPS when the image predates the
  Dockerfile, because "you have not rebuilt yet" is not a defect.

AND ONE CHECK THAT IS NEITHER: every chrome policy this image sets is looked up in the SHIPPED
CHROME BINARY. an unrecognised policy name is not an error to chrome -- it is ignored in silence,
which looks exactly like a policy that works. so the names are verified against the thing that
has to honour them rather than against a document.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = ROOT / "images" / "browser-use"
DOCKERFILE = IMAGE_DIR / "Dockerfile"
BROWSER_HEAD = IMAGE_DIR / "scripts" / "browser-head"
POLICY_SRC = IMAGE_DIR / "chrome-policies" / "telemetry.json"
IMAGE = os.environ.get("BROWSER_USE_IMAGE", "khimaros/browser-use:latest")
PERSONA_IMAGE = os.environ.get("PERSONA_IMAGE", "khimaros/persona:latest")

# where chrome looks. compiled into the binary (`strings chrome | grep /etc/opt/chrome`), so it is
# not configurable and the Dockerfile has to name exactly this.
POLICY_DIR_IN_IMAGE = "/etc/opt/chrome/policies/managed"
# the uv tool venv from the Dockerfile's UV_TOOL_DIR=/opt/uv. the SYSTEM python3 cannot import
# browser_use, so a probe that asks the library anything has to use this one.
VENV_PYTHON = "/opt/uv/browser-use/bin/python"
CHROME_BINARY = "/opt/google/chrome/chrome"
# a policy chrome cannot possibly know, carried through the binary search as its negative control.
CONTROL_NAME = "PersonaNotARealPolicy"

# browser-use's three phone-homes, each an env var read through browser_use/config.py:
#   ANONYMIZED_TELEMETRY     -> eu.i.posthog.com, every agent step and cli invocation
#   BROWSER_USE_CLOUD_SYNC   -> api.browser-use.com; DEFAULTS TO ANONYMIZED_TELEMETRY, set anyway
#                               so that turning one back on does not quietly take the other with it
#   BROWSER_USE_VERSION_CHECK -> pypi.org on every agent run, which is a usage ping with a version
#                               number attached
TELEMETRY_ENV = ("ANONYMIZED_TELEMETRY", "BROWSER_USE_CLOUD_SYNC", "BROWSER_USE_VERSION_CHECK")

# the chrome policies, and what each one stops. VALUES ARE PART OF THE CHECK: three of these are
# enums where the safe answer is a number, and `false` would be silently wrong.
EXPECTED_POLICY = {
    "MetricsReportingEnabled": False,          # UMA: usage + stability to google
    "UrlKeyedAnonymizedDataCollectionEnabled": False,  # urls, keyed to a pseudonymous id
    "SafeBrowsingProtectionLevel": 0,          # list updates and real-time url lookups
    "SafeBrowsingExtendedReportingEnabled": False,
    "DomainReliabilityAllowed": False,         # per-request failure beacons to google
    "ChromeVariations": 2,                     # 2 = no variations seed fetch at all
    "ComponentUpdatesEnabled": False,
    "SyncDisabled": True,
    "BrowserSignin": 0,                        # 0 = signin disabled, so no gaia traffic
    "SearchSuggestEnabled": False,             # otherwise the omnibox sends keystrokes
    "SpellCheckServiceEnabled": False,         # otherwise typed text goes to google
    "TranslateEnabled": False,
    "AlternateErrorPagesEnabled": False,       # navigation errors become a google lookup
    "NetworkPredictionOptions": 2,             # 2 = never prefetch/preresolve
    "PasswordLeakDetectionEnabled": False,     # sends hashed credentials to google
    "EnableMediaRouter": False,                # cast discovery chatter
    "UserFeedbackAllowed": False,
    "FeedbackSurveysEnabled": False,
    "PrivacySandboxAdTopicsEnabled": False,
    "PrivacySandboxSiteEnabledAdsEnabled": False,
    "PrivacySandboxAdMeasurementEnabled": False,
    "PrivacySandboxPromptEnabled": False,
}

# the command line half. policy covers a chrome that reads the policy directory; these cover the
# same ground at launch and are the only lever for anything policy has no key for.
EXPECTED_CHROME_FLAGS = (
    "--disable-background-networking",         # variations, component + extension updates, sync
    "--disable-breakpad",                      # no crash uploads
    "--disable-domain-reliability",
    "--disable-sync",
    "--no-pings",                              # <a ping> hyperlink auditing
    "--disable-client-side-phishing-detection",
    "--safebrowsing-disable-auto-update",
    "--disable-features=",                     # the feature list is checked separately
    "--metrics-recording-only",                # record locally, never upload
)
# features disabled by name, each one an independent fetch from a google endpoint.
EXPECTED_DISABLED_FEATURES = (
    "OptimizationHints",
    "OptimizationGuideModelDownloading",
    "AutofillServerCommunication",
    "MediaRouter",
    "Translate",
    "InterestFeedContentSuggestions",
)

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


# --- recipe: what the files say -------------------------------------------------------------

def dockerfile_env():
    """the ENV assignments the Dockerfile makes, as a dict.

    ENV IS THE RIGHT LAYER and the reason this reads the Dockerfile rather than compose: persona
    is built FROM this image and inherits its ENV, but anything that runs the base directly (or a
    `docker run` for a one-off browse) gets nothing from persona's compose file.
    """
    text = DOCKERFILE.read_text()
    env = {}
    # ENV lines continue with a trailing backslash, so join them before splitting on whitespace.
    for block in re.findall(r"^ENV\s+((?:.*\\\n)*.*)$", text, flags=re.M):
        for pair in block.replace("\\\n", " ").split():
            if "=" in pair:
                key, value = pair.split("=", 1)
                env[key] = value.strip('"\'')
    return env


def recipe_checks():
    env = dockerfile_env()
    for name in TELEMETRY_ENV:
        value = env.get(name, "")
        check(f"the image sets {name} off",
              value.lower()[:1] in ("f", "n", "0"),
              f"{name}={value or '(unset)'} -- browser_use/config.py reads this with a default of "
              "'true', so an unset var is an ENABLED one")

    check("the image ships a chrome managed-policy file", POLICY_SRC.exists(),
          f"{POLICY_SRC} -- flags only reach a chrome someone launched with them; policy reaches "
          "every chrome in the image")
    check("the Dockerfile installs it where chrome looks",
          POLICY_DIR_IN_IMAGE in DOCKERFILE.read_text(),
          f"chrome reads only {POLICY_DIR_IN_IMAGE}; that path is compiled into the binary")

    if POLICY_SRC.exists():
        try:
            policy = json.loads(POLICY_SRC.read_text())
        except json.JSONDecodeError as exc:
            check("the policy file is valid json", False,
                  f"{exc} -- chrome skips a policy file it cannot parse, and says so only in a log")
            policy = {}
        else:
            check("the policy file is valid json", True)
        for name, want in EXPECTED_POLICY.items():
            got = policy.get(name, "(unset)")
            check(f"policy {name} = {want}", got == want,
                  f"got {got!r}")

    if not BROWSER_HEAD.exists():
        check("browser-head exists", False, str(BROWSER_HEAD))
        return
    script = BROWSER_HEAD.read_text()
    for flag in EXPECTED_CHROME_FLAGS:
        check(f"browser-head launches chrome with {flag}", flag in script,
              "policy and flags overlap on purpose: a flag covers a chrome started before the "
              "policy directory is readable, and covers what policy has no key for")
    for feature in EXPECTED_DISABLED_FEATURES:
        check(f"browser-head disables the {feature} feature", feature in script)


# --- artifact: what the image does ----------------------------------------------------------

# ASKS THE LIBRARY, NOT THE ENVIRONMENT. `env | grep ANONYMIZED_TELEMETRY` proves a string was set;
# it cannot prove browser-use agreed with it, and browser_use/config.py has two config classes and
# a `.env` file loader between the variable and the answer. so the probe imports the real module,
# reads the resolved values, constructs the real ProductTelemetry, and reports whether it built a
# posthog client -- the object whose existence IS the leak.
PROBE = f"""
set -e
echo "PROBE env $(printenv ANONYMIZED_TELEMETRY || echo unset) $(printenv BROWSER_USE_CLOUD_SYNC || echo unset) $(printenv BROWSER_USE_VERSION_CHECK || echo unset)"
if [ -x {VENV_PYTHON} ]; then
  {VENV_PYTHON} - <<'PY' 2>/dev/null
from browser_use.config import CONFIG
from browser_use.telemetry.service import ProductTelemetry
print('PROBE resolved', CONFIG.ANONYMIZED_TELEMETRY, CONFIG.BROWSER_USE_CLOUD_SYNC, CONFIG.BROWSER_USE_VERSION_CHECK)
print('PROBE posthog', ProductTelemetry()._posthog_client is None)
PY
else
  echo "PROBE no-venv"
fi
echo "PROBE policy-file $(cat {POLICY_DIR_IN_IMAGE}/telemetry.json 2>/dev/null | tr -d '\\n' || echo missing)"
# EVERY POLICY NAME, LOOKED UP IN THE BINARY THAT HAS TO HONOUR IT. a name chrome does not know is
# ignored without a word, so a renamed or mistyped policy is indistinguishable from a working one.
#
# IN PYTHON, NOT `strings`: binutils is not in a slim debian, and the first version of this probe
# reported every policy unknown for that reason -- a broken instrument reading exactly like the
# defect it was there to find. so it searches for the NUL-DELIMITED name in the binary (chrome
# keeps them in a table of C strings) and carries CONTROL_NAME, a policy that cannot exist: if
# that one comes back known, the search matches anything and no other answer here means a thing.
if [ -x {VENV_PYTHON} ]; then
  {VENV_PYTHON} - <<'PY'
import mmap
names = {list(EXPECTED_POLICY) + [CONTROL_NAME]!r}
with open({CHROME_BINARY!r}, 'rb') as fh:
    blob = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
    for name in names:
        found = blob.find(b'\\x00' + name.encode() + b'\\x00') != -1
        print('PROBE', 'known' if found else 'unknown', name)
PY
fi
"""


def docker():
    return shutil.which("docker") or shutil.which("podman")


def image_predates_recipe(cli, image):
    """true when the image was built before its recipe last changed.

    a recipe check goes green the moment the file is edited; the image that ships is built later.
    an artifact check against the older one would fail for a reason that is not a defect.
    """
    out = subprocess.run([cli, "image", "inspect", image, "-f", "{{json .Created}}"],
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
    sources = [DOCKERFILE, BROWSER_HEAD] + ([POLICY_SRC] if POLICY_SRC.exists() else [])
    recipe = datetime.fromtimestamp(max(p.stat().st_mtime for p in sources), tz=timezone.utc)
    return built < recipe


def artifact_checks(image, label):
    cli = docker()
    if not cli:
        skip(f"no docker/podman; {label} was not asked anything")
        return
    predates = image_predates_recipe(cli, image)
    if predates is None:
        skip(f"no local {image}; {label} telemetry was not checked for real")
        return
    if predates:
        skip(f"{image} predates its recipe; rebuild for the {label} artifact half to mean anything")
        return

    def c(desc, ok, detail=""):
        check(f"[{label}] {desc}", ok, detail)

    out = subprocess.run([cli, "run", "--rm", "--entrypoint", "bash", image, "-c", PROBE],
                         capture_output=True, text=True, timeout=300)
    probe, known = {}, {}
    for line in out.stdout.splitlines():
        if not line.startswith("PROBE "):
            continue
        parts = line.split(" ", 2)
        rest = parts[2] if len(parts) > 2 else ""
        if parts[1] in ("known", "unknown"):
            known[rest.strip()] = parts[1] == "known"
        else:
            probe[parts[1]] = rest

    if "no-venv" in probe:
        c("the image can be asked what browser-use resolved", False,
              f"{VENV_PYTHON} is not there -- the uv tool venv moved, and this probe has been "
              "checking nothing")
    else:
        # THE ONE THAT MATTERS. everything else is a proxy for this line.
        c("browser-use builds no posthog client", probe.get("posthog", "") == "True",
              f"ProductTelemetry()._posthog_client is None -> {probe.get('posthog', '(no answer)')}")
        c("browser-use resolved all three flags off",
              probe.get("resolved", "") == "False False False",
              f"{probe.get('resolved', '(no answer)')} -- config.ANONYMIZED_TELEMETRY, "
              "BROWSER_USE_CLOUD_SYNC, BROWSER_USE_VERSION_CHECK")

    c("the running image carries the three env vars",
          "unset" not in probe.get("env", "unset"),
          f"printenv said: {probe.get('env', '(no answer)')}")

    raw = probe.get("policy-file", "missing")
    if raw == "missing":
        c(f"chrome finds a policy file at {POLICY_DIR_IN_IMAGE}", False,
              "the Dockerfile may say it and the image not have it")
    else:
        try:
            installed = json.loads(raw)
        except json.JSONDecodeError as exc:
            c("the installed policy file parses", False, str(exc))
            installed = {}
        else:
            c("the installed policy file parses", True)
        missing = [k for k, v in EXPECTED_POLICY.items() if installed.get(k, "(unset)") != v]
        c("the installed policy matches the one in the tree", not missing,
              f"differs on: {', '.join(missing)}")

    if not known:
        skip("the chrome binary was not searched for policy names")
    elif known.pop(CONTROL_NAME, False):
        # THE CONTROL FIRED, so the search matches anything and every "known" below is worthless.
        c("the policy-name search can tell a real policy from a made-up one", False,
              f"{CONTROL_NAME} came back known -- the probe is broken, not the policy file")
    else:
        unknown = sorted(name for name, ok in known.items() if not ok)
        c("every policy name is one this chrome recognises", not unknown,
              f"{', '.join(unknown)} -- chrome ignores a name it does not know WITHOUT COMPLAINING, "
              "so these lines look like protection and are not")


# --- launch: what chrome is actually started with ------------------------------------------

# A FLAG LIST IN A BASH ARRAY IS NOT A FLAG ON A PROCESS. the recipe half reads the array and the
# policy half reads a file; neither would notice CHROME_PRIVACY_ARGS being dropped from the
# CHROME_BASE_ARGS expansion, or a second launch path that never picks it up. so this starts the
# browser the way the skill tells the agent to and reads the flags off /proc.
#
# UNPRIVILEGED, WHICH IS NOT A DETAIL: browser-head deliberately does not pass --no-sandbox, and
# chrome refuses to run as root without it -- so a probe that used the default `docker run` user
# would get "chrome process exited" every time and prove nothing about flags.
#
# no DISPLAY, so this takes the headless fallback. that is the same argv either way: the privacy
# flags live in CHROME_BASE_ARGS, which both launches share.
LAUNCH_PROBE = """
set -e
mkdir -p "$HOME"
browser-head start > /tmp/bh.log 2>&1 || true
pid=$(cat /tmp/browser-head.pid 2>/dev/null || true)
# A BARE $pid WOULD READ /proc/cmdline -- THE HOST KERNEL'S -- and print a plausible line of args
# that has nothing to do with chrome. the first version of this probe did exactly that.
if [ -z "$pid" ]; then
  echo "LAUNCH nostart"
  sed 's/^/LAUNCH log /' /tmp/bh.log
else
  tr '\\0' '\\n' < "/proc/$pid/cmdline" | sed 's/^/LAUNCH arg /'
fi
browser-head stop >/dev/null 2>&1 || true
"""


def launch_checks(cli):
    out = subprocess.run(
        [cli, "run", "--rm", "--user", "1000:1000", "-e", "HOME=/tmp/bh-home",
         "--entrypoint", "bash", IMAGE, "-c", LAUNCH_PROBE],
        capture_output=True, text=True, timeout=300)
    args = [line.split(" ", 2)[2] for line in out.stdout.splitlines()
            if line.startswith("LAUNCH arg ")]
    if not args:
        log = "\n      ".join(line[len("LAUNCH log "):] for line in out.stdout.splitlines()
                              if line.startswith("LAUNCH log "))
        check("browser-head starts a chrome at all", False,
              f"no process to read flags off. browser-head said:\n      {log or '(nothing)'}")
        return
    check("browser-head starts a chrome at all", True)
    for flag in EXPECTED_CHROME_FLAGS:
        if flag == "--disable-features=":
            continue
        check(f"the running chrome carries {flag}", flag in args,
              f"argv: {' '.join(args)}")
    features = next((a.split("=", 1)[1] for a in args if a.startswith("--disable-features=")), "")
    for feature in EXPECTED_DISABLED_FEATURES:
        check(f"the running chrome disables {feature}", feature in features.split(","),
              f"--disable-features={features or '(absent)'}")


def main():
    print("--- recipe: what the image files say ---")
    recipe_checks()
    # BOTH IMAGES, and persona is the one that matters: the base is where the settings are WRITTEN,
    # but persona is what ships and what anybody runs. ENV and /etc both cross a FROM, so asking
    # persona is the check that the inheritance actually happened rather than the assumption that
    # it must have -- and a downstream `environment:` or a later layer could undo either one.
    for image, label in ((IMAGE, "base"), (PERSONA_IMAGE, "persona")):
        print(f"\n--- artifact: what {image} does ---")
        artifact_checks(image, label)

    print(f"\n--- launch: the flags on a chrome {IMAGE} really started ---")
    cli = docker()
    if not cli:
        skip("no docker/podman; no chrome was started")
    elif image_predates_recipe(cli, IMAGE) is not False:
        skip(f"{IMAGE} missing or older than its recipe; no chrome was started")
    else:
        launch_checks(cli)

    tail = f", {len(SKIPPED)} SKIPPED" if SKIPPED else ""
    print(f"\n=== telemetry: {PASS} passed, {FAIL} failed{tail} ===")
    for reason in SKIPPED:
        print(f"    skipped: {reason}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
