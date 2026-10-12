"""Create independent maintenance checkouts that preserve the local, tested Z3 source."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

SCOPES = ["native", "apps/desktop", "apps/server", "packages/contracts", "pnpm-workspace.yaml", "pnpm-lock.yaml", "package.json", "scripts/build-desktop-artifact.ts",
          "scripts/build-desktop-artifact.test.ts", ".github/workflows/native-t3-linux.yml"]
SOURCE_SUFFIXES = {".rs", ".ts", ".py", ".json", ".yml", ".md"}


def git(root, *arguments, data=None):
    result = subprocess.run(["git", "-C", str(root), *arguments], input=data,
                            capture_output=True)
    if result.returncode:
        raise RuntimeError("Git maintenance preparation failed: " + result.stderr.decode(errors="replace").strip())
    return result.stdout


def allowed(path):
    return any(path == scope or path.startswith(scope + "/") for scope in SCOPES)


def snapshot(source, destination):
    source, destination = source.resolve(), destination.absolute()
    if destination.exists():
        if not (destination / ".git").exists():
            raise RuntimeError("Maintenance destination exists but is not a Git checkout")
        if git(destination, "remote", "get-url", "source").decode().strip() != str(source):
            raise RuntimeError("Maintenance checkout belongs to another source")
        # Repeated setup must not overwrite work from earlier daily runs.
        return git(destination, "rev-parse", "HEAD").decode().strip()
    if destination == source or source in destination.parents:
        raise RuntimeError("Keep the maintenance checkout outside the working source")
    patch = git(source, "diff", "--binary", "HEAD", "--", *SCOPES)
    untracked = [path.decode() for path in git(source, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if path]
    candidates = [path for path in untracked if allowed(path) and Path(path).suffix in SOURCE_SUFFIXES]
    for relative in candidates:
        path = source / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Untracked source must be a regular file: " + relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "clone", "--no-hardlinks", "--dissociate", "--no-checkout", str(source), str(destination)],
                            capture_output=True)
    if result.returncode:
        raise RuntimeError("Independent maintenance clone failed")
    git(destination, "checkout", "-b", "maintenance/daily", git(source, "rev-parse", "HEAD").decode().strip())
    git(destination, "config", "user.name", "Z3 maintenance")
    git(destination, "config", "user.email", "z3-maintenance@users.noreply.github.com")
    git(destination, "remote", "rename", "origin", "source")
    git(destination, "remote", "add", "upstream", "https://github.com/pingdotgg/t3code.git")
    git(destination, "remote", "add", "fork", "https://github.com/DerpcatMusic/z3-code.git")
    git(destination, "remote", "add", "zeron", "https://github.com/zeronsh/zeron.git")
    if patch:
        git(destination, "apply", "--index", "--binary", "-", data=patch)
    for relative in candidates:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, path)
    if candidates:
        git(destination, "add", "--", *candidates)
    if git(destination, "diff", "--cached", "--name-only"):
        git(destination, "commit", "-m", "chore: preserve tested local Z3 maintenance baseline")
    # No alternates means removing the original scratch checkout cannot break maintenance.
    if (destination / ".git/objects/info/alternates").exists():
        raise RuntimeError("Maintenance clone unexpectedly borrows source objects")
    return git(destination, "rev-parse", "HEAD").decode().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-source", type=Path, required=True)
    parser.add_argument("--backend-source", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    state = args.state_dir.absolute()
    state.mkdir(parents=True, exist_ok=True)
    native = state / "native"
    backend = state / "backend"
    result = {"schemaVersion": 1, "repository": "DerpcatMusic/z3-code",
              "nativeDirectory": str(native), "backendDirectory": str(backend),
              "nativeBaseline": snapshot(args.native_source, native),
              "backendBaseline": snapshot(args.backend_source, backend),
              "policy": {"cadence": "daily", "activateAutomatically": False,
                         "guiVerification": "user", "providerTurnsInTests": False}}
    config = state / "config.json"
    temporary = state / "config.json.next"
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    os.replace(temporary, config)
    print("Independent maintenance checkouts ready: " + str(state))


if __name__ == "__main__":
    main()
