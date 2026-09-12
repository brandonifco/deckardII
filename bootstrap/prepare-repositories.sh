#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

###############################################################################
# Rules-Engine Framework Repository Preparation
#
# Purpose:
#   Prepare source and target Git repositories before invoking the deterministic
#   bootstrap engine.
#
# Responsibilities:
#   - Validate local prerequisites.
#   - Detect whether repository acquisition is necessary.
#   - Handle GitHub authentication only when network/authenticated access is
#     actually required.
#   - Acquire repositories.
#   - Resolve and check out the exact requested source revision.
#   - Leave both repositories ready for bootstrap-framework.sh.
#
# Non-responsibilities:
#   - No framework transformations.
#   - No source-to-target file copying.
#   - No semantic generalization.
#   - No committing, pushing, PR creation, or merging.
#
# Architectural rule:
#   Preparation may use the network and authentication.
#   bootstrap-framework.sh must remain network-independent once both local
#   repositories exist.
###############################################################################


###############################################################################
# 00 — Runtime and CLI
###############################################################################

readonly SCRIPT_VERSION="1.0.1"

SOURCE_DIR=""
SOURCE_REPO=""
SOURCE_REF=""

TARGET_DIR=""
TARGET_REPO=""

GIT_PROTOCOL="https"

DRY_RUN=false
VERBOSE=false

SOURCE_ACQUISITION_ACTION=""
TARGET_ACQUISITION_ACTION=""

SOURCE_CLONE_URL=""
TARGET_CLONE_URL=""


log()
{
    printf '[prepare] %s\n' "$*"
}


log_verbose()
{
    if [[ "${VERBOSE}" == true ]]; then
        printf '[prepare:verbose] %s\n' "$*"
    fi
}


warn()
{
    printf '[prepare:warning] %s\n' "$*" >&2
}


die()
{
    printf '[prepare:error] %s\n' "$*" >&2
    return 1
}


phase()
{
    printf '\n'
    printf '===============================================================================\n'
    printf '%s\n' "$*"
    printf '===============================================================================\n'
}


on_error()
{
    local status=$?
    local line_number="${1:-unknown}"

    printf '\n' >&2
    printf '[prepare:error] command failed at line %s with status %s\n' \
        "${line_number}" \
        "${status}" >&2

    return "${status}"
}


trap 'on_error "${LINENO}"' ERR


usage()
{
    cat <<EOF_USAGE
Rules-engine repository preparation ${SCRIPT_VERSION}

Usage:
  $(basename "$0") [options]

Required options:
  --source-dir PATH
  --source-repo OWNER/REPO
  --source-ref REF

  --target-dir PATH
  --target-repo OWNER/REPO

Optional:
  --git-protocol https|ssh
      Clone protocol when a repository must be acquired.
      Default: https

  --dry-run
      Report intended preparation actions without changing repositories.

  --verbose
      Enable diagnostic output.

  --help
      Show this help text.

Example:
  $(basename "$0") \
      --source-dir /workspace/source \
      --source-repo owner/source-repo \
      --source-ref 0123456789abcdef0123456789abcdef01234567 \
      --target-dir /workspace/framework \
      --target-repo owner/framework-repo \
      --git-protocol https
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

            --git-protocol)
                require_option_value "$1" "$#"
                GIT_PROTOCOL="$2"
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


###############################################################################
# Shared helpers
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


repository_clone_url()
{
    local repository="$1"
    local normalized

    normalized="$(
        normalize_repository_slug "${repository}"
    )" || return 1

    case "${GIT_PROTOCOL}" in
        https)
            printf 'https://github.com/%s.git\n' "${normalized}"
            ;;

        ssh)
            printf 'git@github.com:%s.git\n' "${normalized}"
            ;;

        *)
            return 1
            ;;
    esac
}


normalize_repository_slug()
{
    local repository="$1"

    repository="${repository%.git}"

    case "${repository}" in
        https://github.com/*)
            printf '%s\n' "${repository#https://github.com/}"
            ;;

        http://github.com/*)
            printf '%s\n' "${repository#http://github.com/}"
            ;;

        git@github.com:*)
            printf '%s\n' "${repository#git@github.com:}"
            ;;

        ssh://git@github.com/*)
            printf '%s\n' "${repository#ssh://git@github.com/}"
            ;;

        */*)
            if [[ "${repository}" != *"://"* && "${repository}" != *@*:* ]]; then
                printf '%s\n' "${repository}"
            else
                return 1
            fi
            ;;

        *)
            return 1
            ;;
    esac
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

    if [[ -z "${TARGET_DIR}" ]]; then
        warn "Missing required argument: --target-dir"
        missing=true
    fi

    if [[ -z "${TARGET_REPO}" ]]; then
        warn "Missing required argument: --target-repo"
        missing=true
    fi

    if [[ "${missing}" == true ]]; then
        die "Required repository-preparation configuration is incomplete."
        return 1
    fi
}


###############################################################################
# 01 — Preflight
###############################################################################

phase_01_preflight()
{
    local source_resolved
    local target_resolved
    local normalized_source_repo
    local normalized_target_repo

    phase "01 — Preparation preflight"

    require_configuration

    if ((BASH_VERSINFO[0] < 5)); then
        die "Bash 5 or newer is required. Found: ${BASH_VERSION}"
        return 1
    fi

    require_command git
    require_command readlink
    require_command mkdir

    require_absolute_path "Source" "${SOURCE_DIR}"
    require_absolute_path "Target" "${TARGET_DIR}"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    if [[ "${source_resolved}" == "${target_resolved}" ]]; then
        die "Source and target resolve to the same path: ${source_resolved}"
        return 1
    fi

    if [[ "${target_resolved}" == "/" \
        || "${target_resolved}" == "/home" \
        || "${source_resolved}" == "/" \
        || "${source_resolved}" == "/home" ]]; then
        die "Refusing unsafe repository path."
        return 1
    fi

    if [[ "${target_resolved}" == "${source_resolved}/"* ]]; then
        die "Target repository may not be inside the source repository."
        return 1
    fi

    if [[ "${source_resolved}" == "${target_resolved}/"* ]]; then
        die "Source repository may not be inside the target repository."
        return 1
    fi

    normalized_source_repo="$(
        normalize_repository_slug "${SOURCE_REPO}"
    )" || {
        die "Invalid source repository identifier: ${SOURCE_REPO}"
        return 1
    }

    normalized_target_repo="$(
        normalize_repository_slug "${TARGET_REPO}"
    )" || {
        die "Invalid target repository identifier: ${TARGET_REPO}"
        return 1
    }

    if [[ "${normalized_source_repo,,}" == "${normalized_target_repo,,}" ]]; then
        die "Source and target repository identities must be different."
        return 1
    fi

    case "${GIT_PROTOCOL}" in
        https|ssh)
            ;;
        *)
            die "Unsupported --git-protocol value: ${GIT_PROTOCOL}. Expected: https or ssh."
            return 1
            ;;
    esac

    log_verbose "Bash version: ${BASH_VERSION}"
    log_verbose "Resolved source path: ${source_resolved}"
    log_verbose "Resolved target path: ${target_resolved}"
    log_verbose "Source repository: ${normalized_source_repo}"
    log_verbose "Target repository: ${normalized_target_repo}"
    log_verbose "Source ref: ${SOURCE_REF}"
    log_verbose "Git protocol: ${GIT_PROTOCOL}"

    if [[ "${GITHUB_ACTIONS:-false}" == "true" ]]; then
        log_verbose "Execution environment: github-actions"
    else
        log_verbose "Execution environment: local"
    fi

    log "PASS: preparation configuration and path safety checks completed."
}


###############################################################################
# 02 — Determine repository acquisition requirements
###############################################################################

phase_02_determine_requirements()
{
    local source_resolved
    local target_resolved
    local actual_root

    phase "02 — Determine repository acquisition requirements"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    if [[ -e "${source_resolved}" ]]; then
        if [[ ! -d "${source_resolved}" ]]; then
            die "Source path exists but is not a directory: ${source_resolved}"
            return 1
        fi

        if ! git -C "${source_resolved}" rev-parse \
            --is-inside-work-tree >/dev/null 2>&1; then
            die "Existing source directory is not a Git worktree: ${source_resolved}"
            return 1
        fi

        actual_root="$(
            git -C "${source_resolved}" rev-parse --show-toplevel
        )"

        actual_root="$(canonicalize_path "${actual_root}")"

        if [[ "${actual_root}" != "${source_resolved}" ]]; then
            die "Source path must be the Git worktree root: ${source_resolved}"
            return 1
        fi

        SOURCE_ACQUISITION_ACTION="use-existing"
    else
        SOURCE_ACQUISITION_ACTION="clone"
    fi

    if [[ -e "${target_resolved}" ]]; then
        if [[ ! -d "${target_resolved}" ]]; then
            die "Target path exists but is not a directory: ${target_resolved}"
            return 1
        fi

        if ! git -C "${target_resolved}" rev-parse \
            --is-inside-work-tree >/dev/null 2>&1; then
            die "Existing target directory is not a Git worktree: ${target_resolved}"
            return 1
        fi

        actual_root="$(
            git -C "${target_resolved}" rev-parse --show-toplevel
        )"

        actual_root="$(canonicalize_path "${actual_root}")"

        if [[ "${actual_root}" != "${target_resolved}" ]]; then
            die "Target path must be the Git worktree root: ${target_resolved}"
            return 1
        fi

        TARGET_ACQUISITION_ACTION="use-existing"
    else
        TARGET_ACQUISITION_ACTION="clone"
    fi

    log "SOURCE_ACQUISITION=${SOURCE_ACQUISITION_ACTION}"
    log "TARGET_ACQUISITION=${TARGET_ACQUISITION_ACTION}"

    log "PASS: repository acquisition requirements determined."
}


###############################################################################
# 03 — Determine authentication requirements
###############################################################################

phase_03_authentication()
{
    local source_probe_required=false
    local target_probe_required=false

    phase "03 — Determine authentication requirements"

    SOURCE_CLONE_URL="$(
        repository_clone_url "${SOURCE_REPO}"
    )" || {
        die "Unable to construct source clone URL."
        return 1
    }

    TARGET_CLONE_URL="$(
        repository_clone_url "${TARGET_REPO}"
    )" || {
        die "Unable to construct target clone URL."
        return 1
    }

    if [[ "${SOURCE_ACQUISITION_ACTION}" == "clone" ]]; then
        source_probe_required=true
    fi

    if [[ "${TARGET_ACQUISITION_ACTION}" == "clone" ]]; then
        target_probe_required=true
    fi

    if [[ "${source_probe_required}" == false \
        && "${target_probe_required}" == false ]]; then
        log "AUTHENTICATION=not-required"
        log_verbose "Both repositories already exist locally; no remote access probe needed."
        log "PASS: authentication requirements determined."
        return 0
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        if [[ "${source_probe_required}" == true ]]; then
            log "DRY RUN: would probe source repository access: ${SOURCE_CLONE_URL}"
        fi

        if [[ "${target_probe_required}" == true ]]; then
            log "DRY RUN: would probe target repository access: ${TARGET_CLONE_URL}"
        fi

        log "AUTHENTICATION=unknown-dry-run"
        log "PASS: authentication requirements determined without network access."
        return 0
    fi

    if [[ "${source_probe_required}" == true ]]; then
        if ! GIT_TERMINAL_PROMPT=0 git ls-remote \
            --exit-code \
            "${SOURCE_CLONE_URL}" \
            HEAD >/dev/null 2>&1; then
            die "Cannot access source repository noninteractively: ${SOURCE_CLONE_URL}. Configure Git credentials or SSH access before rerunning."
            return 1
        fi

        log_verbose "Source repository remote access verified."
    fi

    if [[ "${target_probe_required}" == true ]]; then
        if ! GIT_TERMINAL_PROMPT=0 git ls-remote \
            --exit-code \
            "${TARGET_CLONE_URL}" \
            HEAD >/dev/null 2>&1; then
            die "Cannot access target repository noninteractively: ${TARGET_CLONE_URL}. Configure Git credentials or SSH access before rerunning."
            return 1
        fi

        log_verbose "Target repository remote access verified."
    fi

    log "AUTHENTICATION=available"
    log "PASS: required remote repository access verified noninteractively."
}


###############################################################################
# 04 — Prepare source repository
###############################################################################

phase_04_prepare_source()
{
    local source_resolved
    local configured_repo
    local origin
    local origin_repo
    local head
    local resolved_ref
    local status_output

    phase "04 — Prepare source repository"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"

    configured_repo="$(
        normalize_repository_slug "${SOURCE_REPO}"
    )" || {
        die "Invalid configured source repository: ${SOURCE_REPO}"
        return 1
    }

    if [[ ! "${SOURCE_REF}" =~ ^[0-9A-Fa-f]{40}$ ]]; then
        die "Source ref must be a full 40-character commit SHA for reproducible preparation: ${SOURCE_REF}"
        return 1
    fi

    if [[ "${SOURCE_ACQUISITION_ACTION}" == "clone" ]]; then
        if [[ "${DRY_RUN}" == true ]]; then
            log "DRY RUN: would clone source repository to ${source_resolved}"
            log "DRY RUN: would check out source commit detached at ${SOURCE_REF,,}"
            log "PASS: source repository preparation planned."
            return 0
        fi

        mkdir -p -- "$(dirname "${source_resolved}")"

        if ! GIT_TERMINAL_PROMPT=0 git clone \
            --no-checkout \
            "${SOURCE_CLONE_URL}" \
            "${source_resolved}"; then
            die "Failed to clone source repository: ${SOURCE_CLONE_URL}"
            return 1
        fi
    fi

    origin="$(
        git -C "${source_resolved}" remote get-url origin 2>/dev/null
    )" || {
        die "Source repository has no readable origin remote."
        return 1
    }

    origin_repo="$(
        normalize_repository_slug "${origin}"
    )" || {
        die "Unable to normalize source origin: ${origin}"
        return 1
    }

    if [[ "${origin_repo,,}" != "${configured_repo,,}" ]]; then
        die "Source origin mismatch. Expected ${configured_repo}; found ${origin_repo}."
        return 1
    fi

    status_output="$(
        git -C "${source_resolved}" status \
            --porcelain=v1 \
            --untracked-files=all
    )"

    if [[ -n "${status_output}" ]]; then
        die "Source repository must be clean before preparation."
        return 1
    fi

    if resolved_ref="$(
        git -C "${source_resolved}" rev-parse \
            --verify "${SOURCE_REF}^{commit}" 2>/dev/null
    )"; then
        :
    else
        if [[ "${DRY_RUN}" == true ]]; then
            log "DRY RUN: source commit is not present locally; would fetch ${SOURCE_REF,,} from origin."
            log "PASS: source repository preparation planned."
            return 0
        fi

        if ! GIT_TERMINAL_PROMPT=0 git -C "${source_resolved}" fetch \
            --no-tags \
            origin \
            "${SOURCE_REF}"; then
            die "Failed to fetch configured source commit: ${SOURCE_REF}"
            return 1
        fi

        resolved_ref="$(
            git -C "${source_resolved}" rev-parse \
                --verify "${SOURCE_REF}^{commit}" 2>/dev/null
        )" || {
            die "Configured source commit is still unavailable after fetch: ${SOURCE_REF}"
            return 1
        }
    fi

    if [[ "${resolved_ref,,}" != "${SOURCE_REF,,}" ]]; then
        die "Configured source SHA resolved unexpectedly: ${resolved_ref}"
        return 1
    fi

    if head="$(
        git -C "${source_resolved}" rev-parse HEAD 2>/dev/null
    )"; then
        :
    else
        head=""
    fi

    if [[ "${head,,}" == "${resolved_ref,,}" ]]; then
        log "SOURCE_REVISION=already-prepared"
    elif [[ "${DRY_RUN}" == true ]]; then
        log "SOURCE_REVISION=would-checkout"
        log "DRY RUN: would check out detached source commit ${resolved_ref}."
    else
        if ! git -C "${source_resolved}" checkout \
            --detach \
            "${resolved_ref}"; then
            die "Failed to check out configured source commit: ${resolved_ref}"
            return 1
        fi

        log "SOURCE_REVISION=checked-out"
    fi

    log_verbose "Source origin: ${origin}"
    log_verbose "Prepared source commit: ${resolved_ref}"

    log "PASS: source repository prepared at the configured immutable revision."
}


###############################################################################
# 05 — Prepare target repository
###############################################################################

phase_05_prepare_target()
{
    local target_resolved
    local configured_repo
    local origin
    local origin_repo
    local head

    phase "05 — Prepare target repository"

    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    configured_repo="$(
        normalize_repository_slug "${TARGET_REPO}"
    )" || {
        die "Invalid configured target repository: ${TARGET_REPO}"
        return 1
    }

    if [[ "${TARGET_ACQUISITION_ACTION}" == "clone" ]]; then
        if [[ "${DRY_RUN}" == true ]]; then
            log "DRY RUN: would clone target repository to ${target_resolved}"
            log "PASS: target repository preparation planned."
            return 0
        fi

        mkdir -p -- "$(dirname "${target_resolved}")"

        if ! GIT_TERMINAL_PROMPT=0 git clone \
            "${TARGET_CLONE_URL}" \
            "${target_resolved}"; then
            die "Failed to clone target repository: ${TARGET_CLONE_URL}"
            return 1
        fi
    fi

    if ! git -C "${target_resolved}" rev-parse \
        --is-inside-work-tree >/dev/null 2>&1; then
        die "Prepared target is not a Git worktree: ${target_resolved}"
        return 1
    fi

    origin="$(
        git -C "${target_resolved}" remote get-url origin 2>/dev/null
    )" || {
        die "Target repository has no readable origin remote."
        return 1
    }

    origin_repo="$(
        normalize_repository_slug "${origin}"
    )" || {
        die "Unable to normalize target origin: ${origin}"
        return 1
    }

    if [[ "${origin_repo,,}" != "${configured_repo,,}" ]]; then
        die "Target origin mismatch. Expected ${configured_repo}; found ${origin_repo}."
        return 1
    fi

    if head="$(
        git -C "${target_resolved}" rev-parse HEAD 2>/dev/null
    )"; then
        log "TARGET_HEAD=${head}"
    else
        log "TARGET_HEAD=unborn"
    fi

    log_verbose "Target origin: ${origin}"
    log "TARGET_WORKTREE=preserved"

    log "PASS: target repository prepared without resetting or modifying existing work."
}


###############################################################################
# 06 — Verify prepared repositories
###############################################################################

phase_06_verify()
{
    local source_resolved
    local target_resolved
    local source_root
    local target_root
    local source_origin
    local target_origin
    local source_origin_repo
    local target_origin_repo
    local configured_source_repo
    local configured_target_repo
    local source_head
    local source_status
    local target_head

    phase "06 — Verify prepared repositories"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    if [[ "${DRY_RUN}" == true         && ( "${SOURCE_ACQUISITION_ACTION}" == "clone"             || "${TARGET_ACQUISITION_ACTION}" == "clone" ) ]]; then
        log "VERIFICATION=deferred-dry-run"
        log "DRY RUN: final repository verification is deferred because one or more repositories would be cloned."
        log "PASS: prepared-repository verification plan is valid."
        return 0
    fi

    source_root="$(
        git -C "${source_resolved}" rev-parse --show-toplevel
    )" || {
        die "Prepared source is not a readable Git worktree."
        return 1
    }

    target_root="$(
        git -C "${target_resolved}" rev-parse --show-toplevel
    )" || {
        die "Prepared target is not a readable Git worktree."
        return 1
    }

    source_root="$(canonicalize_path "${source_root}")"
    target_root="$(canonicalize_path "${target_root}")"

    if [[ "${source_root}" != "${source_resolved}" ]]; then
        die "Prepared source path is not the Git worktree root."
        return 1
    fi

    if [[ "${target_root}" != "${target_resolved}" ]]; then
        die "Prepared target path is not the Git worktree root."
        return 1
    fi

    configured_source_repo="$(
        normalize_repository_slug "${SOURCE_REPO}"
    )" || {
        die "Invalid configured source repository."
        return 1
    }

    configured_target_repo="$(
        normalize_repository_slug "${TARGET_REPO}"
    )" || {
        die "Invalid configured target repository."
        return 1
    }

    source_origin="$(
        git -C "${source_resolved}" remote get-url origin
    )" || {
        die "Prepared source has no readable origin."
        return 1
    }

    target_origin="$(
        git -C "${target_resolved}" remote get-url origin
    )" || {
        die "Prepared target has no readable origin."
        return 1
    }

    source_origin_repo="$(
        normalize_repository_slug "${source_origin}"
    )" || {
        die "Unable to normalize prepared source origin."
        return 1
    }

    target_origin_repo="$(
        normalize_repository_slug "${target_origin}"
    )" || {
        die "Unable to normalize prepared target origin."
        return 1
    }

    if [[ "${source_origin_repo,,}" != "${configured_source_repo,,}" ]]; then
        die "Prepared source origin does not match configured source repository."
        return 1
    fi

    if [[ "${target_origin_repo,,}" != "${configured_target_repo,,}" ]]; then
        die "Prepared target origin does not match configured target repository."
        return 1
    fi

    source_head="$(
        git -C "${source_resolved}" rev-parse HEAD
    )" || {
        die "Prepared source has no HEAD commit."
        return 1
    }

    if [[ "${source_head,,}" != "${SOURCE_REF,,}" ]]; then
        die "Prepared source HEAD does not equal configured source SHA. Expected ${SOURCE_REF,,}; found ${source_head,,}."
        return 1
    fi

    source_status="$(
        git -C "${source_resolved}" status \
            --porcelain=v1 \
            --untracked-files=all
    )"

    if [[ -n "${source_status}" ]]; then
        die "Prepared source repository is not clean."
        return 1
    fi

    if target_head="$(
        git -C "${target_resolved}" rev-parse HEAD 2>/dev/null
    )"; then
        log "VERIFIED_TARGET_HEAD=${target_head}"
    else
        log "VERIFIED_TARGET_HEAD=unborn"
    fi

    log "VERIFIED_SOURCE_COMMIT=${source_head}"
    log "SOURCE_WORKTREE=clean"
    log "TARGET_WORKTREE=preserved"

    log "PASS: prepared repositories satisfy bootstrap preconditions."
}


###############################################################################
# 07 — Report bootstrap invocation
###############################################################################

phase_07_report()
{
    local source_resolved
    local target_resolved
    local configured_source_repo
    local configured_target_repo

    phase "07 — Report bootstrap invocation"

    source_resolved="$(canonicalize_path "${SOURCE_DIR}")"
    target_resolved="$(canonicalize_path "${TARGET_DIR}")"

    configured_source_repo="$(
        normalize_repository_slug "${SOURCE_REPO}"
    )" || {
        die "Invalid configured source repository."
        return 1
    }

    configured_target_repo="$(
        normalize_repository_slug "${TARGET_REPO}"
    )" || {
        die "Invalid configured target repository."
        return 1
    }

    log "Repositories are prepared for framework transformation."
    log ""
    log "Next invocation:"
    printf '%s\n' \
        "\"${target_resolved}/bootstrap/bootstrap-framework.sh\" \\" \
        "    --source-dir \"${source_resolved}\" \\" \
        "    --source-repo \"${configured_source_repo}\" \\" \
        "    --source-ref \"${SOURCE_REF,,}\" \\" \
        "    --source-prefix '<SOURCE_PREFIX>' \\" \
        "    --target-dir \"${target_resolved}\" \\" \
        "    --target-repo \"${configured_target_repo}\" \\" \
        "    --framework-id '<FRAMEWORK_ID>' \\" \
        "    --framework-name '<FRAMEWORK_NAME>' \\" \
        "    --framework-prefix '<FRAMEWORK_PREFIX>'"

    log ""
    log "Replace the four angle-bracket placeholders with framework-specific identity values."
    log "PASS: preparation handoff reported."
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

    phase_01_preflight
    phase_02_determine_requirements
    phase_03_authentication
    phase_04_prepare_source
    phase_05_prepare_target
    phase_06_verify
    phase_07_report
}


main "$@"
