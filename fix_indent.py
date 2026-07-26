import os
import sys

def fix_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    out_lines = []
    in_block = False
    block_indent = ""
    target_indent = ""
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Detect the end of the block
        if in_block and line.lstrip().startswith("await system_logger.metric("):
            in_block = False
        
        if in_block:
            # We are inside the block that needs to be indented
            if line.strip() == "":
                out_lines.append(line)
            else:
                # Original line has some indentation.
                # If we are adding try -> async with (8 spaces)
                # But wait, it's easier: just look at the line and add 8 spaces
                out_lines.append("        " + line)
        else:
            out_lines.append(line)
            
            # Detect the start of the block
            if "async with Timer() as timer:" in line:
                # The next line might already be replaced by multi_replace_file_content and indented
                in_block = True
                
        i += 1

    with open(filepath, 'w') as f:
        f.writelines(out_lines)

    # Now verify it
    try:
        import ast
        ast.parse("".join(out_lines))
        print(f"Fixed {filepath}")
    except SyntaxError as e:
        print(f"Still syntax error in {filepath}: {e.lineno}")

files = [
    "app/features/tasks/celery/dispatch.py",
    "app/features/tasks/celery/metrics.py",
    "app/features/notifications/tasks.py",
    "app/features/reviews/celery/tasks.py",
    "app/features/credibility/celery/tasks.py"
]

for f in files:
    fix_file(f)
