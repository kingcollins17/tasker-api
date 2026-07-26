import re

def process_file(filepath, funcs_to_target):
    with open(filepath, 'r') as f:
        lines = f.readlines()
        
    out = []
    i = 0
    in_target_func = False
    
    has_timer = any("from app.core.utils.timer import Timer" in l for l in lines)
    if not has_timer:
        out.append("from app.core.utils.timer import Timer\n")
        out.append("from app.core.services.logger_service import get_logger_service_manual\n")
        
    while i < len(lines):
        line = lines[i]
        
        found_target = False
        for func_def, metric_name in funcs_to_target.items():
            if line.startswith(func_def):
                in_target_func = metric_name
                found_target = True
                break
                
        if line.startswith("async def ") and not found_target:
            in_target_func = False
            
        if in_target_func and "async with async_session_maker() as session:" in line:
            out.append(line)
            session_indent = line[:line.find("async with")]
            body_indent = session_indent + "    "
            out.append(f"{body_indent}system_logger = get_logger_service_manual(session)\n")
            out.append(f"{body_indent}timer = Timer()\n")
            out.append(f"{body_indent}timer.start()\n")
            out.append(f"{body_indent}try:\n")
            
            i += 1
            while i < len(lines):
                inner_line = lines[i]
                if inner_line.strip() == "":
                    out.append(inner_line)
                    i += 1
                    
                    if i == len(lines):
                        # Reached EOF while inside block
                        out.append(f"{body_indent}    await system_logger.metric('{in_target_func}', timer.stop(), source='celery.{in_target_func}')\n")
                        out.append(f"{body_indent}except Exception as e:\n")
                        out.append(f"{body_indent}    await system_logger.error(f'{in_target_func} Failed: {{str(e)}}', source='celery.{in_target_func}')\n")
                        out.append(f"{body_indent}    raise e\n")
                        in_target_func = False
                        break
                    continue
                    
                inner_indent = len(inner_line) - len(inner_line.lstrip())
                if inner_indent <= len(session_indent) and inner_line.strip() != "":
                    out.append(f"{body_indent}    await system_logger.metric('{in_target_func}', timer.stop(), source='celery.{in_target_func}')\n")
                    out.append(f"{body_indent}except Exception as e:\n")
                    out.append(f"{body_indent}    await system_logger.error(f'{in_target_func} Failed: {{str(e)}}', source='celery.{in_target_func}')\n")
                    out.append(f"{body_indent}    raise e\n")
                    in_target_func = False
                    break
                else:
                    out.append("    " + inner_line)
                    i += 1
                    
                    if i == len(lines):
                        # Reached EOF while inside block
                        out.append(f"{body_indent}    await system_logger.metric('{in_target_func}', timer.stop(), source='celery.{in_target_func}')\n")
                        out.append(f"{body_indent}except Exception as e:\n")
                        out.append(f"{body_indent}    await system_logger.error(f'{in_target_func} Failed: {{str(e)}}', source='celery.{in_target_func}')\n")
                        out.append(f"{body_indent}    raise e\n")
                        in_target_func = False
                        break
            continue
            
        out.append(line)
        i += 1
        
    with open(filepath, 'w') as f:
        f.writelines(out)


process_file("app/features/tasks/celery/metrics.py", {
    "async def _sync_provider_metrics_async(": "sync_provider_metrics",
    "async def _sync_service_metrics_async(": "sync_service_metrics",
})

process_file("app/features/notifications/tasks.py", {
    "async def _fan_out_notification(": "process_notification",
    "async def _process_batch(": "process_recipient_batch",
    "async def _send_email_batch(": "send_email_batch",
    "async def _send_sms_batch(": "send_sms_batch",
    "async def _send_push_batch(": "send_push_batch",
    "async def _send_whatsapp_batch(": "send_whatsapp_batch",
})

process_file("app/features/reviews/celery/tasks.py", {
    "async def _sync_user_ratings_async(": "sync_user_ratings",
})

process_file("app/features/credibility/celery/tasks.py", {
    "async def _sync_user_credibility_score_async(": "sync_user_credibility_score",
})

