"""Terminal chat for Mitra. Thin shell: input in, reply out."""

from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_config, resolve_profile
from .orchestrator import build_orchestrator
from .speech import SilentBackend, build_speech


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mitra terminal chat")
    parser.add_argument("-p", dest="project", default="default", help="project name")
    parser.add_argument("-c", dest="config", default=None, help="config file path")
    parser.add_argument(
        "-v", dest="speak", action="store_true", help="speak replies aloud"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print("Mitra cannot start: %s" % exc)
        return 1
    resolved = resolve_profile(config, args.project)
    orch = build_orchestrator(resolved)
    profile_label = args.project if args.project in config.profiles else "default"
    speaker = build_speech(resolved) if args.speak else SilentBackend()
    print(
        "Mitra ready. Project: %s. Profile: %s. Provider: %s. "
        "Type 'help', 'quit' to exit."
        % (args.project, profile_label, resolved.provider.name)
    )
    if args.speak and not speaker.available():
        print("Voice backend '%s' is unavailable. Continuing silent." % speaker.name)
    try:
        while True:
            try:
                message = input("> ").strip()
            except EOFError:
                print()
                break
            if not message:
                continue
            if message.lower() in ("quit", "exit"):
                break
            try:
                response = orch.handle_message(args.project, message)
            except Exception as exc:  # never drop the user into a traceback
                print("Something went wrong: %s" % exc)
                continue
            print(response.text)
            if args.speak:
                speaker.speak(response.text)
    except KeyboardInterrupt:
        print()
    finally:
        orch.close()
    print("Goodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
