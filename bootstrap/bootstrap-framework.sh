#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

###############################################################################
# Rules-Engine Framework Bootstrap
#
# Purpose:
#   Deterministically extract reusable framework infrastructure from a pinned
#   configured source baseline into an independent, domain-agnostic repository.
#
# Important:
#   - This script must never modify the configured source repository.
#   - This script must never commit, push, merge, or otherwise mutate GitHub.
#   - Mechanical transformations belong here.
#   - Semantic/generalization decisions do not.
###############################################################################


###############################################################################
# 00 — Runtime, configuration, logging, CLI
###############################################################################

readonly SCRIPT_VERSION="1.0.2"

SOURCE_DIR=""
SOURCE_REPO=""
SOURCE_REF=""
SOURCE_PREFIX=""

TARGET_DIR=""
TARGET_REPO=""

FRAMEWORK_ID=""
FRAMEWORK_NAME=""
FRAMEWORK_PREFIX=""

DRY_RUN=false
VERBOSE=false


log()
{
    printf '[bootstrap] %s
' "$*"
}


log_verbose()
{
    if [[ "${VERBOSE}" == true ]]; then
        printf '[bootstrap:verbose] %s
' "$*"
    fi
}


warn()
{
    printf '[bootstrap:warning] %s
' "$*" >&2
}


die()
{
    printf '[bootstrap:error] %s
' "$*" >&2
    return 1
}


phase()
{
    printf '
'
    printf '===============================================================================
'
    printf '%s
' "$*"
    printf '===============================================================================
'
}


on_error()
{
    local status=$?
    local line_number="${1:-unknown}"

    printf '
' >&2
    printf '[bootstrap:error] command failed at line %s with status %s
' \
        "${line_number}" \
        "${status}" >&2

    return "${status}"
}


trap 'on_error "${LINENO}"' ERR


usage()
{
    cat <<EOF_USAGE
Rules-engine framework bootstrap ${SCRIPT_VERSION}

Usage:
  $(basename "$0") [options]

Required options:
  --source-dir PATH
      Local path to the source repository checkout.

  --source-repo OWNER/REPO
      Expected GitHub repository identity for the source.

  --source-ref REF
      Required source revision. For reproducible extraction, use a full
      40-character commit SHA.

  --source-prefix PREFIX
      Source project/namespace prefix to replace mechanically where explicitly
      authorized by the rename map.

  --target-dir PATH
      Local path to the target framework repository checkout.

  --target-repo OWNER/REPO
      Expected GitHub repository identity for the target.

  --framework-id ID
      Machine-readable framework identifier.

  --framework-name NAME
      Human-readable framework name.

  --framework-prefix PREFIX
      Target project/namespace prefix used by declared mechanical renames.

Optional:
  --dry-run
      Report intended changes without performing them.

  --verbose
      Enable additional diagnostic output.

  --help
      Show this help text.

Example:
  $(basename "$0") \
      --source-dir /workspace/source \
      --source-repo owner/source-repo \
      --source-ref 0123456789abcdef0123456789abcdef01234567 \
      --source-prefix SourceName \
      --target-dir /workspace/framework \
      --target-repo owner/framework-repo \
      --framework-id framework \
      --framework-name "Framework" \
      --framework-prefix Framework \
      --dry-run
EOF_USAGE
}


require_option_value()
{
    local option_name="$1"
    local remaining_count="$2"

    if ((remaining_count < 2)); then
        die "${option_name} requires a value."
        return 1
    fi
}


parse_args()
{
    while (($# > 0)); do
        case "$1" in
            --source-dir)
                require_option_value "$1" "$#"
                SOURCE_DIR="$2"
                shift 2
                ;;

            --source-repo)
                require_option_value "$1" "$#"
                SOURCE_REPO="$2"
                shift 2
                ;;

            --source-ref)
                require_option_value "$1" "$#"
                SOURCE_REF="$2"
                shift 2
                ;;

            --source-prefix)
                require_option_value "$1" "$#"
                SOURCE_PREFIX="$2"
                shift 2
                ;;

            --target-dir)
                require_option_value "$1" "$#"
                TARGET_DIR="$2"
                shift 2
                ;;

            --target-repo)
                require_option_value "$1" "$#"
                TARGET_REPO="$2"
                shift 2
                ;;

            --framework-id)
                require_option_value "$1" "$#"
                FRAMEWORK_ID="$2"
                shift 2
                ;;

            --framework-name)
                require_option_value "$1" "$#"
                FRAMEWORK_NAME="$2"
                shift 2
                ;;

            --framework-prefix)
                require_option_value "$1" "$#"
                FRAMEWORK_PREFIX="$2"
                shift 2
                ;;

            --dry-run)
                DRY_RUN=true
                shift
                ;;

            --verbose)
                VERBOSE=true
                shift
                ;;

            --help|-h)
                usage
                return 2
                ;;

            *)
                die "Unknown argument: $1"
                return 1
                ;;
        esac
    done
}


require_configuration()
{
    local missing=false

    if [[ -z "${SOURCE_DIR}" ]]; then
        warn "Missing required argument: --source-dir"
        missing=true
    fi

    if [[ -z "${SOURCE_REPO}" ]]; then
        warn "Missing required argument: --source-repo"
        missing=true
    fi

    if [[ -z "${SOURCE_REF}" ]]; then
        warn "Missing required argument: --source-ref"
        missing=true
    fi

    if [[ -z "${SOURCE_PREFIX}" ]]; then
        warn "Missing required argument: --source-prefix"
        missing=true
    fi

    if [[ -z "${TARGET_DIR}" ]]; then
        warn "Missing required argument: --target-dir"
        missing=true
    fi

    if [[ -z "${TARGET_REPO}" ]]; then
        warn "Missing required argument: --target-repo"
        missing=true
    fi

    if [[ -z "${FRAMEWORK_ID}" ]]; then
        warn "Missing required argument: --framework-id"
        missing=true
    fi

    if [[ -z "${FRAMEWORK_NAME}" ]]; then
        warn "Missing required argument: --framework-name"
        missing=true
    fi

    if [[ -z "${FRAMEWORK_PREFIX}" ]]; then
        warn "Missing required argument: --framework-prefix"
        missing=true
    fi

    if [[ "${missing}" == true ]]; then
        die "Required bootstrap configuration is incomplete."
        return 1
    fi
}


print_runtime_configuration()
{
    log "Bootstrap version: ${SCRIPT_VERSION}"
    log "Source directory: ${SOURCE_DIR}"
    log "Source repository: ${SOURCE_REPO}"
    log "Source ref: ${SOURCE_REF}"
    log "Source prefix: ${SOURCE_PREFIX}"
    log "Target directory: ${TARGET_DIR}"
    log "Target repository: ${TARGET_REPO}"
    log "Framework ID: ${FRAMEWORK_ID}"
    log "Framework name: ${FRAMEWORK_NAME}"
    log "Framework prefix: ${FRAMEWORK_PREFIX}"
    log "Dry run: ${DRY_RUN}"
    log "Verbose: ${VERBOSE}"

    if [[ "${GITHUB_ACTIONS:-false}" == "true" ]]; then
        log "Execution environment: github-actions"
    else
        log "Execution environment: local"
    fi
}


###############################################################################
# Shared safety helpers
###############################################################################

require_command()
{
    local command_name="$1"

    if ! command -v "${command_name}" >/dev/null 2>&1; then
        die "Required command not found: ${command_name}"
        return 1
    fi

    log_verbose "Found command: ${command_name}"
}


require_absolute_path()
{
    local label="$1"
    local path_value="$2"

    if [[ -z "${path_value}" ]]; then
        die "${label} path is empty."
        return 1
    fi

    if [[ "${path_value}" != /* ]]; then
        die "${label} path must be absolute: ${path_value}"
        return 1
    fi
}


canonicalize_path()
{
    readlink -m -- "$1"
}


###############################################################################
# 01 — Preflight
###############################################################################

phase_01_preflight()
{
    local source_resolved
    local target_resolved

    phase "01 — Preflight"

    if ((BASH_VERSINFO[0] < 5)); then
        die "Bash 5 or newer is required. Found: ${BASH_VERSION}"
        return 1
    fi

    log_verbose "Bash version: ${BASH_VERSION}"

    require_command git
    require_command python3
    require_command readlink
    require_command mktemp
    require_command cmp
    require_command mkdir
    require_command mv
    require_command wc
    require_command tar
    require_command rm

    require_absolute_path "Source" "${SOURCE_DIR}"
    require_absolute_path "Target" "${TARGET_DIR}"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    log_verbose "Resolved source: ${source_resolved}"
    log_verbose "Resolved target: ${target_resolved}"

    if [[ "${source_resolved}" == "${target_resolved}" ]]; then
        die "Source and target resolve to the same directory: ${source_resolved}"
        return 1
    fi

    if [[ "${target_resolved}" == "/"
        || "${target_resolved}" == "/home" ]]; then
        die "Refusing unsafe target directory: ${target_resolved}"
        return 1
    fi

    if [[ -n "${HOME:-}" ]]; then
        local home_resolved
        home_resolved="$(canonicalize_path "${HOME}")"

        if [[ "${target_resolved}" == "${home_resolved}" ]]; then
            die "Refusing target directory equal to HOME: ${target_resolved}"
            return 1
        fi
    fi

    if [[ "${target_resolved}" == "${source_resolved}/"* ]]; then
        die "Target may not be inside the source repository."
        return 1
    fi

    if [[ "${source_resolved}" == "${target_resolved}/"* ]]; then
        die "Source may not be inside the target repository."
        return 1
    fi

    log "PASS: runtime and path safety checks completed."
}


###############################################################################
# Git repository helpers
###############################################################################

normalize_repository_slug()
{
    local repository="$1"

    repository="${repository%.git}"

    case "${repository}" in
        https://github.com/*)
            printf '%s
' "${repository#https://github.com/}"
            ;;

        http://github.com/*)
            printf '%s
' "${repository#http://github.com/}"
            ;;

        git@github.com:*)
            printf '%s
' "${repository#git@github.com:}"
            ;;

        ssh://git@github.com/*)
            printf '%s
' "${repository#ssh://git@github.com/}"
            ;;

        */*)
            if [[ "${repository}" != *"://"* && "${repository}" != *@*:* ]]; then
                printf '%s
' "${repository}"
            else
                return 1
            fi
            ;;

        *)
            return 1
            ;;
    esac
}


###############################################################################
# 02 — Verify configured source repository and revision
###############################################################################

phase_02_verify_source()
{
    local source_resolved
    local git_root
    local actual_commit
    local expected_commit
    local remote_url
    local remote_repository
    local expected_repository
    local status_output

    phase "02 — Verify configured source repository and revision"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"

    if [[ ! -d "${source_resolved}" ]]; then
        die "Source directory does not exist: ${source_resolved}"
        return 1
    fi

    if ! git -C "${source_resolved}" rev-parse \
        --is-inside-work-tree >/dev/null 2>&1; then
        die "Source is not a Git worktree: ${source_resolved}"
        return 1
    fi

    git_root="$(
        git -C "${source_resolved}" rev-parse --show-toplevel
    )"
    git_root="$(canonicalize_path "${git_root}")"

    if [[ "${git_root}" != "${source_resolved}" ]]; then
        die "Source path must be the repository root. Source: ${source_resolved}; Git root: ${git_root}"
        return 1
    fi

    remote_url="$(
        git -C "${source_resolved}" remote get-url origin 2>/dev/null
    )" || {
        die "Source repository has no readable origin remote."
        return 1
    }

    remote_repository="$(
        normalize_repository_slug "${remote_url}"
    )" || {
        die "Source origin is not a recognized GitHub repository URL: ${remote_url}"
        return 1
    }

    expected_repository="$(
        normalize_repository_slug "${SOURCE_REPO}"
    )" || {
        die "Invalid configured source repository: ${SOURCE_REPO}"
        return 1
    }

    if [[ "${remote_repository,,}" != "${expected_repository,,}" ]]; then
        die "Unexpected source repository. Expected: ${expected_repository}; Actual: ${remote_repository}"
        return 1
    fi

    actual_commit="$(
        git -C "${source_resolved}" rev-parse HEAD
    )"

    expected_commit="$(
        git -C "${source_resolved}" rev-parse "${SOURCE_REF}^{commit}" 2>/dev/null
    )" || {
        die "Configured source ref cannot be resolved locally: ${SOURCE_REF}"
        return 1
    }

    if [[ "${actual_commit}" != "${expected_commit}" ]]; then
        die "Source HEAD does not match configured source ref. Expected: ${expected_commit}; Actual: ${actual_commit}"
        return 1
    fi

    status_output="$(
        git -C "${source_resolved}" status \
            --porcelain=v1 \
            --untracked-files=all
    )"

    if [[ -n "${status_output}" ]]; then
        printf '%s
' "${status_output}" >&2
        die "Source working tree is not clean."
        return 1
    fi

    log_verbose "Source repository: ${remote_repository}"
    log_verbose "Source origin: ${remote_url}"
    log_verbose "Source root: ${source_resolved}"
    log_verbose "Configured source ref: ${SOURCE_REF}"
    log_verbose "Resolved source commit: ${actual_commit}"

    log "PASS: source repository identity and revision verified."
}


###############################################################################
# 03 — Verify configured target repository
###############################################################################

phase_03_verify_target()
{
    local source_resolved
    local target_resolved
    local git_root
    local remote_url
    local remote_repository
    local expected_repository
    local target_head

    phase "03 — Verify configured target repository"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    if [[ ! -d "${target_resolved}" ]]; then
        die "Target directory does not exist: ${target_resolved}"
        return 1
    fi

    if ! git -C "${target_resolved}" rev-parse \
        --is-inside-work-tree >/dev/null 2>&1; then
        die "Target is not a Git worktree: ${target_resolved}"
        return 1
    fi

    git_root="$(
        git -C "${target_resolved}" rev-parse --show-toplevel
    )"
    git_root="$(canonicalize_path "${git_root}")"

    if [[ "${git_root}" != "${target_resolved}" ]]; then
        die "Target path must be the repository root. Target: ${target_resolved}; Git root: ${git_root}"
        return 1
    fi

    if [[ "${target_resolved}" == "${source_resolved}" ]]; then
        die "Target repository resolves to the source repository."
        return 1
    fi

    remote_url="$(
        git -C "${target_resolved}" remote get-url origin 2>/dev/null
    )" || {
        die "Target repository has no readable origin remote."
        return 1
    }

    remote_repository="$(
        normalize_repository_slug "${remote_url}"
    )" || {
        die "Target origin is not a recognized GitHub repository URL: ${remote_url}"
        return 1
    }

    expected_repository="$(
        normalize_repository_slug "${TARGET_REPO}"
    )" || {
        die "Invalid configured target repository: ${TARGET_REPO}"
        return 1
    }

    if [[ "${remote_repository,,}" != "${expected_repository,,}" ]]; then
        die "Unexpected target repository. Expected: ${expected_repository}; Actual: ${remote_repository}"
        return 1
    fi

    log_verbose "Target repository: ${remote_repository}"
    log_verbose "Target origin: ${remote_url}"
    log_verbose "Target root: ${target_resolved}"

    if target_head="$(
        git -C "${target_resolved}" rev-parse --verify HEAD 2>/dev/null
    )"; then
        log_verbose "Target HEAD: ${target_head}"
    else
        log_verbose "Target HEAD: unborn"
    fi

    log "PASS: target repository identity verified."
}


###############################################################################
# 04 — Capture canonical source inventory
###############################################################################

phase_04_capture_inventory()
{
    local source_resolved
    local source_commit
    local source_tree
    local source_repository
    local destination
    local destination_dir
    local temporary_file
    local file_count

    phase "04 — Capture canonical source inventory"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"

    source_repository="$(
        normalize_repository_slug "${SOURCE_REPO}"
    )" || {
        die "Invalid configured source repository: ${SOURCE_REPO}"
        return 1
    }

    source_commit="$(
        git -C "${source_resolved}" rev-parse "${SOURCE_REF}^{commit}"
    )"

    source_tree="$(
        git -C "${source_resolved}" rev-parse "${source_commit}^{tree}"
    )"

    file_count="$(
        git -C "${source_resolved}" ls-tree \
            -r \
            --full-tree \
            "${source_commit}" |
        wc -l
    )"
    file_count="${file_count//[[:space:]]/}"

    destination="${TARGET_DIR}/bootstrap/generated/source-inventory.txt"
    destination_dir="${destination%/*}"

    temporary_file="$(mktemp)"

    {
        printf '# Source repository: %s\n' "${source_repository}"
        printf '# Source commit: %s\n' "${source_commit}"
        printf '# Source tree: %s\n' "${source_tree}"
        printf '# Tracked entries: %s\n' "${file_count}"
        printf '# Format: git ls-tree -r --full-tree\n'
        printf '\n'

        git -C "${source_resolved}" ls-tree \
            -r \
            --full-tree \
            "${source_commit}"
    } > "${temporary_file}"

    log_verbose "Resolved source commit: ${source_commit}"
    log_verbose "Resolved source tree: ${source_tree}"
    log_verbose "Tracked source entries: ${file_count}"
    log_verbose "Inventory destination: ${destination}"

    if [[ "${DRY_RUN}" == true ]]; then
        log "DRY RUN: would write canonical source inventory to bootstrap/generated/source-inventory.txt"
        rm -f -- "${temporary_file}"
        log "PASS: canonical source inventory generated and verified."
        return 0
    fi

    mkdir -p -- "${destination_dir}"

    if [[ -f "${destination}" ]] && cmp -s -- "${temporary_file}" "${destination}"; then
        rm -f -- "${temporary_file}"
        log "UNCHANGED: canonical source inventory already matches."
    else
        mv -- "${temporary_file}" "${destination}"
        log "WROTE: bootstrap/generated/source-inventory.txt"
    fi

    log "PASS: canonical source inventory captured."
}


###############################################################################
# 05 — Copy explicitly approved content
###############################################################################

phase_05_copy_content()
{
    local source_resolved
    local target_resolved
    local source_commit
    local manifest
    local rename_map
    local rename_engine
    local path_map
    local path_map_engine
    local staging_dir
    local entry

    local -a approved_paths=()

    phase "05 — Copy explicitly approved content"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    source_commit="$(
        git -C "${source_resolved}" rev-parse "${SOURCE_REF}^{commit}"
    )"

    manifest="${target_resolved}/bootstrap/config/copy-manifest.txt"
    rename_map="${target_resolved}/bootstrap/config/rename-map.json"
    rename_engine="${target_resolved}/bootstrap/lib/rename_engine.py"
    path_map="${target_resolved}/bootstrap/config/path-map.json"
    path_map_engine="${target_resolved}/bootstrap/lib/path_map_engine.py"

    if [[ ! -f "${manifest}" ]]; then
        die "Copy manifest does not exist: ${manifest}"
        return 1
    fi

    if [[ ! -f "${rename_map}" ]]; then
        die "Rename map does not exist: ${rename_map}"
        return 1
    fi

    if [[ ! -f "${rename_engine}" ]]; then
        die "Rename engine does not exist: ${rename_engine}"
        return 1
    fi

    if [[ ! -f "${path_map}" ]]; then
        die "Path map does not exist: ${path_map}"
        return 1
    fi

    if [[ ! -f "${path_map_engine}" ]]; then
        die "Path-map engine does not exist: ${path_map_engine}"
        return 1
    fi

    while IFS= read -r entry || [[ -n "${entry}" ]]; do
        entry="${entry%$'
'}"

        if [[ -z "${entry}" || "${entry}" == \#* ]]; then
            continue
        fi

        if [[ "${entry}" == /* \
            || "${entry}" == ".." \
            || "${entry}" == ../* \
            || "${entry}" == */../* \
            || "${entry}" == */.. \
            || "${entry}" == ".git" \
            || "${entry}" == .git/* ]]; then
            die "Unsafe path in copy manifest: ${entry}"
            return 1
        fi

        if ! git -C "${source_resolved}" cat-file \
            -e "${source_commit}:${entry}" 2>/dev/null; then
            die "Manifest path does not exist in source commit: ${entry}"
            return 1
        fi

        approved_paths+=("${entry}")
    done < "${manifest}"

    if ((${#approved_paths[@]} == 0)); then
        die "Copy manifest contains no approved paths."
        return 1
    fi

    log_verbose "Approved top-level copy entries: ${#approved_paths[@]}"

    staging_dir="$(mktemp -d)"

    if ! git -C "${source_resolved}" archive \
        --format=tar \
        "${source_commit}" \
        -- "${approved_paths[@]}" |
        tar -xf - -C "${staging_dir}"; then
        rm -rf -- "${staging_dir}"
        die "Failed to stage approved source content."
        return 1
    fi

    if ! python3 - \
        "${staging_dir}" \
        "${target_resolved}" \
        "${DRY_RUN}" \
        "${rename_map}" \
        "${rename_engine}" \
        "${path_map}" \
        "${path_map_engine}" \
        "${SOURCE_PREFIX}" \
        "${FRAMEWORK_PREFIX}" \
        "${FRAMEWORK_ID}" \
        "${FRAMEWORK_NAME}" \
        "${SOURCE_REPO}" \
        "${TARGET_REPO}" <<'PY_MERGE'
from __future__ import annotations

import filecmp
import importlib.util
import os
import shutil
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

stage = Path(sys.argv[1])
target = Path(sys.argv[2])
dry_run = sys.argv[3].lower() == "true"
rename_map_path = Path(sys.argv[4])
rename_engine_path = Path(sys.argv[5])
path_map_path = Path(sys.argv[6])
path_map_engine_path = Path(sys.argv[7])

source_prefix = sys.argv[8]
framework_prefix = sys.argv[9]
framework_id = sys.argv[10]
framework_name = sys.argv[11]
source_repo = sys.argv[12]
target_repo = sys.argv[13]

spec = importlib.util.spec_from_file_location(
    "framework_rename_engine",
    rename_engine_path,
)

if spec is None or spec.loader is None:
    raise SystemExit("Unable to load rename engine.")

rename_engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rename_engine)

sys.path.insert(0, str(path_map_engine_path.parent))
path_spec = importlib.util.spec_from_file_location(
    "framework_path_map_engine",
    path_map_engine_path,
)

if path_spec is None or path_spec.loader is None:
    raise SystemExit("Unable to load path-map engine.")

path_map_engine = importlib.util.module_from_spec(path_spec)
sys.modules[path_spec.name] = path_map_engine
path_spec.loader.exec_module(path_map_engine)

rename_data = rename_engine.load_map(rename_map_path)
path_map_data = path_map_engine.load_map(path_map_path)

values = rename_engine.build_values(
    SimpleNamespace(
        source_prefix=source_prefix,
        framework_prefix=framework_prefix,
        framework_id=framework_id,
        framework_name=framework_name,
        source_repo=source_repo,
        target_repo=target_repo,
    )
)

managed = set(rename_engine.managed_paths(rename_data))
path_rules = path_map_engine.render_rules(path_map_data, values)

stage_relative_paths = [
    candidate.relative_to(stage).as_posix()
    for candidate in sorted(stage.rglob("*"), key=lambda p: p.as_posix())
]

try:
    mapped_paths = path_map_engine.map_paths(
        stage_relative_paths,
        path_rules,
    )
except ValueError as exc:
    raise SystemExit(f"Path-map validation failed: {exc}") from exc

copied = 0
unchanged_raw = 0
unchanged_transformed = 0
conflicts: list[str] = []

for source in sorted(stage.rglob("*"), key=lambda p: p.as_posix()):
    relative_path = source.relative_to(stage)
    relative = relative_path.as_posix()
    target_relative = mapped_paths[relative]
    destination = target / target_relative

    if source.is_dir() and not source.is_symlink():
        if destination.exists() and not destination.is_dir():
            conflicts.append(
                f"{relative}: source directory conflicts with target file"
            )
        elif not dry_run:
            destination.mkdir(parents=True, exist_ok=True)

        continue

    if source.is_symlink():
        link_target = os.readlink(source)

        if destination.is_symlink():
            if os.readlink(destination) == link_target:
                unchanged_raw += 1
            else:
                conflicts.append(
                    f"{relative}: different symbolic link already exists"
                )
        elif destination.exists():
            conflicts.append(
                f"{relative}: source symbolic link conflicts with target path"
            )
        else:
            if not dry_run:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(link_target)

            copied += 1

        continue

    if not source.is_file():
        conflicts.append(f"{relative}: unsupported staged file type")
        continue

    source_bytes = source.read_bytes()
    source_mode = stat.S_IMODE(source.stat().st_mode)
    source_executable = bool(source_mode & 0o111)

    if destination.exists() or destination.is_symlink():
        if not destination.is_file() or destination.is_symlink():
            conflicts.append(
                f"{relative}: source file conflicts with target path"
            )
            continue

        destination_bytes = destination.read_bytes()
        destination_mode = stat.S_IMODE(destination.stat().st_mode)
        destination_executable = bool(destination_mode & 0o111)

        if (
            destination_bytes == source_bytes
            and destination_executable == source_executable
        ):
            unchanged_raw += 1
            continue

        if relative in managed:
            try:
                source_text = source_bytes.decode("utf-8")
            except UnicodeDecodeError:
                conflicts.append(
                    f"{relative}: transform-managed source is not UTF-8"
                )
                continue

            try:
                rendered, applied = rename_engine.render(
                    rename_data,
                    relative,
                    source_text,
                    values,
                )
            except ValueError as exc:
                conflicts.append(f"{relative}: {exc}")
                continue

            if applied == 0:
                conflicts.append(
                    f"{relative}: listed as transform-managed but no rules applied"
                )
                continue

            expected_bytes = rendered.encode("utf-8")

            if (
                destination_bytes == expected_bytes
                and destination_executable == source_executable
            ):
                unchanged_transformed += 1
                continue

        conflicts.append(
            f"{relative}: target differs from both approved raw and expected transformed state"
        )
        continue

    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    copied += 1

if conflicts:
    print("Copy conflicts detected:", file=sys.stderr)

    for conflict in conflicts:
        print(f"  {conflict}", file=sys.stderr)

    sys.exit(3)

print(f"COPIED={copied}")
print(f"UNCHANGED_RAW={unchanged_raw}")
print(f"UNCHANGED_TRANSFORMED={unchanged_transformed}")
PY_MERGE
    then
        rm -rf -- "${staging_dir}"
        die "Approved-content merge failed."
        return 1
    fi

    rm -rf -- "${staging_dir}"

    if [[ "${DRY_RUN}" == true ]]; then
        log "DRY RUN: approved content collision-checked; target was not modified."
    else
        log "Approved source content verified/copied without overwriting divergent target files."
    fi

    log "PASS: whitelist-based source copy completed."
}
###############################################################################
# 06 — Enforce domain-content exclusions
###############################################################################

phase_06_remove_domain_content()
{
    local target_resolved
    local manifest
    local entry
    local candidate

    local -a violations=()

    phase "06 — Enforce domain-content exclusions"

    target_resolved="$(canonicalize_path "${TARGET_DIR}")"
    manifest="${target_resolved}/bootstrap/config/exclusion-manifest.txt"

    if [[ ! -f "${manifest}" ]]; then
        die "Exclusion manifest does not exist: ${manifest}"
        return 1
    fi

    while IFS= read -r entry || [[ -n "${entry}" ]]; do
        entry="${entry%$'\r'}"

        if [[ -z "${entry}" || "${entry}" == \#* ]]; then
            continue
        fi

        if [[ "${entry}" == /* \
            || "${entry}" == ".." \
            || "${entry}" == ../* \
            || "${entry}" == */../* \
            || "${entry}" == */.. \
            || "${entry}" == ".git" \
            || "${entry}" == .git/* ]]; then
            die "Unsafe path in exclusion manifest: ${entry}"
            return 1
        fi

        candidate="${target_resolved}/${entry}"

        if [[ -e "${candidate}" || -L "${candidate}" ]]; then
            violations+=("${entry}")
        fi
    done < "${manifest}"

    if ((${#violations[@]} > 0)); then
        printf '[bootstrap:error] Forbidden target paths detected:\n' >&2

        for entry in "${violations[@]}"; do
            printf '  %s\n' "${entry}" >&2
        done

        die "Domain-content exclusion check failed. No files were removed."
        return 1
    fi

    log_verbose "Exclusion entries checked: $(grep -Ev '^[[:space:]]*(#|$)' "${manifest}" | wc -l | tr -d '[:space:]')"

    log "PASS: excluded domain-specific paths are absent."
}


###############################################################################
# 07 — Apply declared mechanical transformations
###############################################################################

phase_07_apply_renames()
{
    local source_resolved
    local target_resolved
    local source_commit
    local copy_manifest
    local rename_map
    local rename_engine
    local path_map
    local path_map_engine

    phase "07 — Apply declared mechanical transformations"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    source_commit="$(
        git -C "${source_resolved}" rev-parse "${SOURCE_REF}^{commit}"
    )"

    copy_manifest="${target_resolved}/bootstrap/config/copy-manifest.txt"
    rename_map="${target_resolved}/bootstrap/config/rename-map.json"
    rename_engine="${target_resolved}/bootstrap/lib/rename_engine.py"
    path_map="${target_resolved}/bootstrap/config/path-map.json"
    path_map_engine="${target_resolved}/bootstrap/lib/path_map_engine.py"

    if [[ ! -f "${copy_manifest}" ]]; then
        die "Copy manifest does not exist: ${copy_manifest}"
        return 1
    fi

    if [[ ! -f "${rename_map}" ]]; then
        die "Rename map does not exist: ${rename_map}"
        return 1
    fi

    if [[ ! -f "${rename_engine}" ]]; then
        die "Rename engine does not exist: ${rename_engine}"
        return 1
    fi

    if [[ ! -f "${path_map}" ]]; then
        die "Path map does not exist: ${path_map}"
        return 1
    fi

    if [[ ! -f "${path_map_engine}" ]]; then
        die "Path-map engine does not exist: ${path_map_engine}"
        return 1
    fi

    if ! python3 - \
        "${source_resolved}" \
        "${source_commit}" \
        "${target_resolved}" \
        "${copy_manifest}" \
        "${rename_map}" \
        "${rename_engine}" \
        "${path_map}" \
        "${path_map_engine}" \
        "${SOURCE_PREFIX}" \
        "${FRAMEWORK_PREFIX}" \
        "${FRAMEWORK_ID}" \
        "${FRAMEWORK_NAME}" \
        "${SOURCE_REPO}" \
        "${TARGET_REPO}" \
        "${DRY_RUN}" <<'PY_TRANSFORM'
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

source_root = Path(sys.argv[1])
source_commit = sys.argv[2]
target_root = Path(sys.argv[3])
copy_manifest_path = Path(sys.argv[4])
rename_map_path = Path(sys.argv[5])
rename_engine_path = Path(sys.argv[6])
path_map_path = Path(sys.argv[7])
path_map_engine_path = Path(sys.argv[8])

source_prefix = sys.argv[9]
framework_prefix = sys.argv[10]
framework_id = sys.argv[11]
framework_name = sys.argv[12]
source_repo = sys.argv[13]
target_repo = sys.argv[14]
dry_run = sys.argv[15].lower() == "true"


def fail(message: str) -> None:
    print(f"transform: {message}", file=sys.stderr)
    raise SystemExit(1)


spec = importlib.util.spec_from_file_location(
    "framework_rename_engine",
    rename_engine_path,
)

if spec is None or spec.loader is None:
    fail("Unable to load rename engine.")

rename_engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rename_engine)

sys.path.insert(0, str(path_map_engine_path.parent))
path_spec = importlib.util.spec_from_file_location(
    "framework_path_map_engine",
    path_map_engine_path,
)

if path_spec is None or path_spec.loader is None:
    fail("Unable to load path-map engine.")

path_map_engine = importlib.util.module_from_spec(path_spec)
sys.modules[path_spec.name] = path_map_engine
path_spec.loader.exec_module(path_map_engine)

try:
    rename_data = rename_engine.load_map(rename_map_path)
    path_map_data = path_map_engine.load_map(path_map_path)
except ValueError as exc:
    fail(str(exc))

values = rename_engine.build_values(
    SimpleNamespace(
        source_prefix=source_prefix,
        framework_prefix=framework_prefix,
        framework_id=framework_id,
        framework_name=framework_name,
        source_repo=source_repo,
        target_repo=target_repo,
    )
)

managed_paths = rename_engine.managed_paths(rename_data)

if not managed_paths:
    fail("Rename map contains no managed paths.")

try:
    path_rules = path_map_engine.render_rules(path_map_data, values)
    mapped_managed_paths = path_map_engine.map_paths(
        managed_paths,
        path_rules,
    )
except ValueError as exc:
    fail(str(exc))

approved_entries: list[str] = []

for raw in copy_manifest_path.read_text(encoding="utf-8").splitlines():
    entry = raw.strip()

    if not entry or entry.startswith("#"):
        continue

    approved_entries.append(entry)

for relative in managed_paths:
    if not any(
        relative == approved
        or relative.startswith(approved.rstrip("/") + "/")
        for approved in approved_entries
    ):
        fail(
            f"Rename-managed path is not approved by copy manifest: {relative}"
        )

raw_count = 0
already_transformed_count = 0
no_effect_count = 0
changed_count = 0
conflicts: list[str] = []

for relative in managed_paths:
    target_relative = mapped_managed_paths[relative]
    target = target_root / target_relative

    blob = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "cat-file",
            "blob",
            f"{source_commit}:{relative}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if blob.returncode != 0:
        conflicts.append(
            f"{relative}: unable to read source blob from pinned commit"
        )
        continue

    tree = subprocess.run(
        [
            "git",
            "-C",
            str(source_root),
            "ls-tree",
            source_commit,
            "--",
            relative,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )

    if tree.returncode != 0 or not tree.stdout.strip():
        conflicts.append(
            f"{relative}: unable to read source mode from pinned commit"
        )
        continue

    mode_token = tree.stdout.split(None, 1)[0]

    if mode_token == "100644":
        expected_executable = False
    elif mode_token == "100755":
        expected_executable = True
    else:
        conflicts.append(
            f"{relative}: unsupported source Git mode {mode_token}"
        )
        continue

    source_bytes = blob.stdout

    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        conflicts.append(
            f"{relative}: transform-managed source is not UTF-8"
        )
        continue

    try:
        rendered, applied = rename_engine.render(
            rename_data,
            relative,
            source_text,
            values,
        )
    except ValueError as exc:
        conflicts.append(f"{relative}: {exc}")
        continue

    if applied == 0:
        conflicts.append(
            f"{relative}: no declared rename rules were applied"
        )
        continue

    expected_bytes = rendered.encode("utf-8")

    if not target.exists() and not target.is_symlink():
        if dry_run:
            if expected_bytes == source_bytes:
                no_effect_count += 1
            else:
                raw_count += 1
            continue

        conflicts.append(
            f"{relative}: expected regular target file is missing"
        )
        continue

    if target.is_symlink() or not target.is_file():
        conflicts.append(
            f"{relative}: expected regular target file is missing"
        )
        continue

    target_bytes = target.read_bytes()
    target_mode = stat.S_IMODE(target.stat().st_mode)
    target_executable = bool(target_mode & 0o111)

    if expected_bytes == source_bytes:
        if (
            target_bytes == source_bytes
            and target_executable == expected_executable
        ):
            no_effect_count += 1
            continue

        conflicts.append(
            f"{relative}: no-effect transformation has divergent target state"
        )
        continue

    if (
        target_bytes == expected_bytes
        and target_executable == expected_executable
    ):
        already_transformed_count += 1
        continue

    if (
        target_bytes != source_bytes
        or target_executable != expected_executable
    ):
        conflicts.append(
            f"{relative}: target differs from both raw source and expected transformed state"
        )
        continue

    raw_count += 1

    if dry_run:
        continue

    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.bootstrap-",
        dir=target.parent,
    )

    temporary = Path(temporary_name)

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(expected_bytes)
            handle.flush()
            os.fsync(handle.fileno())

        # Git tracks only the executable bit for regular files. Preserve
        # the checkout's other permission bits rather than imposing 0644/0755.
        os.chmod(temporary, target_mode)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    changed_count += 1

if conflicts:
    print("Mechanical transformation conflicts detected:", file=sys.stderr)

    for conflict in conflicts:
        print(f"  {conflict}", file=sys.stderr)

    raise SystemExit(3)

print(f"MANAGED={len(managed_paths)}")
print(f"RAW={raw_count}")
print(f"ALREADY_TRANSFORMED={already_transformed_count}")
print(f"NO_EFFECT={no_effect_count}")

if dry_run:
    print(f"WOULD_TRANSFORM={raw_count}")
else:
    print(f"TRANSFORMED={changed_count}")
PY_TRANSFORM
    then
        die "Declared mechanical transformation failed."
        return 1
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        log "DRY RUN: declared transformations verified; target was not modified."
    else
        log "Declared mechanical transformations applied from the pinned source state."
    fi

    log "PASS: declared mechanical transformations completed."
}
###############################################################################
# 08 — Create generic framework directory structure
###############################################################################

phase_08_create_structure()
{
    local target_resolved
    local manifest
    local entry
    local candidate
    local present_count=0
    local create_count=0

    phase "08 — Create generic framework directory structure"

    target_resolved="$(canonicalize_path "${TARGET_DIR}")"
    manifest="${target_resolved}/bootstrap/config/structure-manifest.txt"

    if [[ ! -f "${manifest}" ]]; then
        die "Structure manifest does not exist: ${manifest}"
        return 1
    fi

    while IFS= read -r entry || [[ -n "${entry}" ]]; do
        entry="${entry%$'\r'}"

        if [[ -z "${entry}" || "${entry}" == \#* ]]; then
            continue
        fi

        if [[ "${entry}" == "/" \
            || "${entry}" == "." \
            || "${entry}" == /* \
            || "${entry}" == ".." \
            || "${entry}" == ../* \
            || "${entry}" == */../* \
            || "${entry}" == */.. \
            || "${entry}" == ".git" \
            || "${entry}" == .git/* ]]; then
            die "Unsafe path in structure manifest: ${entry}"
            return 1
        fi

        candidate="${target_resolved}/${entry}"

        if [[ -L "${candidate}" ]]; then
            die "Structure path may not be a symbolic link: ${entry}"
            return 1
        fi

        if [[ -e "${candidate}" ]]; then
            if [[ ! -d "${candidate}" ]]; then
                die "Structure directory conflicts with existing target path: ${entry}"
                return 1
            fi

            present_count=$((present_count + 1))
            continue
        fi

        create_count=$((create_count + 1))

        if [[ "${DRY_RUN}" != true ]]; then
            mkdir -p -- "${candidate}"
        fi
    done < "${manifest}"

    log "DIRECTORIES_PRESENT=${present_count}"

    if [[ "${DRY_RUN}" == true ]]; then
        log "WOULD_CREATE_DIRECTORIES=${create_count}"
        log "DRY RUN: declared directory structure verified; target was not modified."
    else
        log "DIRECTORIES_CREATED=${create_count}"
        log "Declared generic directory structure created."
    fi

    log "PASS: generic framework directory structure completed."
}


###############################################################################
# 09 — Generate framework configuration
###############################################################################

phase_09_generate_config()
{
    local target_resolved
    local destination

    phase "09 — Generate framework configuration"

    target_resolved="$(canonicalize_path "${TARGET_DIR}")"
    destination="${target_resolved}/framework.json"

    if ! python3 - \
        "${destination}" \
        "${FRAMEWORK_ID}" \
        "${FRAMEWORK_NAME}" \
        "${FRAMEWORK_PREFIX}" \
        "${DRY_RUN}" <<'PY_CONFIG'
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

destination = Path(sys.argv[1])
framework_id = sys.argv[2]
framework_name = sys.argv[3]
framework_prefix = sys.argv[4]
dry_run = sys.argv[5].lower() == "true"

document = {
    "schemaVersion": 1,
    "framework": {
        "id": framework_id,
        "name": framework_name,
        "prefix": framework_prefix,
    },
}

expected = (
    json.dumps(
        document,
        indent=2,
        ensure_ascii=False,
    )
    + "\n"
).encode("utf-8")

if destination.exists() or destination.is_symlink():
    if destination.is_symlink() or not destination.is_file():
        print(
            f"framework-config: destination is not a regular file: {destination}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    actual = destination.read_bytes()

    if actual != expected:
        print(
            "framework-config: existing framework.json differs from the "
            "deterministically generated configuration",
            file=sys.stderr,
        )
        raise SystemExit(3)

    print("FRAMEWORK_CONFIG=UNCHANGED")
    raise SystemExit(0)

if dry_run:
    print("FRAMEWORK_CONFIG=WOULD_CREATE")
    raise SystemExit(0)

destination.parent.mkdir(parents=True, exist_ok=True)

fd, temporary_name = tempfile.mkstemp(
    prefix=".framework.json.bootstrap-",
    dir=destination.parent,
)

temporary = Path(temporary_name)

try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(expected)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temporary, destination)
finally:
    if temporary.exists():
        temporary.unlink()

print("FRAMEWORK_CONFIG=CREATED")
PY_CONFIG
    then
        die "Framework configuration generation failed."
        return 1
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        log "DRY RUN: framework configuration verified; target was not modified."
    else
        log "Framework configuration generated."
    fi

    log "PASS: framework configuration completed."
}


###############################################################################
# 10 — Generate extraction provenance
###############################################################################

phase_10_generate_provenance()
{
    local source_resolved
    local target_resolved
    local source_commit
    local source_tree
    local source_repo_normalized
    local target_repo_normalized
    local inputs_manifest
    local destination

    phase "10 — Generate extraction provenance"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    source_commit="$(
        git -C "${source_resolved}" rev-parse "${SOURCE_REF}^{commit}"
    )"

    source_tree="$(
        git -C "${source_resolved}" rev-parse "${source_commit}^{tree}"
    )"

    source_repo_normalized="$(normalize_repository_slug "${SOURCE_REPO}")"
    target_repo_normalized="$(normalize_repository_slug "${TARGET_REPO}")"

    inputs_manifest="${target_resolved}/bootstrap/config/provenance-inputs.txt"
    destination="${target_resolved}/bootstrap/generated/extraction-provenance.json"

    if [[ ! -f "${inputs_manifest}" ]]; then
        die "Provenance-input manifest does not exist: ${inputs_manifest}"
        return 1
    fi

    if ! python3 - \
        "${target_resolved}" \
        "${inputs_manifest}" \
        "${destination}" \
        "${SCRIPT_VERSION}" \
        "${source_repo_normalized}" \
        "${SOURCE_REF}" \
        "${source_commit}" \
        "${source_tree}" \
        "${SOURCE_PREFIX}" \
        "${target_repo_normalized}" \
        "${FRAMEWORK_ID}" \
        "${FRAMEWORK_NAME}" \
        "${FRAMEWORK_PREFIX}" \
        "${DRY_RUN}" <<'PY_PROVENANCE'
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

target_root = Path(sys.argv[1])
manifest = Path(sys.argv[2])
destination = Path(sys.argv[3])

bootstrap_version = sys.argv[4]
source_repository = sys.argv[5]
source_ref = sys.argv[6]
source_commit = sys.argv[7]
source_tree = sys.argv[8]
source_prefix = sys.argv[9]
target_repository = sys.argv[10]
framework_id = sys.argv[11]
framework_name = sys.argv[12]
framework_prefix = sys.argv[13]
dry_run = sys.argv[14].lower() == "true"


def fail(message: str) -> None:
    print(f"provenance: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


try:
    target_root = target_root.resolve(strict=True)
    manifest_resolved = manifest.resolve(strict=True)
except OSError as exc:
    fail(str(exc))

try:
    manifest_relative = manifest_resolved.relative_to(target_root).as_posix()
except ValueError:
    fail("Provenance-input manifest must be inside the target repository.")

entries: list[str] = []

for raw in manifest.read_text(encoding="utf-8").splitlines():
    entry = raw.strip()

    if not entry or entry.startswith("#"):
        continue

    candidate = Path(entry)

    if (
        candidate.is_absolute()
        or entry == "."
        or ".." in candidate.parts
        or candidate.parts[:1] == (".git",)
    ):
        fail(f"Unsafe path in provenance-input manifest: {entry}")

    if entry in entries:
        fail(f"Duplicate provenance-input path: {entry}")

    entries.append(entry)

if not entries:
    fail("Provenance-input manifest contains no files.")

recipe_inputs: list[dict[str, str]] = []

for relative in sorted(entries):
    candidate = target_root / relative

    if candidate.is_symlink() or not candidate.is_file():
        fail(f"Provenance input is not a regular file: {relative}")

    recipe_inputs.append(
        {
            "path": relative,
            "sha256": sha256(candidate),
        }
    )

document = {
    "schemaVersion": 1,
    "bootstrap": {
        "version": bootstrap_version,
    },
    "source": {
        "repository": source_repository,
        "configuredRef": source_ref,
        "commit": source_commit,
        "tree": source_tree,
        "prefix": source_prefix,
    },
    "target": {
        "repository": target_repository,
    },
    "frameworkInvocation": {
        "id": framework_id,
        "name": framework_name,
        "prefix": framework_prefix,
    },
    "recipe": {
        "manifest": {
            "path": manifest_relative,
            "sha256": sha256(manifest),
        },
        "inputs": recipe_inputs,
    },
}

expected = (
    json.dumps(
        document,
        indent=2,
        ensure_ascii=False,
    )
    + "\n"
).encode("utf-8")

status: str

if destination.exists() or destination.is_symlink():
    if destination.is_symlink() or not destination.is_file():
        fail(f"Destination is not a regular file: {destination}")

    actual = destination.read_bytes()

    if actual == expected:
        print("EXTRACTION_PROVENANCE=UNCHANGED")
        raise SystemExit(0)

    if dry_run:
        print("EXTRACTION_PROVENANCE=WOULD_UPDATE")
        raise SystemExit(0)

    status = "UPDATED"
    existing_mode = destination.stat().st_mode & 0o777
else:
    if dry_run:
        print("EXTRACTION_PROVENANCE=WOULD_CREATE")
        raise SystemExit(0)

    status = "CREATED"
    existing_mode = None

destination.parent.mkdir(parents=True, exist_ok=True)

fd, temporary_name = tempfile.mkstemp(
    prefix=".extraction-provenance.json.bootstrap-",
    dir=destination.parent,
)

temporary = Path(temporary_name)

try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(expected)
        handle.flush()
        os.fsync(handle.fileno())

    if existing_mode is not None:
        os.chmod(temporary, existing_mode)

    os.replace(temporary, destination)
finally:
    if temporary.exists():
        temporary.unlink()

print(f"EXTRACTION_PROVENANCE={status}")
PY_PROVENANCE
    then
        die "Extraction provenance generation failed."
        return 1
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        log "DRY RUN: extraction provenance verified; target was not modified."
    else
        log "Extraction provenance generated."
    fi

    log "PASS: extraction provenance completed."
}


###############################################################################
# 11 — Verify permissions and text normalization
###############################################################################

phase_11_normalize()
{
    local target_resolved

    phase "11 — Verify permissions and text normalization"

    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    if ! python3 - "${target_resolved}" <<'PY_NORMALIZE'
from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])

TEXT_SUFFIXES = {
    ".cs",
    ".csproj",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".json",
    ".md",
    ".props",
    ".py",
    ".sh",
    ".slnx",
    ".targets",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

TEXT_NAMES = {
    "LICENSE",
    "NOTICE",
    "README",
}

failures: list[str] = []
text_files = 0
regular_files = 0
symlinks = 0
executables = 0

listed = subprocess.run(
    ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    stdout=subprocess.PIPE,
    check=True,
 ).stdout.split(b"\0")

for raw_relative in sorted(x for x in listed if x):
    relative = Path(raw_relative.decode("utf-8"))
    candidate = root / relative

    if candidate.is_symlink():
        symlinks += 1
        continue

    if not candidate.is_file():
        continue

    regular_files += 1

    mode = stat.S_IMODE(candidate.stat().st_mode)

    if mode & 0o7000:
        failures.append(
            f"{relative}: special permission bits are not allowed ({oct(mode)})"
        )

    if mode & 0o002:
        failures.append(
            f"{relative}: world-writable regular file is not allowed ({oct(mode)})"
        )

    raw = candidate.read_bytes()

    if raw.startswith(b"#!"):
        if not (mode & 0o111):
            failures.append(
                f"{relative}: shebang file is not executable ({oct(mode)})"
            )
        else:
            executables += 1

    is_declared_text = (
        candidate.suffix.lower() in TEXT_SUFFIXES
        or candidate.name in TEXT_NAMES
    )

    if not is_declared_text:
        continue

    text_files += 1

    if raw.startswith(b"\xef\xbb\xbf"):
        failures.append(f"{relative}: UTF-8 BOM is not allowed")

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        failures.append(f"{relative}: declared text file is not valid UTF-8")
        continue

    if b"\r" in raw:
        failures.append(f"{relative}: CR/CRLF line endings are not allowed")

    if raw:
        if not raw.endswith(b"\n"):
            failures.append(f"{relative}: missing trailing newline")
        elif raw.endswith(b"\n\n"):
            failures.append(f"{relative}: more than one trailing newline")

if failures:
    print("Normalization violations detected:", file=sys.stderr)

    for failure in failures:
        print(f"  {failure}", file=sys.stderr)

    raise SystemExit(3)

print(f"REGULAR_FILES_CHECKED={regular_files}")
print(f"TEXT_FILES_CHECKED={text_files}")
print(f"SHEBANG_EXECUTABLES={executables}")
print(f"SYMLINKS_SKIPPED={symlinks}")
PY_NORMALIZE
    then
        die "Permission/text normalization verification failed."
        return 1
    fi

    log "Target content already satisfies normalization requirements; no files modified."
    log "PASS: permission and text normalization verified."
}


###############################################################################
# 12 — Enforce contamination debt baseline
###############################################################################

phase_12_contamination_scan()
{
    local target_resolved
    local policy
    local baseline
    local engine

    phase "12 — Enforce contamination debt baseline"

    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    policy="${target_resolved}/bootstrap/config/contamination-policy.json"
    baseline="${target_resolved}/bootstrap/config/contamination-baseline.json"
    engine="${target_resolved}/bootstrap/lib/contamination_engine.py"

    if [[ ! -f "${policy}" ]]; then
        die "Contamination policy does not exist: ${policy}"
        return 1
    fi

    if [[ ! -f "${baseline}" ]]; then
        die "Contamination baseline does not exist: ${baseline}"
        return 1
    fi

    if [[ ! -f "${engine}" ]]; then
        die "Contamination engine does not exist: ${engine}"
        return 1
    fi

    if ! python3 "${engine}" \
        --root "${target_resolved}" \
        --policy "${policy}" \
        --source-prefix "${SOURCE_PREFIX}" \
        --framework-name "${FRAMEWORK_NAME}" \
        --baseline "${baseline}"; then
        die "Contamination debt baseline verification failed."
        return 1
    fi

    log "No contamination bucket increased, decreased, appeared, or disappeared without review."
    log "PASS: contamination debt baseline verified."
}


###############################################################################
# 13 — Mechanical validation
###############################################################################

phase_13_validate()
{
    local target_resolved
    local manifest

    phase "13 — Mechanical validation"

    target_resolved="$(canonicalize_path "${TARGET_DIR}")"
    manifest="${target_resolved}/bootstrap/config/validation-manifest.json"

    if [[ ! -f "${manifest}" ]]; then
        die "Validation manifest does not exist: ${manifest}"
        return 1
    fi

    if ! python3 - \
        "${target_resolved}" \
        "${manifest}" <<'PY_VALIDATE'
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])

SUPPORTED_TYPES = {
    "shell-syntax",
    "python-syntax",
    "json",
    "python-unittest",
}


def fail(message: str) -> None:
    print(f"validation: {message}", file=sys.stderr)
    raise SystemExit(1)


try:
    document = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
except (OSError, json.JSONDecodeError) as exc:
    fail(f"unable to read validation manifest: {exc}")

if document.get("schemaVersion") != 1:
    fail(
        f"unsupported validation-manifest schemaVersion: "
        f"{document.get('schemaVersion')!r}"
    )

checks = document.get("checks")

if not isinstance(checks, list) or not checks:
    fail("validation manifest must contain a non-empty checks list")

passed = 0

for index, check in enumerate(checks):
    if not isinstance(check, dict) or set(check) != {"type", "path"}:
        fail(
            f"check {index} must contain exactly type and path"
        )

    check_type = check["type"]
    relative = check["path"]

    if check_type not in SUPPORTED_TYPES:
        fail(f"check {index} has unsupported type: {check_type}")

    if not isinstance(relative, str) or not relative:
        fail(f"check {index} has invalid path")

    relative_path = Path(relative)

    if (
        relative_path.is_absolute()
        or relative == "."
        or ".." in relative_path.parts
        or relative_path.parts[:1] == (".git",)
    ):
        fail(f"check {index} has unsafe path: {relative}")

    candidate = root / relative_path

    if candidate.is_symlink() or not candidate.is_file():
        fail(
            f"check {index} target is not a regular file: {relative}"
        )

    if check_type == "shell-syntax":
        result = subprocess.run(
            ["bash", "-n", str(candidate)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            print(result.stdout, end="", file=sys.stderr)
            print(result.stderr, end="", file=sys.stderr)
            fail(f"shell syntax check failed: {relative}")

    elif check_type == "python-syntax":
        try:
            source = candidate.read_text(encoding="utf-8")
            ast.parse(source, filename=relative)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            fail(f"Python syntax check failed for {relative}: {exc}")

    elif check_type == "json":
        try:
            json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            fail(f"JSON validation failed for {relative}: {exc}")

    elif check_type == "python-unittest":
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"

        result = subprocess.run(
            [sys.executable, str(candidate)],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            print(
                f"--- stdout: {relative} ---",
                file=sys.stderr,
            )
            print(result.stdout, end="", file=sys.stderr)
            print(
                f"--- stderr: {relative} ---",
                file=sys.stderr,
            )
            print(result.stderr, end="", file=sys.stderr)

            fail(f"unit test failed: {relative}")

    passed += 1
    print(f"VALIDATION_PASS={check_type}:{relative}")

print(f"VALIDATION_CHECKS_PASSED={passed}")
PY_VALIDATE
    then
        die "Mechanical validation failed."
        return 1
    fi

    log "PASS: declared mechanical validation completed."
}


###############################################################################
# 14 — Produce final extraction report
###############################################################################

phase_14_report()
{
    local source_resolved
    local target_resolved
    local source_commit
    local source_tree
    local source_repo_normalized
    local target_repo_normalized
    local destination

    phase "14 — Produce final extraction report"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    source_commit="$(
        git -C "${source_resolved}" rev-parse "${SOURCE_REF}^{commit}"
    )"

    source_tree="$(
        git -C "${source_resolved}" rev-parse "${source_commit}^{tree}"
    )"

    source_repo_normalized="$(normalize_repository_slug "${SOURCE_REPO}")"
    target_repo_normalized="$(normalize_repository_slug "${TARGET_REPO}")"

    destination="${target_resolved}/bootstrap/generated/extraction-report.json"

    if ! python3 - \
        "${source_resolved}" \
        "${source_commit}" \
        "${source_tree}" \
        "${target_resolved}" \
        "${source_repo_normalized}" \
        "${target_repo_normalized}" \
        "${FRAMEWORK_ID}" \
        "${FRAMEWORK_NAME}" \
        "${FRAMEWORK_PREFIX}" \
        "${SCRIPT_VERSION}" \
        "${DRY_RUN}" \
        "${destination}" <<'PY_REPORT'
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

source_root = Path(sys.argv[1])
source_commit = sys.argv[2]
source_tree = sys.argv[3]
target_root = Path(sys.argv[4])
source_repository = sys.argv[5]
target_repository = sys.argv[6]
framework_id = sys.argv[7]
framework_name = sys.argv[8]
framework_prefix = sys.argv[9]
bootstrap_version = sys.argv[10]
dry_run = sys.argv[11].lower() == "true"
destination = Path(sys.argv[12])

copy_manifest = target_root / "bootstrap/config/copy-manifest.txt"
rename_map = target_root / "bootstrap/config/rename-map.json"
path_map = target_root / "bootstrap/config/path-map.json"
structure_manifest = target_root / "bootstrap/config/structure-manifest.txt"
contamination_baseline = (
    target_root / "bootstrap/config/contamination-baseline.json"
)
validation_manifest = (
    target_root / "bootstrap/config/validation-manifest.json"
)


def fail(message: str) -> None:
    print(f"extraction-report: {message}", file=sys.stderr)
    raise SystemExit(1)


def manifest_entries(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"unable to read {path}: {exc}")

    result: list[str] = []

    for raw in lines:
        entry = raw.strip()

        if not entry or entry.startswith("#"):
            continue

        result.append(entry)

    return result


approved_paths = manifest_entries(copy_manifest)

if not approved_paths:
    fail("copy manifest contains no approved paths")

tracked = subprocess.run(
    [
        "git",
        "-C",
        str(source_root),
        "ls-tree",
        "-r",
        "--name-only",
        source_commit,
        "--",
        *approved_paths,
    ],
    capture_output=True,
    text=True,
    check=False,
)

if tracked.returncode != 0:
    print(tracked.stderr, end="", file=sys.stderr)
    fail("unable to enumerate approved source files")

approved_files = sorted(
    {
        line
        for line in tracked.stdout.splitlines()
        if line
    }
)

try:
    rename_document = json.loads(
        rename_map.read_text(encoding="utf-8")
    )
    path_map_document = json.loads(
        path_map.read_text(encoding="utf-8")
    )
    contamination_document = json.loads(
        contamination_baseline.read_text(encoding="utf-8")
    )
    validation_document = json.loads(
        validation_manifest.read_text(encoding="utf-8")
    )
except (OSError, json.JSONDecodeError) as exc:
    fail(f"unable to read report input: {exc}")

rename_rules = rename_document.get("rules")

if not isinstance(rename_rules, list):
    fail("rename map rules must be a list")

managed_paths = sorted(
    {
        rule["path"]
        for rule in rename_rules
        if isinstance(rule, dict)
        and isinstance(rule.get("path"), str)
    }
)

if len(managed_paths) == 0 and len(rename_rules) != 0:
    fail("unable to derive transform-managed paths")

path_rules = path_map_document.get("rules")

if not isinstance(path_rules, list):
    fail("path map rules must be a list")

structure_entries = manifest_entries(structure_manifest)

baseline_counts = contamination_document.get("counts")

if not isinstance(baseline_counts, list):
    fail("contamination baseline counts must be a list")

contamination_occurrences = 0

for item in baseline_counts:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("count"), int)
        or item["count"] <= 0
    ):
        fail("invalid contamination baseline entry")

    contamination_occurrences += item["count"]

validation_checks = validation_document.get("checks")

if not isinstance(validation_checks, list):
    fail("validation manifest checks must be a list")

document = {
    "schemaVersion": 1,
    "status": "bootstrap-extraction-complete",
    "bootstrapVersion": bootstrap_version,
    "framework": {
        "id": framework_id,
        "name": framework_name,
        "prefix": framework_prefix,
        "repository": target_repository,
    },
    "source": {
        "repository": source_repository,
        "commit": source_commit,
        "tree": source_tree,
    },
    "extraction": {
        "approvedManifestEntries": len(approved_paths),
        "approvedTrackedFiles": len(approved_files),
        "mechanicallyTransformedFiles": len(managed_paths),
        "declaredPathMappings": len(path_rules),
        "declaredStructureDirectories": len(structure_entries),
    },
    "knownContaminationDebt": {
        "occurrences": contamination_occurrences,
        "buckets": len(baseline_counts),
    },
    "validation": {
        "declaredChecks": len(validation_checks),
    },
}

expected = (
    json.dumps(
        document,
        indent=2,
        ensure_ascii=False,
    )
    + "\n"
).encode("utf-8")

if destination.exists() or destination.is_symlink():
    if destination.is_symlink() or not destination.is_file():
        fail(f"destination is not a regular file: {destination}")

    actual = destination.read_bytes()

    if actual == expected:
        print("EXTRACTION_REPORT=UNCHANGED")
        raise SystemExit(0)

    if dry_run:
        print("EXTRACTION_REPORT=WOULD_UPDATE")
        raise SystemExit(0)

    status = "UPDATED"
    existing_mode = destination.stat().st_mode & 0o777
else:
    if dry_run:
        print("EXTRACTION_REPORT=WOULD_CREATE")
        raise SystemExit(0)

    status = "CREATED"
    existing_mode = None

destination.parent.mkdir(parents=True, exist_ok=True)

fd, temporary_name = tempfile.mkstemp(
    prefix=".extraction-report.json.bootstrap-",
    dir=destination.parent,
)

temporary = Path(temporary_name)

try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(expected)
        handle.flush()
        os.fsync(handle.fileno())

    if existing_mode is not None:
        os.chmod(temporary, existing_mode)

    os.replace(temporary, destination)
finally:
    if temporary.exists():
        temporary.unlink()

print(f"EXTRACTION_REPORT={status}")
PY_REPORT
    then
        die "Extraction report generation failed."
        return 1
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        log "DRY RUN: extraction report verified; target was not modified."
    else
        log "Extraction report generated."
    fi

    log "PASS: final extraction report completed."
}


###############################################################################
# Main
###############################################################################

main()
{
    local parse_status=0

    parse_args "$@" || parse_status=$?

    if ((parse_status == 2)); then
        return 0
    fi

    if ((parse_status != 0)); then
        return "${parse_status}"
    fi

    require_configuration
    print_runtime_configuration

    phase_01_preflight
    phase_02_verify_source
    phase_03_verify_target
    phase_04_capture_inventory
    phase_05_copy_content
    phase_06_remove_domain_content
    phase_07_apply_renames
    phase_08_create_structure
    phase_09_generate_config
    phase_10_generate_provenance
    phase_11_normalize
    phase_12_contamination_scan
    phase_13_validate
    phase_14_report

    printf '\n'
    log "Bootstrap extraction completed successfully."
}


main "$@"
