"""ADK function tools for Terraform generation."""

from __future__ import annotations

import logging

from google.adk.tools import ToolContext

from .diagram.spec import ArchitectureSpec
from .terraform import lint as tflint
from .terraform import project_io, registry
from .terraform.scaffold import BASE_VARIABLES, scaffold_files, slugify
from .tools import STATE_LAST_SPEC

log = logging.getLogger(__name__)

STATE_TF_PROJECT = "terraform_project"


def get_terraform_resources(product_ids: list[str]) -> dict:
    """Map catalog product ids to their primary Terraform resources (hashicorp/google), APIs and docs.

    Call this first to know which resources implement each component; then call
    fetch_terraform_resource_doc for every resource you are about to write.
    Args:
        product_ids: catalog ids such as ["cloud_run", "cloud_sql", "vpc"].
    """
    out = {}
    unknown = []
    for pid in product_ids:
        info = registry.resources_for_product(pid)
        if info:
            out[pid] = info
        else:
            unknown.append(pid)
    return {"products": out, "unknown": unknown, "provider_latest": registry.latest_provider_version()}


def fetch_terraform_resource_doc(resource: str, max_chars: int = 12000) -> dict:
    """Fetch the OFFICIAL Terraform Google provider documentation for a resource or data source.

    Returns the example usage, the full Argument Reference (required/optional arguments, nested
    blocks) and key attributes, straight from hashicorp/terraform-provider-google docs. You MUST
    read this before writing a resource block so every argument name is real.
    Args:
        resource: e.g. "google_cloud_run_v2_service" or "data.google_project".
        max_chars: response budget (default 12000).
    """
    return registry.fetch_resource_doc(resource, max_chars=max_chars)


def scaffold_terraform_project(name: str, tool_context: ToolContext, region: str = "", force: bool = False) -> dict:
    """Create the Terraform project skeleton for the current design and return its files.

    Generates providers.tf (pinned google/google-beta), backend.tf, variables.tf (core variables with
    validation), terraform.tfvars, main.tf (locals, API enablement, one TODO section per component
    with the exact resources + doc links) and outputs.tf. Files are written under
    output/<slug>/terraform/. Next: implement each TODO with write_terraform_files.
    If the project already exists its files are returned untouched (use force=true to regenerate).
    Args:
        name: project name (slugified for the folder), usually the architecture title.
        region: default region for variables (defaults to us-central1 or the region in the design notes).
        force: overwrite an existing project (discards implemented files).
    """
    spec_dict = tool_context.state.get(STATE_LAST_SPEC)
    spec = ArchitectureSpec.model_validate(spec_dict) if spec_dict else None
    slug = slugify(name)
    existing = project_io.read_files(slug)
    if existing and not force:
        tool_context.state[STATE_TF_PROJECT] = slug
        return {"ok": True, "project": slug, "path": str(project_io.project_dir(slug)), "files": existing,
                "already_existed": True,
                "note": "Project exists; returned current files. Edit with write_terraform_files or pass force=true to regenerate."}
    files, plan = scaffold_files(spec, name, region=region or "us-central1")
    root = project_io.write_files(slug, files)
    tool_context.state[STATE_TF_PROJECT] = slug
    return {
        "ok": True,
        "project": slug,
        "path": str(root),
        "files": files,
        "components_to_implement": plan,
        "base_variables": BASE_VARIABLES,
        "rules": [
            "Every resource uses for_each over a typed map(object({...})) variable (see each component's map_variable); never count, never singletons. New instances are tfvars entries.",
            "No literal project ids, regions, zones, names or secrets in main.tf: add a typed variable to variables.tf and a value to terraform.tfvars.",
            "Name resources with local.name_prefix; label with local.labels.",
            "Add depends_on = [google_project_service.apis] to resources that need a newly enabled API.",
            "Read fetch_terraform_resource_doc for every resource before writing it.",
        ],
    }


def write_terraform_files(files: dict, tool_context: ToolContext, project: str = "") -> dict:
    """Write or overwrite files in the Terraform project, then lint (and run terraform fmt/validate if installed).

    Pass complete file contents (not diffs). Typical call: {"main.tf": "...", "variables.tf": "...",
    "terraform.tfvars": "...", "outputs.tf": "..."}. Subfolders such as "modules/network/main.tf"
    are allowed. Returns lint findings: fix every `error` and re-call until clean.
    Args:
        files: mapping of relative file path -> full content.
        project: project slug (defaults to the one created by scaffold_terraform_project).
    """
    slug = project or tool_context.state.get(STATE_TF_PROJECT)
    if not slug:
        return {"ok": False, "error": "No Terraform project yet; call scaffold_terraform_project first."}
    try:
        root = project_io.write_files(slug, files)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    tool_context.state[STATE_TF_PROJECT] = slug
    return _report(slug, root)


def lint_terraform_project(tool_context: ToolContext, project: str = "") -> dict:
    """Re-run the structural lint + terraform fmt/validate on the current project without changing files."""
    slug = project or tool_context.state.get(STATE_TF_PROJECT)
    if not slug:
        return {"ok": False, "error": "No Terraform project yet."}
    return _report(slug, project_io.project_dir(slug))


def read_terraform_project(tool_context: ToolContext, project: str = "") -> dict:
    """Return every file of the current Terraform project (to make targeted edits)."""
    slug = project or tool_context.state.get(STATE_TF_PROJECT)
    if not slug:
        return {"found": False}
    files = project_io.read_files(slug)
    return {"found": bool(files), "project": slug, "path": str(project_io.project_dir(slug)), "files": files}


def _report(slug: str, root) -> dict:
    files = project_io.read_files(slug)
    findings = tflint.lint_files(files)
    tf = tflint.run_terraform_checks(root)
    errors = [f for f in findings if f.severity == "error"]
    ok = not errors and tf.get("validate", {}).get("ok", True) and tf.get("init", {}).get("ok", True)
    return {
        "ok": ok,
        "project": slug,
        "path": str(root),
        "tree": sorted(files.keys()),
        "lint": tflint.findings_to_dicts(findings),
        "terraform": tf,
        "next": "Fix all lint errors and re-call write_terraform_files." if errors else "Clean. Summarise the project for the user.",
    }
