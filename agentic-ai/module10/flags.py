"""LaunchDarkly SDK evaluation backed by a reloadable local flag file."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from .control_planes import IntegrationUnavailable

FLAG_KEY = "module10-recovery-planning"
DEFAULT_FLAGS_PATH = Path(__file__).with_name("flags.json")


def flag_path() -> Path:
    return Path(os.getenv("MODULE10_LD_FILE") or DEFAULT_FLAGS_PATH).resolve()


def set_recovery_planning_enabled(path: Path, enabled: bool) -> None:
    """Atomically replace the file so the SDK never reads half-written JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    flag = data["flags"][FLAG_KEY]
    flag["on"] = enabled
    flag["version"] = flag.get("version", 0) + 1
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as output:
        temporary = Path(output.name)
        json.dump(data, output, indent=2)
        output.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class LocalLaunchDarkly:
    """The real server SDK; flag data and evaluation remain on this machine."""

    def __init__(self, path: Path | None = None) -> None:
        from ldclient import LDClient
        from ldclient.config import Config
        from ldclient.integrations import Files

        self.path = (path or flag_path()).resolve()
        if not self.path.is_file():
            raise IntegrationUnavailable(f"LaunchDarkly flag file not found: {self.path}")
        self.client = LDClient(
            Config(
                "module10-local-file",
                update_processor_class=Files.new_data_source(
                    paths=[str(self.path)],
                    auto_update=True,
                    force_polling=True,
                    poll_interval=0.2,
                ),
                send_events=False,
                diagnostic_opt_out=True,
            ),
            start_wait=3,
        )
        # offline=True would skip file evaluation and return fallback values.
        try:
            ready = self.client.is_initialized() and self.evaluate("platform")["reason"].get("kind") != "ERROR"
        except IntegrationUnavailable:
            ready = False
        if not ready:
            self.close()
            raise IntegrationUnavailable(f"Cannot load {FLAG_KEY} from {self.path}")

    def evaluate(self, team: str) -> dict:
        from ldclient import Context

        context = Context.builder(team).kind("team").build()
        state = self.client.all_flags_state(
            context, with_reasons=True, details_only_for_tracked_flags=False,
        ).to_json_dict()
        metadata = state.get("$flagsState", {}).get(FLAG_KEY, {})
        value = state.get(FLAG_KEY)
        if not state.get("$valid") or not isinstance(value, bool) or "version" not in metadata:
            raise IntegrationUnavailable("Recovery-planning flag snapshot is unavailable")
        return {
            "flag_key": FLAG_KEY,
            "recovery_planning_enabled": value,
            "reason": metadata.get("reason", {}),
            "version": metadata["version"],
            "variation_index": metadata.get("variation"),
            "context_kind": "team",
            "context_key": team,
        }

    def status(self) -> dict:
        return {team: self.evaluate(team) for team in ("platform", "payments")}

    def close(self) -> None:
        self.client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Control the local recovery-planning capability")
    parser.add_argument("action", choices=("enable-planner", "disable-planner", "status"))
    parser.add_argument("--file", type=Path, default=flag_path())
    args = parser.parse_args()
    if args.action != "status":
        set_recovery_planning_enabled(args.file, args.action == "enable-planner")
    print(args.file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
