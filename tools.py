from typing import List


def detect_long_lines(source: str, threshold: int = 100) -> List[int]:
    """Find line numbers with length > threshold."""
    return [i + 1 for i, line in enumerate(source.split('\n')) if len(line) > threshold]


def detect_todos(source: str) -> List[int]:
    """Find line numbers containing TODO."""
    return [i + 1 for i, line in enumerate(source.split('\n')) if 'TODO' in line]


def detect_missing_docstrings(source: str) -> bool:
    """Check if function has a docstring."""
    lines = source.strip().split('\n')
    for i, line in enumerate(lines):
        if line.strip().startswith('def '):
            # Check if next non-empty line is a docstring
            for j in range(i + 1, len(lines)):
                next_line = lines[j].strip()
                if not next_line:
                    continue
                if '"""' in next_line or "'''" in next_line:
                    return False
                return True
    return False


def count_parameters(source: str) -> int:
    """Count function parameters."""
    import re
    match = re.search(r'def\s+\w+\s*$$(.*?)$$', source)
    if match:
        params = [p.strip() for p in match.group(1).split(',') if p.strip()]
        return len(params)
    return 0


def count_control_flow(source: str) -> int:
    """Count control flow keywords (if, for, while, and, or)."""
    keywords = ['if', 'for', 'while', 'and', 'or']
    count = 0
    for keyword in keywords:
        import re
        count += len(re.findall(r'\b' + keyword + r'\b', source))
    return count


# Tool registry
TOOLS = {
    'detect_long_lines': detect_long_lines,
    'detect_todos': detect_todos,
    'detect_missing_docstrings': detect_missing_docstrings,
    'count_parameters': count_parameters,
    'count_control_flow': count_control_flow,
}
