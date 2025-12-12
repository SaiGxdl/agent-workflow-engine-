#!/bin/bash
# Demo script: Create, run, and query a workflow

set -e

BASE_URL="http://localhost:8000"

echo "🔍 Agent Workflow Engine - Demo"
echo "================================"
echo ""

# Step 1: Create workflow
echo "📋 Step 1: Creating workflow..."
WORKFLOW_RESPONSE=$(curl -s -X POST "$BASE_URL/graph/create" \
  -H "Content-Type: application/json" \
  -d '{
    "nodes": {
      "extract_functions": {"func": "extract_functions"},
      "check_complexity": {"func": "check_complexity"},
      "detect_issues": {"func": "detect_issues"},
      "suggest_improvements": {"func": "suggest_improvements"},
      "end": {"func": "end_node"}
    },
    "edges": {
      "extract_functions": ["check_complexity"],
      "check_complexity": ["detect_issues"],
      "detect_issues": ["suggest_improvements"],
      "suggest_improvements": ["suggest_improvements", "end"]
    },
    "conditions": {
      "suggest_improvements->end": "state['"'"'quality_score'"'"'] >= 0.9",
      "suggest_improvements->suggest_improvements": "state['"'"'quality_score'"'"'] < 0.9 and state.get('"'"'iterations'"'"', 0) < 5"
    },
    "start_node": "extract_functions"
  }')

GRAPH_ID=$(echo "$WORKFLOW_RESPONSE" | grep -o '"graph_id":"[^"]*' | cut -d'"' -f4)
echo "✓ Workflow created: $GRAPH_ID"
echo "  Response: $WORKFLOW_RESPONSE"
echo ""

# Step 2: Run workflow
echo "🚀 Step 2: Running workflow..."
RUN_RESPONSE=$(curl -s -X POST "$BASE_URL/graph/run" \
  -H "Content-Type: application/json" \
  -d "{
    \"graph_id\": \"$GRAPH_ID\",
    \"initial_state\": {
      \"code\": \"def add(a, b):\\n    return a + b\\n\\ndef complex_func(x, y, z, w, v, u):\\n    # TODO: refactor\\n    if x > 0:\\n        for i in range(y):\\n            z += i\\n    return z\\n\"
    }
  }")

RUN_ID=$(echo "$RUN_RESPONSE" | grep -o '"run_id":"[^"]*' | cut -d'"' -f4)
echo "✓ Workflow executed: $RUN_ID"
echo ""

# Step 3: Query state
echo "📊 Step 3: Querying final state..."
curl -s -X GET "$BASE_URL/graph/state/$RUN_ID" | python -m json.tool
echo ""

echo "✅ Demo complete!"
