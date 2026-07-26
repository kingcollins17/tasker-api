import re

def process_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    out = []
    i = 0
    in_target_func = False
    func_indent = ""
    session_indent = ""
    
    # Imports
    # We need to add the imports for Timer and get_logger_service_manual if not there
    has_imports = any("from app.core.utils.timer import Timer" in l for l in lines)
    if not has_imports:
        out.append("from app.core.utils.timer import Timer\n")
        out.append("from app.core.services.logger_service import get_logger_service_manual\n")
    
    while i < len(lines):
        line = lines[i]
        
        if line.startswith("async def _dispatch_next_candidate_async("):
            in_target_func = "dispatch_next_candidate"
        elif line.startswith("async def _handle_provider_response_async("):
            in_target_func = "handle_provider_response"
        elif line.startswith("async def _complete_task_assignment_async("):
            in_target_func = "complete_task_assignment"
        elif line.startswith("async def "):
            in_target_func = False
            
        if in_target_func and "async with async_session_maker() as session:" in line:
            out.append(line)
            session_indent = line[:line.find("async with")]
            body_indent = session_indent + "    "
            out.append(f"{body_indent}system_logger = get_logger_service_manual(session)\n")
            out.append(f"{body_indent}timer = Timer()\n")
            out.append(f"{body_indent}timer.start()\n")
            out.append(f"{body_indent}try:\n")
            
            # Read until the end of this async with block
            i += 1
            while i < len(lines):
                inner_line = lines[i]
                if inner_line.strip() == "":
                    out.append(inner_line)
                    i += 1
                    continue
                    
                inner_indent = len(inner_line) - len(inner_line.lstrip())
                if inner_indent <= len(session_indent) and inner_line.strip() != "":
                    # Reached the end of the async with block
                    # Add except block
                    out.append(f"{body_indent}    await system_logger.metric('{in_target_func}', timer.stop(), source='celery.{in_target_func}')\n")
                    out.append(f"{body_indent}except Exception as e:\n")
                    out.append(f"{body_indent}    await system_logger.error(f'{in_target_func} Failed: {{str(e)}}', source='celery.{in_target_func}')\n")
                    out.append(f"{body_indent}    raise e\n")
                    in_target_func = False
                    # We shouldn't advance i here because we need to process this line in the main loop
                    break
                else:
                    # Indent by 4 spaces
                    out.append("    " + inner_line)
                    i += 1
            continue

        out.append(line)
        i += 1
        
    with open(filepath, 'w') as f:
        f.writelines(out)

process_file("app/features/tasks/celery/dispatch.py")
