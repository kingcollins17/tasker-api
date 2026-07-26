import ast
import re
import os

def process_file(filepath, source_prefix):
    with open(filepath, 'r') as f:
        source = f.read()
    
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    
    inserts = []
    
    has_timer = any("from app.core.utils.timer import Timer" in l for l in lines)
    if not has_timer:
        # Check if the file is empty or just has imports
        inserts.append((0, "from app.core.utils.timer import Timer\nfrom app.core.services.logger_service import LoggerService, get_logger_service\n"))

    class EndpointVisitor(ast.NodeVisitor):
        def visit_AsyncFunctionDef(self, node):
            is_endpoint = False
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router":
                        is_endpoint = True
            
            if is_endpoint:
                func_name = node.name
                
                for stmt in node.body:
                    if isinstance(stmt, ast.Try):
                        # check if timer already injected
                        first_stmt = stmt.body[0]
                        if isinstance(first_stmt, ast.Assign) and isinstance(first_stmt.value, ast.Call) and getattr(first_stmt.value.func, 'id', '') == 'Timer':
                            continue # already processed
                            
                        first_stmt_line = stmt.body[0].lineno - 1
                        indent = lines[first_stmt_line][:len(lines[first_stmt_line]) - len(lines[first_stmt_line].lstrip())]
                        
                        inserts.append((first_stmt_line, f"{indent}timer = Timer()\n{indent}timer.start()\n"))
                        
                        class ReturnVisitor(ast.NodeVisitor):
                            def visit_Return(self, ret_node):
                                ret_line = ret_node.lineno - 1
                                ret_indent = lines[ret_line][:len(lines[ret_line]) - len(lines[ret_line].lstrip())]
                                # check if metric already injected
                                prev_line = lines[ret_line-1] if ret_line > 0 else ""
                                if "system_logger.metric" not in prev_line:
                                    inserts.append((ret_line, f"{ret_indent}await system_logger.metric('{func_name}', timer.stop(), source='{source_prefix}.{func_name}')\n"))
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
                                inserts.append((handler_line, f"{handler_indent}await system_logger.warn('{func_name} failed', source='{source_prefix}.{func_name}', metadata={{'detail': str({exc_name}.detail) if hasattr({exc_name}, 'detail') else str({exc_name})}})\n"))
                            elif exc_type == "Exception":
                                inserts.append((handler_line, f"{handler_indent}await system_logger.error(f'{func_name} error: {{str({exc_name})}}', source='{source_prefix}.{func_name}')\n"))
            
            self.generic_visit(node)
            
    EndpointVisitor().visit(tree)
    
    inserts.sort(key=lambda x: x[0], reverse=True)
    for line_idx, text in inserts:
        lines.insert(line_idx, text)
        
    out_source = "".join(lines)
    
    def add_dep(match):
        text = match.group(0)
        if "system_logger: LoggerService" not in text:
            last_paren = text.rfind(")")
            before = text[:last_paren].rstrip()
            if before.endswith("("):
                return before + "\n    system_logger: LoggerService = Depends(get_logger_service)\n" + text[last_paren:]
            else:
                if not before.endswith(","):
                    before += ","
                return before + "\n    system_logger: LoggerService = Depends(get_logger_service)\n" + text[last_paren:]
        return text

    # we only replace dependencies for endpoints if they were modified, but let's just do it for all async def that are endpoints
    # To do this safely, we will just use a simple regex again
    out_source = re.sub(r'async def [a-zA-Z0-9_]+\([^)]*\)\s*(?:->\s*[^:]+)?\s*:', add_dep, out_source)
    
    with open(filepath, 'w') as f:
        f.write(out_source)
        
    print(f"Processed {filepath}")

process_file("app/features/tasks/router/tasks.py", "tasks")
process_file("app/features/tasks/router/assignments.py", "assignments")
import py_compile
py_compile.compile("app/features/tasks/router/tasks.py", doraise=True)
py_compile.compile("app/features/tasks/router/assignments.py", doraise=True)
