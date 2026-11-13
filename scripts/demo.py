"""Break the platform on purpose and time how it recovers.

Runs against the local kind cluster and records real measurements to
results/demo.json (the showcase site is built from that file).

  python scripts/demo.py                 # all experiments (about 15 minutes)
  python scripts/demo.py --only pod_kill,drift
  python scripts/demo.py --yes           # don't ask before pushing release commits

The release experiments push real commits to this repo (that IS the GitOps
flow: Git changes, the cluster follows). They need a clean working tree on main.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "gitops/workloads/demo-app/release.yaml"
RESULTS = ROOT / "results/demo.json"
CTX = "kind-platform-lab"
NS = "demo"


# --------------------------------------------------------------------- helpers

def sh(*cmd: str, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout


def kubectl(*args: str, check: bool = True) -> str:
    return sh("kubectl", "--context", CTX, *args, check=check)


def kjson(*args: str) -> dict:
    return json.loads(kubectl(*args, "-o", "json"))


def rollout() -> dict:
    return kjson("-n", NS, "get", "rollout", "demo-app")


def available(r: dict | None = None) -> tuple[int, int]:
    r = r or rollout()
    return int(r["status"].get("availableReplicas", 0)), int(r["spec"].get("replicas", 0))


def wait_until(check, timeout: float, poll: float = 1.0):
    """Seconds until check() is truthy, or None on timeout."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            if check():
                return round(time.monotonic() - t0, 1)
        except RuntimeError:
            pass                                      # e.g. resource briefly missing
        time.sleep(poll)
    return None


class Timeline:
    def __init__(self):
        self.t0 = time.monotonic()
        self.events: list[dict] = []

    def mark(self, text: str):
        t = round(time.monotonic() - self.t0, 1)
        self.events.append({"t": t, "event": text})
        print(f"    +{t:6.1f}s  {text}", flush=True)
        return t


def refresh_argocd(app: str = "demo-app"):
    kubectl("-n", "argocd", "annotate", "application", app, "argocd.argoproj.io/refresh=hard", "--overwrite")


# ------------------------------------------------------------- release file

def read_release(path: Path = RELEASE) -> dict:
    """Return {'VERSION': 'v1', 'ERROR_RATE': '0', ...} from release.yaml."""
    text = path.read_text()
    return dict(re.findall(r"- name: (\w+)\n\s+value: \"?([^\"\n]*)\"?", text))


def write_release(updates: dict, path: Path = RELEASE) -> None:
    """Change env values in release.yaml in place, keeping its comments and layout."""
    text = path.read_text()
    for name, value in updates.items():
        pat = re.compile(rf"(- name: {name}\n(\s+)value: )\"?[^\"\n]*\"?")
        if not pat.search(text):
            raise ValueError(f"{name} not found in {path}")
        text = pat.sub(lambda m: f'{m.group(1)}"{value}"' if name != "VERSION" else f"{m.group(1)}{value}", text)
    path.write_text(text)


def next_version(current: str, suffix: str = "") -> str:
    m = re.match(r"v(\d+)", current)
    return f"v{int(m.group(1)) + 1 if m else 2}{suffix}"


def git_push_release(message: str):
    sh("git", "-C", str(ROOT), "add", str(RELEASE))
    sh("git", "-C", str(ROOT), "commit", "-m", message)
    sh("git", "-C", str(ROOT), "push", "origin", "main")
    refresh_argocd()


# ---------------------------------------------------------------- prometheus

@contextlib.contextmanager
def prometheus():
    proc = subprocess.Popen(["kubectl", "--context", CTX, "-n", "monitoring", "port-forward", "svc/kps-prometheus", "19090:9090"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_until(lambda: urllib.request.urlopen("http://localhost:19090/-/ready", timeout=2).status == 200, 20)
        yield query
    finally:
        proc.terminate()


def query(promql: str) -> float | None:
    url = "http://localhost:19090/api/v1/query?" + urllib.parse.urlencode({"query": promql})
    with urllib.request.urlopen(url, timeout=10) as r:
        res = json.load(r)["data"]["result"]
    return float(res[0]["value"][1]) if res else None


# --------------------------------------------------------------- experiments

def exp_pod_kill() -> dict:
    tl = Timeline()
    pods = [p for p in kjson("-n", NS, "get", "pods", "-l", "app=demo-app")["items"] if p["status"]["phase"] == "Running"]
    victim = pods[0]["metadata"]["name"]
    kubectl("-n", NS, "delete", "pod", victim, "--wait=false")
    tl.mark(f"deleted pod {victim}")
    wait_until(lambda: available()[0] < available()[1], 15, 0.3)
    tl.mark("Kubernetes noticed: fewer pods ready than wanted")
    secs = wait_until(lambda: available()[0] == available()[1], 120, 0.5)
    tl.mark("replacement pod ready: back to full strength")
    return {"name": "pod_kill", "title": "A pod is killed", "recovered_s": tl.events[-1]["t"] if secs is not None else None,
            "events": tl.events}


def exp_node_failure() -> dict:
    tl = Timeline()
    pods = kjson("-n", NS, "get", "pods", "-l", "app=demo-app")["items"]
    counts: dict[str, int] = {}
    for p in pods:
        n = p["spec"].get("nodeName", "")
        if "worker" in n:
            counts[n] = counts.get(n, 0) + 1
    node = max(counts, key=counts.get)
    sh("docker", "stop", node)
    tl.mark(f"powered off node {node} ({counts[node]} app pods on it)")

    def node_not_ready():
        conds = kjson("get", "node", node)["status"]["conditions"]
        return any(c["type"] == "Ready" and c["status"] != "True" for c in conds)
    wait_until(node_not_ready, 120)
    tl.mark("Kubernetes marked the node NotReady")

    def all_ready_elsewhere():
        ps = kjson("-n", NS, "get", "pods", "-l", "app=demo-app")["items"]
        ok = [p for p in ps if p["spec"].get("nodeName") != node and p["status"]["phase"] == "Running"
              and all(c.get("ready") for c in p["status"].get("containerStatuses", []))]
        return len(ok) >= available()[1]
    secs = wait_until(all_ready_elsewhere, 300, 2)
    tl.mark("all pods running on the surviving nodes")
    sh("docker", "start", node)
    wait_until(lambda: not node_not_ready(), 180, 3)
    tl.mark("node powered back on and Ready")
    return {"name": "node_failure", "title": "A whole node fails", "node": node, "pods_on_node": counts[node],
            "recovered_s": tl.events[2]["t"] if secs is not None else None, "events": tl.events}


def exp_drift() -> dict:
    tl = Timeline()
    kubectl("-n", NS, "delete", "service", "demo-app", "--wait=true")
    tl.mark("deleted the demo-app Service by hand (not in Git)")
    s1 = wait_until(lambda: kubectl("-n", NS, "get", "service", "demo-app", check=False).strip() != "", 180, 1)
    tl.mark("Argo CD re-created the Service from Git")
    kubectl("-n", NS, "patch", "rollout", "demo-app", "--type", "merge", "-p", '{"spec":{"replicas":1}}')
    tl.mark("scaled the app to 1 replica by hand")
    s2 = wait_until(lambda: rollout()["spec"]["replicas"] == 5, 180, 1)
    tl.mark("Argo CD set replicas back to 5, as Git says")
    wait_until(lambda: available()[0] == 5, 120, 1)
    return {"name": "drift", "title": "Someone changes the cluster by hand",
            "service_restored_s": s1, "replicas_restored_s": s2, "events": tl.events}


def _release(bad: bool) -> dict:
    cur = read_release()
    version = next_version(cur["VERSION"], "-bad" if bad else "")
    tl = Timeline()
    write_release({"VERSION": version, "ERROR_RATE": "0.3" if bad else "0"})
    git_push_release(f"release {version}" + (" (deliberately broken: 30% errors)" if bad else ""))
    tl.mark(f"pushed release {version} to Git")
    start_hash = rollout()["status"].get("currentPodHash")
    wait_until(lambda: rollout()["status"].get("currentPodHash") != start_hash, 240, 1)
    tl.mark("Argo CD synced it: canary started with 20% of pods")
    out = {"name": "bad_release" if bad else "good_release", "version": version, "events": tl.events}
    if bad:
        secs = wait_until(lambda: rollout()["status"].get("abort") is True or rollout()["status"].get("phase") == "Degraded", 420, 2)
        tl.mark("canary analysis failed: release aborted, traffic back on the stable version")
        wait_until(lambda: available()[0] == 5, 240, 2)
        window = int(tl.events[-1]["t"]) + 30
        with prometheus() as q:
            errors = q(f'sum(increase(demo_http_requests_total{{namespace="demo", path="/", code=~"5.."}}[{window}s]))')
            total = q(f'sum(increase(demo_http_requests_total{{namespace="demo", path="/"}}[{window}s]))')
            alert = q(f'max_over_time(ALERTS{{alertname="DemoAppHighErrorRate", alertstate="firing"}}[{window}s])')
        out.update(title="A broken release is pushed", detected_s=tl.events[-1]["t"] if secs is not None else None,
                   failed_requests=round(errors or 0), total_requests=round(total or 0),
                   error_share=round((errors or 0) / total, 4) if total else None, alert_fired=bool(alert))
        # put Git back the way it was: the rollback in the cluster is real, now make Git agree
        write_release({"VERSION": cur["VERSION"], "ERROR_RATE": cur.get("ERROR_RATE", "0")})
        git_push_release(f"revert {version}")
        wait_until(lambda: rollout()["status"].get("phase") == "Healthy", 300, 2)
        tl.mark("Git reverted to the last good release; rollout Healthy")
    else:
        secs = wait_until(lambda: rollout()["status"].get("phase") == "Healthy"
                          and rollout()["status"].get("stableRS") == rollout()["status"].get("currentPodHash"), 600, 2)
        tl.mark(f"canary passed analysis: {version} promoted to 100%")
        out.update(title="A good release is pushed", promoted_s=tl.events[-1]["t"] if secs is not None else None)
    return out


EXPERIMENTS = {
    "pod_kill": exp_pod_kill,
    "node_failure": exp_node_failure,
    "drift": exp_drift,
    "bad_release": lambda: _release(bad=True),
    "good_release": lambda: _release(bad=False),
}


def preflight(need_git: bool):
    kubectl("cluster-info")
    r = rollout()
    if r["status"].get("phase") != "Healthy":
        sys.exit(f"demo-app rollout is {r['status'].get('phase')}, not Healthy. Wait for `make status` to settle.")
    if need_git:
        if sh("git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD").strip() != "main":
            sys.exit("Release experiments push to main: check out main first.")
        if sh("git", "-C", str(ROOT), "status", "--porcelain").strip():
            sys.exit("Release experiments need a clean working tree: commit or stash your changes first.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", help="comma-separated: " + ",".join(EXPERIMENTS))
    p.add_argument("--yes", action="store_true", help="don't ask before pushing release commits")
    a = p.parse_args(argv)
    names = a.only.split(",") if a.only else list(EXPERIMENTS)
    need_git = any(n.endswith("release") for n in names)
    preflight(need_git)
    if need_git and not a.yes:
        if input("Release experiments push commits to origin/main. Continue? [y/N] ").strip().lower() != "y":
            return 1
    nodes = kjson("get", "nodes")["items"]
    results = {"run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "cluster": {"nodes": len(nodes), "kubernetes": nodes[0]["status"]["nodeInfo"]["kubeletVersion"]},
               "experiments": []}
    if RESULTS.exists():
        old = json.loads(RESULTS.read_text())
        results["experiments"] = [e for e in old.get("experiments", []) if e["name"] not in names]
    for n in names:
        print(f"\n== {n}", flush=True)
        results["experiments"].append(EXPERIMENTS[n]())
        wait_until(lambda: rollout()["status"].get("phase") == "Healthy" and available()[0] == 5, 300, 2)
        RESULTS.parent.mkdir(exist_ok=True)
        RESULTS.write_text(json.dumps(results, indent=2))
    print(f"\nSaved {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
