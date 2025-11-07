#!/usr/bin/env python3
"""
mcpr: MCP-compliant R code execution and management agent
Purpose: Generate, execute, and manage R scripts within a user-specified directory
Requirements: Python 3.8+, MCP SDK, R runtime (Rscript in PATH)
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import base64
import csv
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server

# Configure logging to stderr for debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# R script scaffold template
R_SCAFFOLD = """# mcpr: Primary R Script
# Purpose: Add your analysis functions, data prep, and execution blocks here.
# Style:
# - Use "=" for assignment (not "<-").
# - No space in control statements: if(cond){...}, for(i in xs){...}, while(ok){...}, function(x){...}
# Notes:
# - Keep functions small, documented, and testable.
# - Use explicit library() calls in the "Packages" section.
# - Write outputs (CSV/RDS/plots) into the working directory.

# ---- Packages ----
# library(readr)
# library(dplyr)

# ---- Functions ----
# example_function = function(x){
#   # Add docs about inputs/outputs
#   y = x * 2
#   return(y)
# }

# ---- Main ----
# Uncomment to run:
# result = example_function(21)
# write.csv(data.frame(result=result), "result.csv", row.names=FALSE)

# ---- Session Info ----
# print(sessionInfo())
"""

class MCPRServer:
    def __init__(self):
        self.state_dir = None
        self.state_file = None
        self.workdir = None
        self.primary_file = "agent.r"
        
    def load_state(self) -> Dict[str, Any]:
        """Load state from JSON file"""
        if not self.state_file or not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")
            return {}
    
    def save_state(self, state: Dict[str, Any]) -> None:
        """Save state to JSON file with atomic write"""
        if not self.state_file:
            return
        temp_file = self.state_file.with_suffix('.tmp')
        try:
            with open(temp_file, 'w') as f:
                json.dump(state, f, indent=2, default=str)
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            if temp_file.exists():
                temp_file.unlink()
    
    def ensure_workdir_set(self) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Check if workdir is set and valid"""
        if not self.workdir:
            return False, {"code": "NO_WORKDIR", "message": "Working directory not set. Use set_workdir first.", "hints": ["Call set_workdir with a directory path"]}
        if not self.workdir.exists():
            return False, {"code": "WORKDIR_MISSING", "message": f"Working directory {self.workdir} no longer exists", "hints": ["Recreate or set a new working directory"]}
        return True, None
    
    def is_safe_path(self, path: Path) -> bool:
        """Check if path is within workdir"""
        if not self.workdir:
            return False
        try:
            resolved = path.resolve()
            # For Python 3.8 compatibility
            try:
                return resolved.is_relative_to(self.workdir)
            except AttributeError:
                # Fallback for Python < 3.9
                try:
                    resolved.relative_to(self.workdir)
                    return True
                except ValueError:
                    return False
        except (ValueError, RuntimeError):
            return False
    
    def find_r_executable(self) -> Optional[str]:
        """Find R executable, preferring Rscript"""
        rscript = shutil.which("Rscript")
        if rscript:
            return rscript
        r_exe = shutil.which("R")
        if r_exe:
            return r_exe
        return None
    
    def run_r_command(self, args: List[str], timeout: int = 120) -> Dict[str, Any]:
        """Execute R command and capture output"""
        r_exe = self.find_r_executable()
        if not r_exe:
            return {
                "ok": False,
                "error": {
                    "code": "R_NOT_FOUND",
                    "message": "Rscript not found in PATH. Please install R or add Rscript to PATH.",
                    "hints": ["Install R from https://www.r-project.org/", "Ensure Rscript is in your system PATH"]
                }
            }
        
        start_time = time.time()
        try:
            result = subprocess.run(
                [r_exe] + args,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            duration_ms = int((time.time() - start_time) * 1000)
            return {
                "ok": True,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": duration_ms
            }
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            return {
                "ok": False,
                "error": {
                    "code": "TIMEOUT",
                    "message": f"R execution timed out after {timeout} seconds",
                    "hints": ["Increase timeout_sec parameter", "Check for infinite loops in code"]
                },
                "duration_ms": duration_ms
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "EXEC_ERROR",
                    "message": f"Failed to execute R: {str(e)}"
                }
            }
    
    def scan_directory_files(self) -> Dict[str, float]:
        """Scan directory for files and their mtimes"""
        files = {}
        for item in self.workdir.iterdir():
            if item.is_file() and not item.name.startswith('.'):
                files[item.name] = item.stat().st_mtime
        return files
    
    async def handle_set_workdir(self, path: str, create: bool = True) -> Dict[str, Any]:
        """Set working directory and initialize state"""
        try:
            workdir_path = Path(path).resolve()
            
            if create and not workdir_path.exists():
                workdir_path.mkdir(parents=True, exist_ok=True)
                created = True
            elif not workdir_path.exists():
                return {
                    "ok": False,
                    "error": {
                        "code": "DIR_NOT_FOUND",
                        "message": f"Directory {path} does not exist",
                        "hints": ["Set create=true to create it", "Provide an existing directory path"]
                    }
                }
            else:
                created = False
            
            self.workdir = workdir_path
            self.state_dir = self.workdir / ".mcpr"
            self.state_dir.mkdir(exist_ok=True)
            self.state_file = self.state_dir / "state.json"
            
            # Initialize state
            state = {
                "workdir": str(self.workdir),
                "primary_file": "agent.r",
                "last_run": None,
                "exports_manifest": {}
            }
            self.save_state(state)
            self.primary_file = "agent.r"
            
            # Create agent.r if it doesn't exist
            agent_file = self.workdir / "agent.r"
            if not agent_file.exists():
                agent_file.write_text(R_SCAFFOLD)
                logger.info(f"Created {agent_file}")
            
            return {
                "ok": True,
                "data": {
                    "workdir": str(self.workdir),
                    "created": created,
                    "primary_file": self.primary_file
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "SET_WORKDIR_ERROR",
                    "message": f"Failed to set working directory: {str(e)}"
                }
            }
    
    async def handle_get_state(self) -> Dict[str, Any]:
        """Get current state"""
        if not self.workdir:
            return {
                "ok": True,
                "data": {
                    "state": {
                        "workdir": None,
                        "primary_file": None,
                        "configured": False
                    }
                }
            }
        
        state = self.load_state()
        state["configured"] = True
        state["agent_r_exists"] = (self.workdir / "agent.r").exists()
        state["primary_file_exists"] = (self.workdir / self.primary_file).exists() if self.primary_file else False
        
        return {
            "ok": True,
            "data": {
                "state": state
            }
        }
    
    async def handle_create_r_file(self, filename: str, overwrite: bool = False, scaffold: bool = True) -> Dict[str, Any]:
        """Create a new R file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        # Ensure .r extension
        if not filename.endswith('.r'):
            filename = filename + '.r'
        
        file_path = self.workdir / filename
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if file_path.exists() and not overwrite:
            return {
                "ok": False,
                "error": {
                    "code": "FILE_EXISTS",
                    "message": f"File {filename} already exists",
                    "hints": ["Set overwrite=true to replace it", "Choose a different filename"]
                }
            }
        
        try:
            content = R_SCAFFOLD if scaffold else ""
            file_path.write_text(content)
            
            return {
                "ok": True,
                "data": {
                    "file": filename
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "CREATE_ERROR",
                    "message": f"Failed to create file: {str(e)}"
                }
            }
    
    async def handle_rename_r_file(self, old_name: str, new_name: str, overwrite: bool = False) -> Dict[str, Any]:
        """Rename an R file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        # Ensure .r extension
        if not old_name.endswith('.r'):
            old_name = old_name + '.r'
        if not new_name.endswith('.r'):
            new_name = new_name + '.r'
        
        old_path = self.workdir / old_name
        new_path = self.workdir / new_name
        
        if not self.is_safe_path(old_path) or not self.is_safe_path(new_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if not old_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {old_name} does not exist"
                }
            }
        
        if new_path.exists() and not overwrite:
            return {
                "ok": False,
                "error": {
                    "code": "FILE_EXISTS",
                    "message": f"File {new_name} already exists",
                    "hints": ["Set overwrite=true to replace it", "Choose a different filename"]
                }
            }
        
        try:
            if new_path.exists():
                new_path.unlink()
            old_path.rename(new_path)
            
            # Update primary_file if needed
            primary_updated = False
            if self.primary_file == old_name:
                self.primary_file = new_name
                state = self.load_state()
                state["primary_file"] = new_name
                self.save_state(state)
                primary_updated = True
            
            return {
                "ok": True,
                "data": {
                    "old_name": old_name,
                    "new_name": new_name,
                    "primary_file_updated": primary_updated
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "RENAME_ERROR",
                    "message": f"Failed to rename file: {str(e)}"
                }
            }
    
    async def handle_set_primary_file(self, filename: str) -> Dict[str, Any]:
        """Set the primary R file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if not filename.endswith('.r'):
            filename = filename + '.r'
        
        file_path = self.workdir / filename
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {filename} does not exist"
                }
            }
        
        self.primary_file = filename
        state = self.load_state()
        state["primary_file"] = filename
        self.save_state(state)
        
        return {
            "ok": True,
            "data": {
                "primary_file": filename
            }
        }
    
    async def handle_append_r_code(self, code: str, filename: str = None, ensure_trailing_newline: bool = True) -> Dict[str, Any]:
        """Append code to an R file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if filename is None:
            filename = self.primary_file
        
        if not filename.endswith('.r'):
            filename = filename + '.r'
        
        file_path = self.workdir / filename
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {filename} does not exist",
                    "hints": ["Create the file first with create_r_file"]
                }
            }
        
        try:
            # Read existing content to check for trailing newline
            existing = file_path.read_text()
            
            # Prepare content to append
            if ensure_trailing_newline and existing and not existing.endswith('\n'):
                code = '\n' + code
            
            # Append
            with open(file_path, 'a') as f:
                bytes_written = f.write(code)
            
            return {
                "ok": True,
                "data": {
                    "file": filename,
                    "bytes_written": bytes_written
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "APPEND_ERROR",
                    "message": f"Failed to append to file: {str(e)}"
                }
            }
    
    async def handle_write_r_code(self, code: str, filename: str = None, overwrite: bool = False, use_scaffold_header: bool = True) -> Dict[str, Any]:
        """Write code to an R file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if filename is None:
            filename = self.primary_file
        
        if not filename.endswith('.r'):
            filename = filename + '.r'
        
        file_path = self.workdir / filename
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if file_path.exists() and not overwrite:
            return {
                "ok": False,
                "error": {
                    "code": "FILE_EXISTS",
                    "message": f"File {filename} already exists",
                    "hints": ["Set overwrite=true to replace it", "Use append_r_code to add to existing file"]
                }
            }
        
        try:
            if use_scaffold_header and code:
                # Add scaffold header before code
                content = R_SCAFFOLD + "\n\n" + code
            else:
                content = code
            
            bytes_written = len(content.encode('utf-8'))
            file_path.write_text(content)
            
            return {
                "ok": True,
                "data": {
                    "file": filename,
                    "bytes_written": bytes_written
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "WRITE_ERROR",
                    "message": f"Failed to write file: {str(e)}"
                }
            }
    
    async def handle_run_r_script(self, filename: str = None, args: List[str] = None, timeout_sec: int = 120, save_rdata: bool = True) -> Dict[str, Any]:
        """Execute an R script"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if filename is None:
            filename = self.primary_file
        
        if not filename.endswith('.r'):
            filename = filename + '.r'
        
        file_path = self.workdir / filename
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {filename} does not exist"
                }
            }
        
        # Scan files before execution
        files_before = self.scan_directory_files()
        
        # Prepare R command
        if args is None:
            args = []
        
        if save_rdata:
            # Wrap execution to save workspace
            r_code = f'source("{filename}"); save.image(".mcpr/last_session.RData")'
            cmd_args = ["-e", r_code] + args
        else:
            cmd_args = [str(file_path)] + args
        
        # Execute
        result = self.run_r_command(cmd_args, timeout_sec)
        
        if result.get("ok"):
            # Scan files after execution
            files_after = self.scan_directory_files()
            new_or_modified = []
            for name, mtime in files_after.items():
                if name not in files_before or files_before[name] < mtime:
                    new_or_modified.append(name)
            
            # Update state
            state = self.load_state()
            state["last_run"] = datetime.now().isoformat()
            state["exports_manifest"] = files_after
            self.save_state(state)
            
            return {
                "ok": True,
                "data": {
                    "exit_code": result["exit_code"],
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "duration_ms": result["duration_ms"],
                    "new_or_modified_files": new_or_modified
                }
            }
        else:
            return result
    
    async def handle_run_r_expression(self, expr: str, timeout_sec: int = 60) -> Dict[str, Any]:
        """Execute an R expression"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        result = self.run_r_command(["-e", expr], timeout_sec)
        
        if result.get("ok"):
            return {
                "ok": True,
                "data": {
                    "exit_code": result["exit_code"],
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "duration_ms": result["duration_ms"]
                }
            }
        else:
            return result
    
    async def handle_list_exports(self, glob: str = "*", sort_by: str = "mtime", descending: bool = True, limit: int = 200) -> Dict[str, Any]:
        """List files in working directory"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        try:
            files = []
            for item in self.workdir.glob(glob):
                if item.is_file():
                    # Skip .mcpr unless explicitly requested
                    if item.name.startswith('.mcpr') and '.mcpr' not in glob:
                        continue
                    
                    stat = item.stat()
                    # Guess if text file
                    is_text_guess = item.suffix in ['.r', '.R', '.txt', '.csv', '.tsv', '.json', '.xml', '.html', '.md']
                    
                    files.append({
                        "name": item.name,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "is_text_guess": is_text_guess
                    })
            
            # Sort
            if sort_by == "mtime":
                files.sort(key=lambda x: x["mtime"], reverse=descending)
            elif sort_by == "size":
                files.sort(key=lambda x: x["size"], reverse=descending)
            elif sort_by == "name":
                files.sort(key=lambda x: x["name"], reverse=not descending)
            
            # Limit
            files = files[:limit]
            
            return {
                "ok": True,
                "data": {
                    "files": files
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "LIST_ERROR",
                    "message": f"Failed to list files: {str(e)}"
                }
            }
    
    async def handle_read_export(self, name: str, max_bytes: int = 2000000, as_text: bool = True, encoding: str = "utf-8") -> Dict[str, Any]:
        """Read a file from working directory"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        file_path = self.workdir / name
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {name} does not exist"
                }
            }
        
        try:
            file_size = file_path.stat().st_size
            truncated = file_size > max_bytes
            
            with open(file_path, 'rb') as f:
                data = f.read(max_bytes)
            
            if as_text:
                try:
                    text = data.decode(encoding, errors='replace')
                    return {
                        "ok": True,
                        "data": {
                            "name": name,
                            "text": text,
                            "truncated": truncated
                        }
                    }
                except Exception as e:
                    return {
                        "ok": False,
                        "error": {
                            "code": "DECODE_ERROR",
                            "message": f"Failed to decode file as {encoding}: {str(e)}"
                        }
                    }
            else:
                data_b64 = base64.b64encode(data).decode('ascii')
                return {
                    "ok": True,
                    "data": {
                        "name": name,
                        "data_b64": data_b64,
                        "truncated": truncated
                    }
                }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "READ_ERROR",
                    "message": f"Failed to read file: {str(e)}"
                }
            }
    
    async def handle_preview_table(self, name: str, delimiter: str = ",", max_rows: int = 50) -> Dict[str, Any]:
        """Preview CSV/TSV file as table"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        file_path = self.workdir / name
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "File path escapes working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {name} does not exist"
                }
            }
        
        try:
            rows = []
            header = None
            
            # Auto-detect delimiter for TSV
            if name.endswith('.tsv'):
                delimiter = '\t'
            
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                reader = csv.reader(f, delimiter=delimiter)
                for i, row in enumerate(reader):
                    if i == 0:
                        header = row
                    elif i <= max_rows:
                        rows.append(row)
                    else:
                        break
            
            return {
                "ok": True,
                "data": {
                    "header": header or [],
                    "rows": rows,
                    "row_count_returned": len(rows)
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "PREVIEW_ERROR",
                    "message": f"Failed to preview table: {str(e)}"
                }
            }
    
    async def handle_inspect_r_objects(self, objects: List[str] = None, str_max_level: int = 1, timeout_sec: int = 60) -> Dict[str, Any]:
        """Inspect R objects from last session"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        rdata_file = self.state_dir / "last_session.RData"
        if not rdata_file.exists():
            return {
                "ok": False,
                "error": {
                    "code": "NO_SESSION",
                    "message": "No saved R session found",
                    "hints": ["Run an R script with save_rdata=True first"]
                }
            }
        
        if objects is None:
            objects = []
        
        # Build R code to inspect objects
        r_code = f'load(".mcpr/last_session.RData", .GlobalEnv); '
        
        if not objects:
            # List all objects
            r_code += 'cat(ls(), sep="\\n")'
        else:
            # Inspect specified objects
            inspections = []
            for obj in objects:
                safe_obj = obj.replace('"', '\\"')
                inspections.append(f'if(exists("{safe_obj}")) {{ cat("\\n### {safe_obj} ###\\n"); str(get("{safe_obj}"), max.level={str_max_level}) }} else {{ cat("\\n### {safe_obj} ###\\n[Object not found]\\n") }}')
            r_code += '; '.join(inspections)
        
        result = self.run_r_command(["-e", r_code], timeout_sec)
        
        if result.get("ok"):
            stdout = result["stdout"]
            
            if not objects:
                # Parse listed objects
                listed = [line.strip() for line in stdout.split('\n') if line.strip()]
                return {
                    "ok": True,
                    "data": {
                        "listed": listed,
                        "inspected": {}
                    }
                }
            else:
                # Parse inspected objects
                inspected = {}
                current_obj = None
                current_lines = []
                
                for line in stdout.split('\n'):
                    if line.startswith('### ') and line.endswith(' ###'):
                        if current_obj:
                            inspected[current_obj] = '\n'.join(current_lines)
                        current_obj = line[4:-4]
                        current_lines = []
                    elif current_obj:
                        current_lines.append(line)
                
                if current_obj:
                    inspected[current_obj] = '\n'.join(current_lines)
                
                return {
                    "ok": True,
                    "data": {
                        "listed": [],
                        "inspected": inspected
                    }
                }
        else:
            return result
    
    async def handle_which_r(self) -> Dict[str, Any]:
        """Find R executable in PATH"""
        alternatives = []
        executable = None
        
        rscript = shutil.which("Rscript")
        if rscript:
            executable = rscript
            alternatives.append(rscript)
        
        r_exe = shutil.which("R")
        if r_exe:
            if not executable:
                executable = r_exe
            alternatives.append(r_exe)
        
        if executable:
            return {
                "ok": True,
                "data": {
                    "executable": executable,
                    "alternatives": alternatives
                }
            }
        else:
            return {
                "ok": False,
                "error": {
                    "code": "R_NOT_FOUND",
                    "message": "R not found in PATH",
                    "hints": ["Install R from https://www.r-project.org/", "Add Rscript or R to your system PATH"]
                }
            }
    
    async def handle_list_r_files(self) -> Dict[str, Any]:
        """List all R files in working directory"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        try:
            r_files = []
            for item in self.workdir.glob("*.r"):
                if item.is_file():
                    r_files.append(item.name)
            
            for item in self.workdir.glob("*.R"):
                if item.is_file() and item.name not in r_files:
                    r_files.append(item.name)
            
            r_files.sort()
            
            return {
                "ok": True,
                "data": {
                    "files": r_files,
                    "primary_file": self.primary_file
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "LIST_ERROR",
                    "message": f"Failed to list R files: {str(e)}"
                }
            }

async def main():
    """Main entry point"""
    logger.info("Starting mcpr MCP server...")
    
    # Create server instance
    server = Server("mcpr")
    mcpr = MCPRServer()
    
    # Register list_tools handler
    @server.list_tools()
    async def list_tools():
        logger.debug("Listing tools...")
        return [
            Tool(name="set_workdir", description="Set the working directory for all R operations", 
                 inputSchema={"type": "object", "properties": {"path": {"type": "string"}, "create": {"type": "boolean", "default": True}}, "required": ["path"]}),
            Tool(name="get_state", description="Get current mcpr state and configuration", 
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="create_r_file", description="Create a new R script file", 
                 inputSchema={"type": "object", "properties": {"filename": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}, "scaffold": {"type": "boolean", "default": True}}, "required": ["filename"]}),
            Tool(name="rename_r_file", description="Rename an R script file", 
                 inputSchema={"type": "object", "properties": {"old_name": {"type": "string"}, "new_name": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}}, "required": ["old_name", "new_name"]}),
            Tool(name="set_primary_file", description="Set the primary R script file", 
                 inputSchema={"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}),
            Tool(name="append_r_code", description="Append R code to an existing script file", 
                 inputSchema={"type": "object", "properties": {"code": {"type": "string"}, "filename": {"type": "string"}, "ensure_trailing_newline": {"type": "boolean", "default": True}}, "required": ["code"]}),
            Tool(name="write_r_code", description="Write R code to a script file", 
                 inputSchema={"type": "object", "properties": {"code": {"type": "string"}, "filename": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}, "use_scaffold_header": {"type": "boolean", "default": True}}, "required": ["code"]}),
            Tool(name="run_r_script", description="Execute an R script file", 
                 inputSchema={"type": "object", "properties": {"filename": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}, "timeout_sec": {"type": "integer", "default": 120}, "save_rdata": {"type": "boolean", "default": True}}}),
            Tool(name="run_r_expression", description="Execute a single R expression", 
                 inputSchema={"type": "object", "properties": {"expr": {"type": "string"}, "timeout_sec": {"type": "integer", "default": 60}}, "required": ["expr"]}),
            Tool(name="list_exports", description="List files in the working directory", 
                 inputSchema={"type": "object", "properties": {"glob": {"type": "string", "default": "*"}, "sort_by": {"type": "string", "default": "mtime"}, "descending": {"type": "boolean", "default": True}, "limit": {"type": "integer", "default": 200}}}),
            Tool(name="read_export", description="Read a file from the working directory", 
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "max_bytes": {"type": "integer", "default": 2000000}, "as_text": {"type": "boolean", "default": True}, "encoding": {"type": "string", "default": "utf-8"}}, "required": ["name"]}),
            Tool(name="preview_table", description="Preview a CSV/TSV file as a table", 
                 inputSchema={"type": "object", "properties": {"name": {"type": "string"}, "delimiter": {"type": "string", "default": ","}, "max_rows": {"type": "integer", "default": 50}}, "required": ["name"]}),
            Tool(name="inspect_r_objects", description="Inspect R objects from the last saved session", 
                 inputSchema={"type": "object", "properties": {"objects": {"type": "array", "items": {"type": "string"}}, "str_max_level": {"type": "integer", "default": 1}, "timeout_sec": {"type": "integer", "default": 60}}}),
            Tool(name="which_r", description="Find R executable in PATH", 
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="list_r_files", description="List all R script files in the working directory", 
                 inputSchema={"type": "object", "properties": {}})
        ]
    
    # Register call_tool handler
    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        logger.debug(f"Calling tool: {name} with arguments: {arguments}")
        try:
            if name == "set_workdir":
                result = await mcpr.handle_set_workdir(**arguments)
            elif name == "get_state":
                result = await mcpr.handle_get_state()
            elif name == "create_r_file":
                result = await mcpr.handle_create_r_file(**arguments)
            elif name == "rename_r_file":
                result = await mcpr.handle_rename_r_file(**arguments)
            elif name == "set_primary_file":
                result = await mcpr.handle_set_primary_file(**arguments)
            elif name == "append_r_code":
                result = await mcpr.handle_append_r_code(**arguments)
            elif name == "write_r_code":
                result = await mcpr.handle_write_r_code(**arguments)
            elif name == "run_r_script":
                result = await mcpr.handle_run_r_script(**arguments)
            elif name == "run_r_expression":
                result = await mcpr.handle_run_r_expression(**arguments)
            elif name == "list_exports":
                result = await mcpr.handle_list_exports(**arguments)
            elif name == "read_export":
                result = await mcpr.handle_read_export(**arguments)
            elif name == "preview_table":
                result = await mcpr.handle_preview_table(**arguments)
            elif name == "inspect_r_objects":
                result = await mcpr.handle_inspect_r_objects(**arguments)
            elif name == "which_r":
                result = await mcpr.handle_which_r()
            elif name == "list_r_files":
                result = await mcpr.handle_list_r_files()
            else:
                result = {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": f"Unknown tool: {name}"}}
            
            logger.debug(f"Tool {name} result: {result}")
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        except Exception as e:
            logger.error(f"Error in tool {name}: {str(e)}")
            logger.error(traceback.format_exc())
            error_result = {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": f"Internal error: {str(e)}"
                }
            }
            return [TextContent(type="text", text=json.dumps(error_result, indent=2))]
    
    # Run server with initialization_options parameter
    try:
        async with stdio_server() as (read_stream, write_stream):
            logger.info("Server running...")
            initialization_options = server.create_initialization_options()
            await server.run(read_stream, write_stream, initialization_options)
    except Exception as e:
        logger.error(f"Server error: {e}")
        logger.error(traceback.format_exc())
        raise

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

