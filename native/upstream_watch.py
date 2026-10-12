"""Track released T3/Zeron changes and native RPC compatibility without changing source."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request


class Api:
    def __init__(self, github_auth=False):
        self.github_auth = github_auth

    def get(self, endpoint):
        if self.github_auth:
            result = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"GitHub request failed: {endpoint}")
            return json.loads(result.stdout)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "z3-upstream-watch"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request("https://api.github.com/" + endpoint, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise RuntimeError(f"GitHub request failed (HTTP {status}): {endpoint}") from None

    def source(self, repository, path, sha):
        result = self.get(f"repos/{repository}/contents/{path}?ref={sha}")
        if result.get("encoding") != "base64":
            raise RuntimeError(f"GitHub source encoding changed: {path}")
        return base64.b64decode(result["content"]).decode()


def methods(source):
    aliases = dict(re.findall(r'\b(\w+):\s*"([^"]+)"', source))
    registered = set(re.findall(r'\bRpc\.make\(\s*"([^"]+)"', source))
    unresolved = []
    for key in re.findall(r'\bRpc\.make\(\s*(?:WS_METHODS|ORCHESTRATION_V2_WS_METHODS)\.(\w+)', source):
        if key in aliases:
            registered.add(aliases[key])
        else:
            unresolved.append(key)
    return registered, sorted(set(unresolved))


def native_calls(root):
    calls = set()
    for path in (root / "native/zeron/crates/t3/src").glob("*.rs"):
        source = path.read_text().split("#[cfg(test)]", 1)[0]
        calls.update(re.findall(r'\.(?:call|subscribe_checked|subscribe)\(\s*"([^"]+)"', source))
    if len(calls) < 10:
        raise RuntimeError("Native adapter call syntax changed; update the coverage parser")
    return calls


def release_for(releases, pattern):
    return next((release for release in releases
                 if not release.get("draft") and re.search(pattern, release["tag_name"])), None)


def observe(api, config):
    repository = config["repository"]
    branch = api.get(f"repos/{repository}/commits/{config['branch']}")
    release = release_for(api.get(f"repos/{repository}/releases?per_page=100"), config["releasePattern"])
    target = api.get(f"repos/{repository}/commits/{urllib.parse.quote(release['tag_name'], safe='')}") if release else branch
    sha = target["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("GitHub returned an invalid commit identity")
    result = {"repository": repository, "integratedCommit": config["integratedCommit"],
              "headCommit": branch["sha"], "targetCommit": sha,
              "release": release["tag_name"] if release else None,
              "releaseUrl": release["html_url"] if release else None,
              "changed": sha != config["integratedCommit"]}
    if result["changed"]:
        try:
            comparison = api.get(f"repos/{repository}/compare/{config['integratedCommit']}...{sha}?per_page=100")
            result.update({"comparisonStatus": comparison["status"],
                           "aheadBy": comparison["ahead_by"],
                           "commits": [{"sha": item["sha"], "message": item["commit"]["message"].splitlines()[0]}
                                       for item in comparison["commits"]],
                           "files": [{"path": item["filename"], "status": item["status"]} for item in comparison["files"]],
                           "filesMayBeTruncated": len(comparison["files"]) >= 300,
                           "commitsMayBeTruncated": comparison["total_commits"] > len(comparison["commits"])})
        except RuntimeError as error:
            # A rewritten upstream history can make the old import unreachable.
            result["comparisonError"] = str(error)
    return result


def tree_difference(before, after):
    return [{"path": path, "status": "added" if path not in before else "removed" if path not in after else "modified"}
            for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]


def compare_imported_tree(api, root, upstream):
    # The vendored import remains in our Git history even if upstream rewrote theirs.
    result = subprocess.run(["git", "-C", str(root), "ls-tree", "-rz", upstream["integratedCommit"]],
                            capture_output=True)
    if result.returncode:
        raise RuntimeError("Imported source tree is unavailable locally")
    before = {}
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, sha = metadata.decode().split()
        if kind == "blob":
            before[path.decode()] = (sha, mode)
    current = api.get(f"repos/{upstream['repository']}/git/trees/{upstream['targetCommit']}?recursive=1")
    after = {entry["path"]: (entry["sha"], entry["mode"]) for entry in current["tree"] if entry["type"] == "blob"}
    return {"files": tree_difference(before, after), "filesMayBeTruncated": current.get("truncated", False),
            "comparisonStatus": "tree-diff", "comparisonBasis": "original imported Git tree versus release tree"}


def report(api, root, config):
    observed = {name: observe(api, upstream) for name, upstream in config["upstreams"].items()}
    for name, upstream in observed.items():
        if name == "zeron" and upstream.get("comparisonError"):
            try:
                upstream.update(compare_imported_tree(api, root, upstream))
            except RuntimeError as error:
                upstream["treeComparisonError"] = str(error)
    target = observed["t3"]
    source = api.source(target["repository"], "packages/contracts/src/rpc.ts", target["targetCommit"])
    source += api.source(target["repository"], "packages/contracts/src/orchestrationV2.ts", target["targetCommit"])
    registered, unresolved = methods(source)
    if len(registered) < 50:
        raise RuntimeError("T3 RPC declaration syntax changed; update the coverage parser")
    calls = native_calls(root)
    return {"schemaVersion": 1, "checkedAt": datetime.now(timezone.utc).isoformat(),
            "upstreams": observed, "compatibility": {
                "registeredRpcCount": len(registered), "nativeRpcCount": len(calls),
                "nativeCallsMissingFromTarget": sorted(calls - registered),
                "candidateUnmappedRpcs": sorted(registered - calls),
                "unresolvedAliases": unresolved,
                "note": "Static RPC coverage identifies candidates, not complete feature parity. Verify schemas and UI reachability."}}


def markdown(result):
    lines = ["# Z3 daily upstream report", "", f"Checked: {result['checkedAt']}", ""]
    for name, upstream in result["upstreams"].items():
        repository = upstream["repository"]
        lines += [f"## {name.upper()}", "",
                  f"Release: [{upstream['release'] or 'branch head'}]({upstream['releaseUrl'] or 'https://github.com/' + repository})",
                  f"Target: `{upstream['targetCommit']}`", f"Integrated: `{upstream['integratedCommit']}`", ""]
        if upstream.get("comparisonError"):
            if upstream.get("comparisonStatus") == "tree-diff":
                lines += [f"Upstream history changed; compared the original imported tree directly. {len(upstream['files'])} files differ.", ""]
            else:
                lines += ["History comparison is unavailable. Inspect the target snapshot directly.", ""]
        for item in upstream.get("files", [])[:20]:
            lines.append(f"- {item['status']}: `{item['path']}`")
        lines.append("")
        for commit in upstream.get("commits", [])[:30]:
            title = commit["message"].replace("\n", " ")
            lines.append(f"- [{commit['sha'][:8]}](https://github.com/{repository}/commit/{commit['sha']}): {title}")
        if upstream.get("commitsMayBeTruncated") or upstream.get("filesMayBeTruncated"):
            lines += ["", "This summary is bounded; the JSON marks truncated comparison data."]
        lines.append("")
    compatibility = result["compatibility"]
    missing = compatibility["nativeCallsMissingFromTarget"]
    lines += ["## Native compatibility", "", f"Missing called RPCs: {', '.join(missing) or 'none detected'}.", "",
              compatibility["note"], "", "### Candidate RPC additions", ""]
    lines += [f"- `{method}`" for method in compatibility["candidateUnmappedRpcs"]]
    if compatibility["unresolvedAliases"]:
        lines += ["", "Unresolved declarations require manual parser review: " + ", ".join(compatibility["unresolvedAliases"])]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--github-auth", action="store_true", help="Use the existing gh login locally")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    config = json.loads((args.root / "native/upstreams.json").read_text())
    result = report(Api(args.github_auth), args.root, config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "upstreams.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.output_dir / "upstreams.md").write_text(markdown(result))
    if args.github_output:
        with args.github_output.open("a") as output:
            for name, upstream in result["upstreams"].items():
                output.write(f"{name}_sha={upstream['targetCommit']}\n")
    print(f"Upstream report saved to {args.output_dir}")


if __name__ == "__main__":
    main()
