"""Lightweight HCL linter for generated projects (no terraform binary required).

Checks structure, variable discipline ("everything variablized"), obvious secrets and
balanced braces. When a `terraform` binary is available, `fmt` and `validate` are run too.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

REQUIRED_FILES = ["providers.tf", "variables.tf", "main.tf", "terraform.tfvars"]

_VAR_REF = re.compile(r"\bvar\.([A-Za-z_][A-Za-z0-9_-]*)")
_VAR_DECL = re.compile(r'^\s*variable\s+"([^"]+)"\s*\{', re.M)
_TFVARS_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=", re.M)
_RESOURCE = re.compile(r'^\s*(resource|module|data)\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{', re.M)
_FOR_EACH_EXEMPT = {"google_project_service"}  # scaffold singleton already iterates a set

_REGION_PREFIX = r"(us|europe|asia|northamerica|southamerica|australia|me|africa|global)"
HARDCODE_RULES = [
    (re.compile(r'^\s*project\s*=\s*"[^"$]+"', re.M), "hard-coded project id; use var.project_id"),
    (re.compile(rf'^\s*(region|location)\s*=\s*"{_REGION_PREFIX}-[a-z0-9-]+"', re.M), "hard-coded region/location; use var.region"),
    (re.compile(rf'^\s*zone\s*=\s*"{_REGION_PREFIX}-[a-z0-9-]+-[a-z]"', re.M), "hard-coded zone; use var.zone"),
    (re.compile(r'^\s*credentials\s*=\s*"', re.M), "credentials file path in code; rely on ADC / WIF"),
]
SECRET_RULES = [
    (re.compile(r'^\s*(password|secret|api_key|private_key|token)\s*=\s*"[^"$]{4,}"', re.M | re.I),
     "plaintext secret literal; source it from google_secret_manager_secret_version or a sensitive variable"),
]


@dataclass
class Finding:
    severity: str  # error | warning | info
    file: str
    line: int | None
    message: str


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _variable_defaults(variables_tf: str) -> dict[str, bool]:
    """{name: has_default} by scanning each variable block with brace counting."""
    out: dict[str, bool] = {}
    for m in _VAR_DECL.finditer(variables_tf):
        name = m.group(1)
        depth, i = 1, m.end()
        body_start = i
        while i < len(variables_tf) and depth:
            ch = variables_tf[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        body = variables_tf[body_start:i]
        # default at depth 1 only
        has_default = bool(re.search(r"^\s*default\s*=", _top_level(body), re.M))
        out[name] = has_default
    return out


def _top_level(body: str) -> str:
    """Return body with nested blocks blanked out (keeps line structure)."""
    out, depth = [], 0
    for ch in body:
        if ch == "{":
            depth += 1
            out.append(" ")
        elif ch == "}":
            depth -= 1
            out.append(" ")
        else:
            out.append(ch if depth <= 0 else (" " if ch != "\n" else "\n"))
    return "".join(out)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)(#|//).*$", "", ln) for ln in text.splitlines())


def _blocks(text: str, kinds: tuple[str, ...] = ("resource",)) -> list[tuple[str, str, str, int]]:
    """Yield (type, name, top_level_body, line) for each block of the given kinds."""
    out = []
    for m in _RESOURCE.finditer(text):
        if m.group(1) not in kinds:
            continue
        depth, i = 1, m.end()
        start = i
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        out.append((m.group(2), m.group(3) or "", _top_level(text[start:i]), _line_of(text, m.start())))
    return out


def _variable_types(variables_tf: str) -> dict[str, str]:
    """{name: first line of the type expression} for warnings about non-map for_each sources."""
    out: dict[str, str] = {}
    for m in _VAR_DECL.finditer(variables_tf):
        depth, i = 1, m.end()
        start = i
        while i < len(variables_tf) and depth:
            depth += variables_tf[i] == "{"
            depth -= variables_tf[i] == "}"
            i += 1
        tm = re.search(r"^\s*type\s*=\s*(.+)$", _top_level(variables_tf[start:i]), re.M)
        out[m.group(1)] = tm.group(1).strip() if tm else ""
    return out


def lint_files(files: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    tf_files = {k: v for k, v in files.items() if k.endswith(".tf")}
    tfvars_files = {k: v for k, v in files.items() if k.endswith(".tfvars")}

    for req in REQUIRED_FILES:
        if req not in files:
            findings.append(Finding("error", req, None, f"required file missing: {req}"))

    # Balanced braces / quotes per file.
    for name, text in {**tf_files, **tfvars_files}.items():
        clean = _strip_comments(text)
        if clean.count("{") != clean.count("}"):
            findings.append(Finding("error", name, None, f"unbalanced braces ({clean.count('{')} '{{' vs {clean.count('}')} '}}')"))
        if clean.count('"') % 2:
            findings.append(Finding("warning", name, None, "odd number of double quotes (possible unterminated string)"))

    # Variable discipline.
    declared: dict[str, bool] = {}
    for name, text in tf_files.items():
        declared.update(_variable_defaults(text))
        if name != "variables.tf" and _VAR_DECL.search(text):
            findings.append(Finding("warning", name, None, "variable declared outside variables.tf"))

    referenced: dict[str, tuple[str, int]] = {}
    for name, text in tf_files.items():
        for m in _VAR_REF.finditer(_strip_comments(text)):
            referenced.setdefault(m.group(1), (name, _line_of(text, m.start())))
    for v, (fname, line) in sorted(referenced.items()):
        if v not in declared:
            findings.append(Finding("error", fname, line, f"var.{v} is referenced but not declared in variables.tf"))

    assigned: set[str] = set()
    for name, text in tfvars_files.items():
        vals = set(_TFVARS_ASSIGN.findall(_top_level(_strip_comments(text))))
        assigned |= vals
        for v in sorted(vals - set(declared)):
            findings.append(Finding("warning", name, None, f"{v} is set in tfvars but no such variable is declared"))
        for m in re.finditer(r"REPLACE_ME", text):
            findings.append(Finding("info", name, _line_of(text, m.start()), "placeholder REPLACE_ME must be filled before apply"))
    for v, has_default in sorted(declared.items()):
        if not has_default and v not in assigned:
            findings.append(Finding("error", "terraform.tfvars", None, f"variable {v} has no default and is not set in any tfvars"))
        if v not in referenced and v not in ("labels",):
            findings.append(Finding("warning", "variables.tf", None, f"variable {v} is declared but never used"))

    # Hard-coded values & secrets in resource files.
    for name, text in tf_files.items():
        if name in ("variables.tf", "providers.tf", "backend.tf"):
            continue
        clean = _strip_comments(text)
        for rx, msg in HARDCODE_RULES:
            for m in rx.finditer(clean):
                findings.append(Finding("error", name, _line_of(clean, m.start()), msg))
        for rx, msg in SECRET_RULES:
            for m in rx.finditer(clean):
                findings.append(Finding("error", name, _line_of(clean, m.start()), msg))
    for name, text in tfvars_files.items():
        for rx, msg in SECRET_RULES:
            for m in rx.finditer(text):
                findings.append(Finding("error", name, _line_of(text, m.start()), msg))

    # for_each discipline: every resource iterates a map so instances are added in tfvars.
    var_types = _variable_types(files.get("variables.tf", ""))
    for name, text in tf_files.items():
        clean = _strip_comments(text)
        for rtype, rname, body, line in _blocks(clean):
            fe = re.search(r"^\s*for_each\s*=\s*(.+)$", body, re.M)
            if re.search(r"^\s*count\s*=", body, re.M):
                findings.append(Finding("error", name, line, f'{rtype}.{rname}: uses count; use for_each over a map variable'))
            if not fe:
                if rtype in _FOR_EACH_EXEMPT:
                    continue
                findings.append(Finding("error", name, line,
                                        f'{rtype}.{rname}: missing for_each. Iterate a map(object(...)) variable so new instances are tfvars entries'))
                continue
            src = fe.group(1).strip()
            vm = re.fullmatch(r"var\.([A-Za-z_][A-Za-z0-9_-]*)", src)
            if vm:
                vtype = var_types.get(vm.group(1), "")
                if vtype and not vtype.startswith(("map(", "map (", "object(")) and "map(" not in vtype:
                    findings.append(Finding("warning", "variables.tf", None,
                                            f"variable {vm.group(1)} drives for_each in {rtype}.{rname} but its type is '{vtype}', not map(object(...))"))
            elif not re.match(r"(local\.|toset\(|\{|merge\(|for\s)", src):
                findings.append(Finding("info", name, line, f"{rtype}.{rname}: for_each source '{src[:40]}' is not a variable or local map"))

    # Structure sanity.
    prov = files.get("providers.tf", "")
    if prov and "required_providers" not in prov:
        findings.append(Finding("error", "providers.tf", None, "missing terraform { required_providers { google = ... } } block"))
    if prov and not re.search(r'provider\s+"google"', prov):
        findings.append(Finding("error", "providers.tf", None, 'missing provider "google" block'))
    main = files.get("main.tf", "")
    resources = _RESOURCE.findall(_strip_comments(main)) + [
        r for n, t in tf_files.items() if n not in ("main.tf", "variables.tf", "providers.tf", "backend.tf", "outputs.tf")
        for r in _RESOURCE.findall(_strip_comments(t))
    ]
    if main and not resources:
        findings.append(Finding("warning", "main.tf", None, "no resource/module blocks found"))
    for m in re.finditer(r"TODO\(architect\)", main):
        findings.append(Finding("warning", "main.tf", _line_of(main, m.start()), "unimplemented component (TODO(architect) left in place)"))
    if "outputs.tf" in files and not re.search(r'^\s*output\s+"', files["outputs.tf"], re.M):
        findings.append(Finding("info", "outputs.tf", None, "no outputs defined"))
    return findings


def terraform_binary() -> str | None:
    env = os.getenv("TERRAFORM_BIN")
    if env and Path(env).exists():
        return env
    return shutil.which("terraform") or shutil.which("tofu")


def run_terraform_checks(project_dir: Path, timeout: int = 180) -> dict:
    """terraform fmt + init(-backend=false) + validate, if a binary is available."""
    tf = terraform_binary()
    if not tf:
        return {"available": False, "hint": "Install terraform (or set TERRAFORM_BIN) to run fmt/validate locally."}
    out: dict = {"available": True, "binary": tf}
    env = {**os.environ, "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}

    def run(args: list[str]) -> tuple[int, str]:
        p = subprocess.run([tf, *args], cwd=project_dir, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout + p.stderr).strip()[-4000:]

    rc, txt = run(["fmt", "-recursive"])
    out["fmt"] = {"ok": rc == 0, "output": txt}
    rc, txt = run(["init", "-backend=false", "-input=false", "-no-color"])
    out["init"] = {"ok": rc == 0, "output": txt[-1500:]}
    if rc == 0:
        rc, txt = run(["validate", "-no-color"])
        out["validate"] = {"ok": rc == 0, "output": txt}
    return out


def findings_to_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]
