"""Install the pinned, unmodified Portkey source and its locked Node dependencies."""

from pathlib import Path
import shutil
import subprocess

REVISION = "669825cbe89ee51569918b8f78a9db486fd69dd4"
SOURCE = Path(__file__).resolve().parents[1] / ".cache" / "portkey"


def main() -> None:
    if not shutil.which("node") or not shutil.which("npm"):
        raise SystemExit("Install Node.js 24 LTS (including npm), then run setup again.")
    if not (SOURCE / ".git").exists():
        SOURCE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", str(SOURCE)], check=True)
        subprocess.run([
            "git", "-C", str(SOURCE), "remote", "add", "origin",
            "https://github.com/Portkey-AI/gateway.git",
        ], check=True)
    head = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "--verify", "HEAD"],
        capture_output=True, text=True,
    )
    if head.returncode:
        # A failed download can be retried without replacing an existing checkout.
        subprocess.run([
            "git", "-C", str(SOURCE), "fetch", "--depth=1", "origin", REVISION,
        ], check=True)
        subprocess.run([
            "git", "-C", str(SOURCE), "checkout", "--quiet", "--detach", "FETCH_HEAD",
        ], check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True,
    ).strip()
    if revision != REVISION:
        raise SystemExit(f"Expected Portkey {REVISION}; found {revision}. Use a fresh cache directory.")
    subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=SOURCE, check=True)
    print(f"Portkey ready at {SOURCE} ({REVISION[:12]}).")


if __name__ == "__main__":
    main()
