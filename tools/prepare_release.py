"""Build a source-only public release without development history or local data."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitignore", ".dockerignore", "AGENTS.md", "README.md", "DESIGN.md", "CONTRIBUTING.md",
    "CHANGELOG.md", "VERSION", "Dockerfile", "requirements.txt", "requirements-dev.txt",
    "server.py", "settings.py", "logtext.py", "captioning.py", "translation.py", "live.py", "access.py", "routes_live.py",
    "routes_materials.py", "routes_session.py", "routes_records.py", "security.py", "event.py", "deck_context.py", "materials.py", "pdf_worker.py", "workloads.py",
    "setup_event.py", "start.sh",
    "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md",
}
OPTIONAL_ROOT_FILES = set()
TREES = {
    "static": {".html", ".css", ".js"},
    "postprocess": {".py", ".md", ".css"},
    "glossary": {".toml"},
    "skills": {".md"},
    "tests": {".py", ".md", ".json"},
    "tools": {".py", ".sh"},
}
EXACT = {
    "events/_base.toml", "events/general.toml", "events/example.toml",
    "infra/app.py", "infra/cdk.json", "infra/requirements.txt", "infra/local.example.json",
    "docs/architecture.md", "docs/operations.md",
    "postprocess/writeup-policy.md",
    ".agents/skills/session-writeup/SKILL.md",
    ".agents/skills/session-writeup/agents/openai.yaml",
    ".agents/skills/session-writeup/references/examples.md",
}
CHECKS = {
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal home path": re.compile(r"/(?:Users|home)/[a-zA-Z][a-zA-Z0-9._-]+/"),
    "concrete CloudFront URL": re.compile(r"https?://d[a-z0-9]{10,}\.cloudfront\.net"),
    "embedded font": re.compile(r"data:font/(?:woff2?|ttf|otf);base64,"),
    "account identifier": re.compile(r"(?<![A-Za-z0-9])[0-9]{12}(?![A-Za-z0-9])"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
    "provider key": re.compile(r"\b(?:sk-ant-|sk-proj-)[A-Za-z0-9_-]{20,}\b"),
    "corporate email": re.compile(r"[\w.+-]+@amazon\.com\b", re.I),
    "internal host": re.compile(r"\b(?:w|wiki|code|phonetool|midway|policy|issues)\.amazon\.(?:com|dev)\b"),
    "infrastructure identifier": re.compile(r"\b(?:vpc|subnet|sg|vpce)-[a-f0-9]{8,17}\b"),
}
PUBLIC_EMAIL = re.compile(r"(?:\d+\+)?[A-Za-z0-9-]+@users\.noreply\.github\.com")


def allowed(path):
    parts = path.parts
    if path.as_posix() in ROOT_FILES | OPTIONAL_ROOT_FILES | EXACT:
        return True
    if any(part.startswith(".") or part == "__pycache__" for part in parts):
        return False
    return (
        len(parts) > 1 and parts[0] in TREES and path.suffix in TREES[parts[0]]
    )


def inspect(path, data):
    text = data.decode("utf-8")
    findings = []
    for kind, pattern in CHECKS.items():
        for match in pattern.finditer(text):
            if kind == "account identifier" and match.group() in ("123456789012", "000000000000"):
                continue
            findings.append(kind)
    if findings:
        # Report categories and paths, never the matched private value.
        raise ValueError(f"{path}: " + ", ".join(sorted(set(findings))))


def verify_public_history(root):
    """Check every reachable commit, including deleted files and identity metadata."""
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root)
    blobs = {}
    commits = git("rev-list", "--all").decode().splitlines()
    if not commits:
        raise ValueError("The public repository has no commits.")
    for commit in commits:
        metadata = git("show", "-s", "--format=%ae%n%ce%n%B", commit).decode()
        author, committer, message = metadata.split("\n", 2)
        if not all(PUBLIC_EMAIL.fullmatch(email) for email in (author, committer)):
            raise ValueError(f"{commit[:8]}: public commits require GitHub noreply author and committer emails.")
        inspect(Path("commit-message"), message.encode())
        for row in git("ls-tree", "-r", "-z", commit).split(b"\0"):
            if not row:
                continue
            details, name = row.split(b"\t", 1)
            mode, kind, identity = details.split()
            path = Path(name.decode())
            if mode not in (b"100644", b"100755") or kind != b"blob" or (
                    path.as_posix() != "SHA256SUMS" and not allowed(path)):
                raise ValueError(f"Private or unsupported historical file: {path}")
            blobs.setdefault(identity.decode(), path)
    for identity, path in blobs.items():
        inspect(path, git("cat-file", "blob", identity))
    print(f"Public Git verified: {len(commits)} commits, {len(blobs)} file objects.")


def build(root, *, init_git=False, git_name=None, git_email=None):
    if init_git and (not git_name or not git_email or not PUBLIC_EMAIL.fullmatch(git_email)):
        raise ValueError("--init-git requires --git-name and a GitHub noreply --git-email.")
    version = (root / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?", version):
        raise ValueError("VERSION must identify a source release.")
    listed = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    ).decode().split("\0")
    files = {}
    for name in sorted(set(listed)):
        relative = Path(name)
        source = root / relative
        if not name or not allowed(relative) or not source.is_file():
            continue
        if source.is_symlink():
            raise ValueError(f"Symlinks are not included: {relative}")
        data = source.read_bytes()
        inspect(relative, data)
        files[relative] = data
    for required in ROOT_FILES | EXACT:
        if Path(required) not in files:
            raise ValueError(f"Required release file missing: {required}")
    releases = root / "out/releases"
    releases.mkdir(parents=True, exist_ok=True)
    container = Path(tempfile.mkdtemp(prefix=f"v{version}-", dir=releases))
    destination = container / f"live-interpreter-{version}"
    destination.mkdir()
    manifest = []
    for relative, data in files.items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if relative.suffix == ".sh":
            path.chmod(0o755)
        manifest.append(f"{hashlib.sha256(data).hexdigest()}  {relative.as_posix()}")
    (destination / "SHA256SUMS").write_text("\n".join(manifest) + "\n")
    archive = container / f"live-interpreter-{version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(container))
    if init_git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=destination, check=True)
        subprocess.run(["git", "config", "user.name", git_name], cwd=destination, check=True)
        subprocess.run(["git", "config", "user.email", git_email], cwd=destination, check=True)
        subprocess.run(["git", "add", "."], cwd=destination, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"Initial public source v{version}"], cwd=destination, check=True)
        verify_public_history(destination)
    print(f"Source files: {len(files)}")
    print(f"Repository: {destination}")
    print(f"ZIP: {archive}")
    print("No remote configured. No files published.")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-git", action="store_true", help="create a fresh local Git history")
    parser.add_argument("--git-name", help="public commit display name")
    parser.add_argument("--git-email", help="GitHub noreply address from your GitHub email settings")
    parser.add_argument("--verify-public-git", type=Path, help="check all reachable public commits without changing files")
    args = parser.parse_args()
    if args.verify_public_git:
        verify_public_history(args.verify_public_git)
    else:
        build(ROOT, init_git=args.init_git, git_name=args.git_name, git_email=args.git_email)
