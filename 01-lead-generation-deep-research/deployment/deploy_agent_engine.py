#!/usr/bin/env python3
"""Deploy the lead generation agent to Vertex AI Agent Engine.

Agent Engine is the managed runtime: it handles sessions, scaling and tracing,
which matters here because a deep-research turn can run for minutes.

Usage::

    python deployment/deploy_agent_engine.py --create
    python deployment/deploy_agent_engine.py --list
    python deployment/deploy_agent_engine.py --quicktest --resource_id=<id>
    python deployment/deploy_agent_engine.py --delete --resource_id=<id>

Requires ``pip install -e '.[deploy]'`` and ``gcloud auth application-default
login``.
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

# Make the agent package importable when this script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lead_generation_agent.agent import root_agent  # noqa: E402

FLAGS = flags.FLAGS
flags.DEFINE_string("project_id", None, "GCP project ID. Defaults to env.")
flags.DEFINE_string("location", None, "GCP region. Defaults to env.")
flags.DEFINE_string("bucket", None, "GCS staging bucket, without gs://.")
flags.DEFINE_string("resource_id", None, "Agent Engine resource ID.")
flags.DEFINE_string("user_id", "deploy-smoke-test", "User ID for --quicktest.")
flags.DEFINE_bool("create", False, "Create a new deployment.")
flags.DEFINE_bool("delete", False, "Delete an existing deployment.")
flags.DEFINE_bool("list", False, "List existing deployments.")
flags.DEFINE_bool("quicktest", False, "Send one message to a deployment.")
flags.mark_bool_flags_as_mutual_exclusive(["create", "delete", "list", "quicktest"])

REQUIREMENTS = [
    "google-adk>=2.12.0,<3.0.0",
    "google-cloud-aiplatform[agent_engines]>=1.95.0",
    "google-genai>=1.0.0",
    "pydantic>=2.10.0,<3.0.0",
    "pydantic-settings>=2.6.0",
    "python-dotenv>=1.0.0",
]


def create() -> None:
    """Package the agent and create a new Agent Engine deployment."""
    adk_app = reasoning_engines.AdkApp(agent=root_agent, enable_tracing=True)
    remote_agent = agent_engines.create(
        adk_app,
        display_name="Deep Research Lead Generation Agent",
        description=(
            "Discovers the signals that preceded comparable market entries, then "
            "finds and scores companies showing the same signals today."
        ),
        requirements=REQUIREMENTS,
        extra_packages=["./lead_generation_agent"],
    )
    print(f"Created: {remote_agent.resource_name}")
    print("Record this as REASONING_ENGINE_ID in your .env.")


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


def quicktest(resource_id: str, user_id: str) -> None:
    """Send one message to a live deployment and stream the response."""
    remote_agent = agent_engines.get(resource_id)
    session = remote_agent.create_session(user_id=user_id)
    message = "I sell cross-border payment rails. Find me fintech leads in Thailand."
    print(f"> {message}\n")
    for event in remote_agent.stream_query(
        user_id=user_id, session_id=session["id"], message=message
    ):
        for part in event.get("content", {}).get("parts", []):
            if text := part.get("text"):
                print(text, end="", flush=True)
    print()


def main(argv: list[str]) -> None:
    del argv
    load_dotenv()

    project_id = FLAGS.project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
    location = FLAGS.location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    bucket = FLAGS.bucket or os.getenv("GOOGLE_CLOUD_STORAGE_BUCKET")

    missing = [
        name
        for name, value in (
            ("GOOGLE_CLOUD_PROJECT", project_id),
            ("GOOGLE_CLOUD_LOCATION", location),
            ("GOOGLE_CLOUD_STORAGE_BUCKET", bucket),
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
    elif FLAGS.quicktest:
        if not FLAGS.resource_id:
            raise SystemExit("--quicktest requires --resource_id")
        quicktest(FLAGS.resource_id, FLAGS.user_id)
    else:
        raise SystemExit("Pass one of --create, --delete, --list, --quicktest")


if __name__ == "__main__":
    app.run(main)
