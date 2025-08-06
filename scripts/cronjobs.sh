#!/bin/bash
# scripts/cronjobs.sh - Wrapper script for cron jobs
# Handles kubectl context and runs audit scripts properly

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"  # Go up one level from scripts/
LOG_FILE="$PROJECT_DIR/reports/audit.log"
KUBECONFIG_FILE="$HOME/.kube/smile-ovh.yaml"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

# Ensure reports directory exists
mkdir -p "$PROJECT_DIR/reports"

# Function to log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to check prerequisites
check_prereqs() {
    # Check if kubeconfig exists
    if [[ ! -f "$KUBECONFIG_FILE" ]]; then
        log "ERROR: kubectl config not found at $KUBECONFIG_FILE"
        return 1
    fi

    # Check if venv python exists
    if [[ ! -f "$VENV_PYTHON" ]]; then
        log "ERROR: Python venv not found at $VENV_PYTHON"
        return 1
    fi

    # Check if kubectl is accessible
    export KUBECONFIG="$KUBECONFIG_FILE"
    if ! kubectl cluster-info &>/dev/null; then
        log "ERROR: Cannot connect to Kubernetes cluster"
        return 1
    fi

    return 0
}

# Function to run current audit
run_current_audit() {
    log "Starting current audit..."

    export KUBECONFIG="$KUBECONFIG_FILE"
    cd "$PROJECT_DIR"

    if "$VENV_PYTHON" resource_audit.py --mode current >> "$LOG_FILE" 2>&1; then
        log "Current audit completed successfully"
        return 0
    else
        log "ERROR: Current audit failed"
        return 1
    fi
}

# Function to run extended operational audit
run_extended_audit() {
    log "Starting extended operational audit..."

    export KUBECONFIG="$KUBECONFIG_FILE"
    cd "$PROJECT_DIR"

    if "$VENV_PYTHON" resource_audit.py --mode extended >> "$LOG_FILE" 2>&1; then
        log "Extended audit completed successfully"
        return 0
    else
        log "ERROR: Extended audit failed"
        return 1
    fi
}

# Function to run dashboard summary
run_dashboard() {
    log "Starting dashboard summary..."

    export KUBECONFIG="$KUBECONFIG_FILE"
    cd "$PROJECT_DIR"

    if "$VENV_PYTHON" audit_dashboard.py >> "$LOG_FILE" 2>&1; then
        log "Dashboard summary completed successfully"
        return 0
    else
        log "ERROR: Dashboard summary failed"
        return 1
    fi
}

# Function to cleanup old files
cleanup_old_files() {
    log "Cleaning up old files..."

    cd "$PROJECT_DIR/reports"

    # Remove CSV files older than 14 days
    find . -name "*.csv" -type f -mtime +14 -delete 2>/dev/null || true

    # Remove MD files older than 7 days (except weekly summaries)
    find . -name "recommendations_*.md" -type f -mtime +7 -delete 2>/dev/null || true
    find . -name "usage_recommendations_*.md" -type f -mtime +7 -delete 2>/dev/null || true

    # Keep only last 5 weekly summaries
    ls -t weekly_summary_*.md 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true

    log "Cleanup completed"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [current|extended|dashboard|cleanup|health]"
    echo ""
    echo "Commands:"
    echo "  current   - Run current state audit"
    echo "  extended  - Run extended operational metrics audit"
    echo "  dashboard - Run dashboard summary"
    echo "  cleanup   - Clean up old report files"
    echo "  health    - Check system health and prerequisites"
    echo ""
    echo "Examples:"
    echo "  $0 current          # For cron hourly jobs"
    echo "  $0 extended         # For detailed operational analysis"
    echo "  $0 health           # Check if everything works"
}

# Function to run health check
run_health_check() {
    log "Running health check..."

    if check_prereqs; then
        log "✅ All prerequisites OK"

        # Test kubectl
        export KUBECONFIG="$KUBECONFIG_FILE"
        local node_count=$(kubectl get nodes --no-headers 2>/dev/null | wc -l)
        local pod_count=$(kubectl get pods -A --no-headers 2>/dev/null | wc -l)

        log "📊 Cluster status: $node_count nodes, $pod_count pods"
        log "✅ Health check passed"
        return 0
    else
        log "❌ Health check failed"
        return 1
    fi
}

# Main script logic
main() {
    local command="${1:-current}"

    log "=== K8s Audit Runner Started (command: $command) ==="

    case "$command" in
        "current")
            if check_prereqs && run_current_audit; then
                log "=== Current audit completed successfully ==="
                exit 0
            else
                log "=== Current audit failed ==="
                exit 1
            fi
            ;;
        "extended")
            if check_prereqs && run_extended_audit; then
                log "=== Extended audit completed successfully ==="
                exit 0
            else
                log "=== Extended audit failed ==="
                exit 1
            fi
            ;;
        "dashboard")
            if check_prereqs && run_dashboard; then
                log "=== Dashboard summary completed successfully ==="
                exit 0
            else
                log "=== Dashboard summary failed ==="
                exit 1
            fi
            ;;
        "cleanup")
            cleanup_old_files
            log "=== Cleanup completed ==="
            exit 0
            ;;
        "health")
            if run_health_check; then
                exit 0
            else
                exit 1
            fi
            ;;
        "help"|"-h"|"--help")
            show_usage
            exit 0
            ;;
        *)
            log "ERROR: Unknown command '$command'"
            show_usage
            exit 1
            ;;
    esac
}

# Trap to ensure we log completion
trap 'log "Script interrupted"' INT TERM

# Run main function
main "$@"