#!/usr/bin/env sh
set -eu

PYTHON_VERSION=""
PACKAGE_SPEC="tradingcodex"
PACKAGE_SPEC_SET="0"
WORKSPACE=""
RUN_DOCTOR="1"
UPDATE="0"
DEV="0"

usage() {
  cat <<'USAGE'
TradingCodex POSIX installer for macOS and Linux.
Native Windows: use `uvx --refresh --from tradingcodex tcx attach .`, then `tcx.cmd doctor`.

Usage:
  install.sh [options] <workspace>

Options:
  --from <package-spec>  Install from a PyPI name, path, URL, or PEP 508 spec.
  --dev                 Bootstrap from this TradingCodex source checkout.
  --python <version>    Python version for uvx. Default: uv selects a compatible Python.
  --update              Update an existing TradingCodex workspace.
  --no-doctor           Skip ./tcx doctor after bootstrap or update.
  -h, --help            Show this help.

Examples:
  install.sh .
  install.sh ~/tradingcodex-workspaces/apple-research
  install.sh --update .
  install.sh --dev /path/to/empty-workspace
  install.sh --dev --update /path/to/existing-workspace
  install.sh --from /path/to/tradingcodex .
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --from)
      if [ "$#" -lt 2 ]; then
        echo "install.sh: --from requires a package spec" >&2
        exit 2
      fi
      PACKAGE_SPEC="$2"
      PACKAGE_SPEC_SET="1"
      shift 2
      ;;
    --dev)
      DEV="1"
      shift
      ;;
    --python)
      if [ "$#" -lt 2 ]; then
        echo "install.sh: --python requires a version" >&2
        exit 2
      fi
      PYTHON_VERSION="$2"
      shift 2
      ;;
    --update)
      UPDATE="1"
      shift
      ;;
    --no-doctor)
      RUN_DOCTOR="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "install.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [ -n "$WORKSPACE" ]; then
        echo "install.sh: only one workspace path is supported" >&2
        usage >&2
        exit 2
      fi
      WORKSPACE="$1"
      shift
      ;;
  esac
done

if [ -z "$WORKSPACE" ] && [ "$#" -gt 0 ]; then
  WORKSPACE="$1"
  shift
fi

if [ -z "$WORKSPACE" ]; then
  usage >&2
  exit 2
fi

if [ "$DEV" = "1" ] && [ "$PACKAGE_SPEC_SET" = "1" ]; then
  echo "install.sh: --dev and --from cannot be used together" >&2
  exit 2
fi

if [ "$DEV" = "1" ]; then
  SOURCE_ROOT=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
  if [ ! -f "$SOURCE_ROOT/pyproject.toml" ] \
    || [ ! -f "$SOURCE_ROOT/tradingcodex_cli/__main__.py" ] \
    || [ ! -f "$SOURCE_ROOT/tradingcodex_service/version.py" ] \
    || [ ! -d "$SOURCE_ROOT/workspace_templates/modules" ]; then
    echo "install.sh: --dev requires install.sh from a TradingCodex source checkout" >&2
    exit 2
  fi
  PACKAGE_SPEC="$SOURCE_ROOT"
  _TRADINGCODEX_DEV_SOURCE_ROOT="$SOURCE_ROOT"
  export _TRADINGCODEX_DEV_SOURCE_ROOT
fi

ensure_uvx() {
  if command -v uvx >/dev/null 2>&1; then
    return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  echo "install.sh: uv/uvx not found; installing uv into the user environment" >&2
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    echo "install.sh: install uv first: https://docs.astral.sh/uv/" >&2
    exit 127
  fi
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  export PATH
  if ! command -v uvx >/dev/null 2>&1 && ! command -v uv >/dev/null 2>&1; then
    echo "install.sh: uv installation finished, but uvx is still not on PATH" >&2
    exit 127
  fi
}

run_uvx() {
  if command -v uvx >/dev/null 2>&1; then
    uvx "$@"
  else
    uv tool run "$@"
  fi
}

run_tradingcodex() {
  if [ "$DEV" = "1" ] && [ -n "$PYTHON_VERSION" ]; then
    run_uvx --isolated --refresh --python "$PYTHON_VERSION" --with-editable "$PACKAGE_SPEC" python -m tradingcodex_cli "$@"
  elif [ "$DEV" = "1" ]; then
    run_uvx --isolated --refresh --with-editable "$PACKAGE_SPEC" python -m tradingcodex_cli "$@"
  elif [ -n "$PYTHON_VERSION" ]; then
    run_uvx --isolated --refresh --python "$PYTHON_VERSION" --from "$PACKAGE_SPEC" python -m tradingcodex_cli "$@"
  else
    run_uvx --isolated --refresh --from "$PACKAGE_SPEC" python -m tradingcodex_cli "$@"
  fi
}

validate_package_spec() {
  PACKAGE_SPEC_LOWER=$(printf '%s' "$PACKAGE_SPEC" | tr '[:upper:]' '[:lower:]')
  case "$PACKAGE_SPEC" in
    ""|*"$(printf '\r')"*|*'
'*)
      echo "install.sh: package source is invalid" >&2
      exit 2
      ;;
    -*)
      echo "install.sh: package source must not begin with an option" >&2
      exit 2
      ;;
    *\?*|*\#*)
      echo "install.sh: package source URLs must not contain a query or fragment" >&2
      exit 2
      ;;
  esac
  case "$PACKAGE_SPEC_LOWER" in
    *token=*|*password=*|*secret=*|*signature=*|*credential=*|*api_key=*|*api-key=*|*access_key=*|*access-key=*)
      echo "install.sh: package source must not contain inline secrets" >&2
      exit 2
      ;;
    *://*)
      url_prefix=${PACKAGE_SPEC_LOWER%%://*}
      url_scheme=${url_prefix##* }
      case "$url_scheme" in
        https|git+https|git+ssh|ssh|file)
          ;;
        *)
          echo "install.sh: package source URL scheme is unsupported" >&2
          exit 2
          ;;
      esac
      url_authority=${PACKAGE_SPEC_LOWER#*://}
      url_authority=${url_authority%%/*}
      case "$url_authority" in
        *@*)
          echo "install.sh: package source URL must not contain credentials" >&2
          exit 2
          ;;
      esac
      if [ "$url_scheme" = "file" ] && [ -n "$url_authority" ]; then
        echo "install.sh: package source file URL must be local" >&2
        exit 2
      fi
      ;;
    *@*:*)
      echo "install.sh: package source must not use SCP-style remote syntax" >&2
      exit 2
      ;;
  esac
}

validate_package_spec
ensure_uvx

if [ "$UPDATE" = "1" ] && [ "$DEV" = "1" ]; then
  echo "install.sh: updating TradingCodex development workspace: $WORKSPACE" >&2
elif [ "$UPDATE" = "1" ]; then
  echo "install.sh: updating TradingCodex workspace: $WORKSPACE" >&2
elif [ "$DEV" = "1" ]; then
  echo "install.sh: bootstrapping TradingCodex development workspace: $WORKSPACE" >&2
else
  echo "install.sh: bootstrapping TradingCodex workspace: $WORKSPACE" >&2
fi
# Keep the package-runner cache available while TradingCodex provisions its
# separate durable Python under TRADINGCODEX_HOME. The uvx interpreter itself
# is never persisted into generated launchers or project MCP configuration.
unset UV_NO_CACHE

if [ "$UPDATE" = "1" ] && [ "$DEV" = "1" ]; then
  run_tradingcodex update "$WORKSPACE" --dev --no-doctor
elif [ "$UPDATE" = "1" ]; then
  run_tradingcodex update "$WORKSPACE" --from "$PACKAGE_SPEC" --no-doctor
elif [ "$DEV" = "1" ]; then
  run_tradingcodex attach "$WORKSPACE" --dev
else
  run_tradingcodex attach "$WORKSPACE" --from "$PACKAGE_SPEC"
fi

if [ "$RUN_DOCTOR" = "1" ]; then
  (cd "$WORKSPACE" && ./tcx doctor)
fi

cat >&2 <<EOF

install.sh: TradingCodex workspace is ready: $WORKSPACE
install.sh: fully quit and restart Codex, then open this generated workspace
install.sh: and start from a new thread so project MCP config is reloaded.
EOF
