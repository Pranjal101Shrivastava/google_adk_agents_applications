#!/usr/bin/env python3
"""Deploy the code review assistant to Vertex AI Agent Engine.

Note on code execution: Agent Engine runs a managed Python runtime. The
subprocess-with-rlimits approach in ``tools/execution.py`` may be restricted
there. If a deployment reports every test run as an exec error, set
``ENABLE_CODE_EXECUTION=false`` for it — the grade then reports correctness as
"not assessed" and redistributes its weight, which is honest, rather than
silently scoring zero. For a deployment that must run tests, prefer Cloud Run
with ``--execution-environment=gen2``.

Usage::

    python deployment/deploy_agent_engine.py --create
    python deployment/deploy_agent_engine.py --list
    python deployment/deploy_agent_engine.py --delete --resource_id=<id>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import vertexai
from absl import app, flags
from dotenv import load_dotenv
from vertexai import agent_engines
from vertexai.preview import reasoning_engines

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from code_review_assistant.agent import root_agent  # noqa: E402

FLAGS = flags.FLAGS
flags.DEFINE_string("project_id", None, "GCP project ID. Defaults to env.")
flags.DEFINE_string("location", None, "GCP region. Defaults to env.")
flags.DEFINE_string("bucket", None, "GCS staging bucket, without gs://.")
flags.DEFINE_string("resource_id", None, "Agent Engine resource ID.")
flags.DEFINE_bool("create", False, "Create a new deployment.")
flags.DEFINE_bool("delete", False, "Delete an existing deployment.")
flags.DEFINE_bool("list", False, "List existing deployments.")
flags.mark_bool_flags_as_mutual_exclusive(["create", "delete", "list"])

REQUIREMENTS = [
    "google-adk>=2.12.0,<3.0.0",
    "google-cloud-aiplatform[agent_engines]>=1.95.0",
    "google-genai>=1.0.0",
    "pycodestyle>=2.12.0",
    "pydantic>=2.10.0,<3.0.0",
    "pydantic-settings>=2.6.0",
    "python-dotenv>=1.0.0",
    "pytest>=8.3.0",
]


def create() -> None:
    """Package the agent and create a new Agent Engine deployment."""
    adk_app = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=True)
    remote_agent = agent_engines.create(
        adk_app,
        display_name="Code Review Assistant",
        description=(
            "Reviews Python for structure, style, security and correctness, "
            "grades it deterministically, and can fix what it found."
        ),
        requirements=REQUIREMENTS,
        extra_packages=["./code_review_assistant"],
    )
    print(f"Created: {remote_agent.resource_name}")


def delete(resource_id: str) -> None:
    """Delete a deployment and everything it owns."""
    agent_engines.get(resource_id).delete(force=True)
    print(f"Deleted: {resource_id}")


def list_deployments() -> None:
    """Print every Agent Engine deployment in the project."""
    deployments = list(agent_engines.list())
    if not deployments:
        print("No deployments found.")
        return
    for deployment in deployments:
        print(f"{deployment.resource_name}  ({deployment.display_name})")


def main(argv: list[str]) -> None:
    del argv
    load_dotenv()

    project_id = FLAGS.project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = FLAGS.location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    bucket = FLAGS.bucket or os.getenv("STAGING_BUCKET")

    missing = [
        name
        for name, value in (
            ("GOOGLE_CLOUD_PROJECT", project_id),
            ("STAGING_BUCKET", bucket),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required configuration: {', '.join(missing)}")

    vertexai.init(project=project_id, location=location, staging_bucket=f"gs://{bucket}")

    if FLAGS.create:
        create()
    elif FLAGS.delete:
        if not FLAGS.resource_id:
            raise SystemExit("--delete requires --resource_id")
        delete(FLAGS.resource_id)
    elif FLAGS.list:
        list_deployments()
    else:
        raise SystemExit("Pass one of --create, --delete, --list")


if __name__ == "__main__":
    app.run(main)
