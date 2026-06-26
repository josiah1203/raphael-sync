"""Run the Calliope local agent."""

from raphael_sync.calliope_agent.config import load_config
from raphael_sync.calliope_agent.server import run_server


def main() -> None:
    run_server(load_config())


if __name__ == "__main__":
    main()
