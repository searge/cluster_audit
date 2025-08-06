#!/bin/bash
# weekly_audit_review.sh

cd ~/Dev/k8s

echo "📊 Weekly Kubernetes Audit Review"
echo "================================="

# 1. Dashboard summary
/home/sebor/Dev/k8s/.venv/bin/python audit_dashboard.py

# 2. Quick stats
echo ""
echo "📁 File Statistics:"
echo "CSV files: $(ls reports/*.csv 2>/dev/null | wc -l)"
echo "Total size: $(du -sh reports/ 2>/dev/null | cut -f1)"

# 3. Latest critical issues
echo ""
echo "🚨 Latest Critical Issues:"
latest_ns=$(ls reports/namespaces_summary_*.csv | tail -1)
if [ -f "$latest_ns" ]; then
    echo "Top 5 problematic namespaces:"
    head -6 "$latest_ns" | tail -5 | cut -d',' -f2,5,6 | column -t -s','
fi

# 4. Cleanup recommendations
echo ""
echo "🧹 Cleanup Commands:"
echo "kubectl delete pod --field-selector=status.phase==Failed -A"
echo "kubectl delete pod --field-selector=status.phase==Succeeded -A"

