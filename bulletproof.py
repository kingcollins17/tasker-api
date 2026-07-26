import ast

def process_file(filepath, source_prefix):
    with open(filepath, 'r') as f:
        source = f.read()
    
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    
    inserts = []
    
    has_timer = any("from app.core.utils.timer import Timer" in l for l in lines)
    if not has_timer:
        inserts.append((0, 0, "from app.core.utils.timer import Timer\nfrom app.core.services.logger_service import LoggerService, get_logger_service\n"))

    class EndpointVisitor(ast.NodeVisitor):
        def visit_AsyncFunctionDef(self, node):
            is_endpoint = False
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router":
                        is_endpoint = True
            
            if is_endpoint:
                func_name = node.name
                
                body_start = node.body[0].lineno - 1
                def_start = node.lineno - 1
                
                signature_lines = lines[def_start:body_start]
                sig_str = "".join(signature_lines)
                last_paren_idx = sig_str.rfind(")")
                
                if last_paren_idx != -1 and "system_logger: LoggerService" not in sig_str:
                    before = sig_str[:last_paren_idx].rstrip()
                    if before.endswith("("):
                        new_sig = before + "\n    system_logger: LoggerService = Depends(get_logger_service)\n" + sig_str[last_paren_idx:]
                    else:
                        if not before.endswith(","):
                            before += ","
                        new_sig = before + "\n    system_logger: LoggerService = Depends(get_logger_service)\n" + sig_str[last_paren_idx:]
                    
                    inserts.append((def_start, body_start, new_sig))
                
                
                for stmt in node.body:
                    if isinstance(stmt, ast.Try):
                        first_stmt = stmt.body[0]
                        if isinstance(first_stmt, ast.Assign) and isinstance(first_stmt.value, ast.Call) and getattr(first_stmt.value.func, 'id', '') == 'Timer':
                            continue
                            
                        first_stmt_line = stmt.body[0].lineno - 1
                        indent = lines[first_stmt_line][:len(lines[first_stmt_line]) - len(lines[first_stmt_line].lstrip())]
                        
                        inserts.append((first_stmt_line, first_stmt_line, f"{indent}timer = Timer()\n{indent}timer.start()\n"))
                        
                        class ReturnVisitor(ast.NodeVisitor):
                            def visit_FunctionDef(self, node):
                                pass # prevent entering inner sync functions
                            def visit_AsyncFunctionDef(self, node):
                                pass # prevent entering inner async functions
                            def visit_Return(self, ret_node):
                                ret_line = ret_node.lineno - 1
                                ret_indent = lines[ret_line][:len(lines[ret_line]) - len(lines[ret_line].lstrip())]
                                prev_line = lines[ret_line-1] if ret_line > 0 else ""
                                if "system_logger.metric" not in prev_line:
                                    inserts.append((ret_line, ret_line, f"{ret_indent}await system_logger.metric('{func_name}', timer.stop(), source='{source_prefix}.{func_name}')\n"))
                                self.generic_visit(ret_node)
                        ReturnVisitor().visit(stmt)
                        
                        for handler in stmt.handlers:
                            handler_line = handler.body[0].lineno - 1
                            handler_indent = lines[handler_line][:len(lines[handler_line]) - len(lines[handler_line].lstrip())]
                            
                            exc_type = ""
                            if isinstance(handler.type, ast.Name):
                                exc_type = handler.type.id
                            
                            exc_name = handler.name if handler.name else "e"
                            
                            if exc_type == "HTTPException":
                                inserts.append((handler_line, handler_line, f"{handler_indent}await system_logger.warn('{func_name} failed', source='{source_prefix}.{func_name}', metadata={{'detail': str({exc_name}.detail) if hasattr({exc_name}, 'detail') else str({exc_name})}})\n"))
                            elif exc_type == "Exception":
                                inserts.append((handler_line, handler_line, f"{handler_indent}await system_logger.error(f'{func_name} error: {{str({exc_name})}}', source='{source_prefix}.{func_name}')\n"))
            
            self.generic_visit(node)
            
    EndpointVisitor().visit(tree)
    
    inserts.sort(key=lambda x: x[0], reverse=True)
    for start, end, text in inserts:
        if start == end:
            lines.insert(start, text)
        else:
            del lines[start:end]
            for i, line in enumerate(text.splitlines(keepends=True)):
                lines.insert(start + i, line)
        
    out_source = "".join(lines)
    
    with open(filepath, 'w') as f:
        f.write(out_source)
        
    print(f"Processed {filepath}")

for file, prefix in [("app/features/users/router/otp.py", "otp"),
                     ("app/features/users/router/kyc.py", "kyc"), 
                     ("app/features/users/router/payouts.py", "payouts"),
                     ("app/features/users/router/profile.py", "profile"),
                     ("app/features/tasks/router/tasks.py", "tasks"),
                     ("app/features/tasks/router/assignments.py", "assignments")]:
    process_file(file, prefix)

import py_compile
for file in ["app/features/users/router/otp.py", "app/features/users/router/kyc.py", 
             "app/features/users/router/payouts.py", "app/features/users/router/profile.py",
             "app/features/tasks/router/tasks.py", "app/features/tasks/router/assignments.py"]:
    py_compile.compile(file, doraise=True)
print("All files compiled successfully!")
