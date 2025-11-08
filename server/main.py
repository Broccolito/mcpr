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

# R script scaffold template - now minimal, getting straight to business
R_SCAFFOLD = """# ---- Packages ----
library(ggplot2)

# ---- Functions ----

# ---- Main ----

"""

# ggplot Style Guide for reference
GGPLOT_STYLE_GUIDE = """
# ggplot Style Guide - One-Time Code Optimization

## Core Principles:
1. **Assignment**: Always use = instead of <- 
2. **Theme**: Use theme_minimal() or theme_classic() with base_size=14
3. **Colors**: Muted palettes (Set2 for categorical, viridis for continuous)
4. **Dimensions**: Optimize for 5x4 inches (width x height)
5. **Typography**: Base size ≥ 14pt for readability
6. **Visibility**: Points ≥ 2.5, lines ≥ 0.8 width
7. **Export**: Always save with dpi=800

## Color Palette Guidelines:
### Categorical Data:
- Set2, Set3, Pastel1, Pastel2, Dark2 (RColorBrewer)
- Avoid default ggplot2 colors

### Continuous Data:
- viridis, magma, plasma, inferno, cividis
- Colorblind-friendly by default

### Diverging Data:
- RdBu, RdYlBu, Spectral, PuOr, BrBG
- Center at meaningful value

## Code Optimization Example:
```r
# Good practice - optimized code
library(ggplot2)

# Use = for assignments
data = read.csv("data.csv")

# Build plot with optimal settings
p = ggplot(data, aes(x=x_var, y=y_var, color=group)) +
  geom_point(size=2.5, alpha=0.8) +
  geom_line(linewidth=0.8) +
  scale_color_brewer(palette="Set2") +  # Muted categorical colors
  theme_minimal(base_size=14) +
  labs(x="Clear X Label",
       y="Clear Y Label", 
       title="Concise Title") +
  theme(plot.margin=margin(10,10,10,10))

# Save with optimal dimensions and quality
ggsave("plot.png", p, width=5, height=4, dpi=800)
```

## Automatic Optimizations:
- Replace theme_gray() → theme_minimal(base_size=14)
- Convert <- to = throughout
- Add color scales if missing (no defaults)
- Optimize dimensions to 5x4 inches
- Ensure dpi=800 for all exports
- Humanize variable names in labels
"""

class MCPRServer:
    def __init__(self):
        self.state_dir = None
        self.state_file = None
        self.workdir = None
        self.primary_file = "agent.R"  # Changed from .r to .R
        
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
        """Scan directory for files with modification times"""
        files = {}
        try:
            for item in self.workdir.iterdir():
                if item.is_file():
                    files[item.name] = item.stat().st_mtime
        except Exception:
            pass
        return files
    
    async def handle_set_workdir(self, path: str, create: bool = True) -> Dict[str, Any]:
        """Set working directory"""
        try:
            workdir_path = Path(path).expanduser().resolve()
            
            if create and not workdir_path.exists():
                workdir_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created working directory: {workdir_path}")
            
            if not workdir_path.exists():
                return {
                    "ok": False,
                    "error": {
                        "code": "DIR_NOT_FOUND",
                        "message": f"Directory {path} does not exist",
                        "hints": ["Set create=true to create the directory", "Check the path is correct"]
                    }
                }
            
            if not workdir_path.is_dir():
                return {
                    "ok": False,
                    "error": {
                        "code": "NOT_A_DIRECTORY",
                        "message": f"Path {path} is not a directory"
                    }
                }
            
            # Set state directory and file
            self.workdir = workdir_path
            self.state_dir = workdir_path / ".mcpr_state"
            self.state_dir.mkdir(exist_ok=True)
            self.state_file = self.state_dir / "state.json"
            
            # Update state
            state = self.load_state()
            state['workdir'] = str(workdir_path)
            state['primary_file'] = self.primary_file
            state['updated_at'] = datetime.now().isoformat()
            self.save_state(state)
            
            logger.info(f"Working directory set to: {workdir_path}")
            
            return {
                "ok": True,
                "data": {
                    "workdir": str(workdir_path),
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
        state = self.load_state()
        
        # Add current runtime info
        state['workdir_set'] = self.workdir is not None
        if self.workdir:
            state['workdir'] = str(self.workdir)
            state['workdir_exists'] = self.workdir.exists()
        
        state['r_available'] = self.find_r_executable() is not None
        state['primary_file'] = self.primary_file
        
        return {
            "ok": True,
            "data": state
        }
    
    async def handle_create_r_file(self, filename: str, overwrite: bool = False, scaffold: bool = True) -> Dict[str, Any]:
        """Create a new R script file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        # Ensure .R extension (capitalized)
        if not filename.endswith('.R'):
            if filename.endswith('.r'):
                filename = filename[:-2] + '.R'
            else:
                filename = filename + '.R'
        
        file_path = self.workdir / filename
        
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": f"File path {filename} is outside working directory"
                }
            }
        
        if file_path.exists() and not overwrite:
            return {
                "ok": False,
                "error": {
                    "code": "FILE_EXISTS",
                    "message": f"File {filename} already exists",
                    "hints": ["Set overwrite=true to replace the file", "Use a different filename"]
                }
            }
        
        try:
            content = R_SCAFFOLD if scaffold else ""
            file_path.write_text(content)
            
            # Update state
            state = self.load_state()
            if 'r_files' not in state:
                state['r_files'] = []
            if filename not in state['r_files']:
                state['r_files'].append(filename)
            state['updated_at'] = datetime.now().isoformat()
            self.save_state(state)
            
            return {
                "ok": True,
                "data": {
                    "filename": filename,
                    "path": str(file_path),
                    "scaffold": scaffold
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
        """Rename an R script file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        # Ensure .R extension for new name
        if not new_name.endswith('.R'):
            if new_name.endswith('.r'):
                new_name = new_name[:-2] + '.R'
            else:
                new_name = new_name + '.R'
        
        old_path = self.workdir / old_name
        new_path = self.workdir / new_name
        
        if not self.is_safe_path(old_path) or not self.is_safe_path(new_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": "Path is outside working directory"
                }
            }
        
        if not old_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {old_name} not found"
                }
            }
        
        if new_path.exists() and not overwrite:
            return {
                "ok": False,
                "error": {
                    "code": "FILE_EXISTS",
                    "message": f"File {new_name} already exists",
                    "hints": ["Set overwrite=true to replace the file", "Use a different filename"]
                }
            }
        
        try:
            if new_path.exists():
                new_path.unlink()
            old_path.rename(new_path)
            
            # Update state
            state = self.load_state()
            if 'r_files' in state:
                if old_name in state['r_files']:
                    state['r_files'].remove(old_name)
                if new_name not in state['r_files']:
                    state['r_files'].append(new_name)
            if self.primary_file == old_name:
                self.primary_file = new_name
                state['primary_file'] = new_name
            state['updated_at'] = datetime.now().isoformat()
            self.save_state(state)
            
            return {
                "ok": True,
                "data": {
                    "old_name": old_name,
                    "new_name": new_name
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
        """Set primary R script file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        # Ensure .R extension
        if not filename.endswith('.R'):
            if filename.endswith('.r'):
                filename = filename[:-2] + '.R'
            else:
                filename = filename + '.R'
        
        file_path = self.workdir / filename
        
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": f"File path {filename} is outside working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {filename} not found",
                    "hints": ["Create the file first with create_r_file", "Check the filename is correct"]
                }
            }
        
        self.primary_file = filename
        
        # Update state
        state = self.load_state()
        state['primary_file'] = filename
        state['updated_at'] = datetime.now().isoformat()
        self.save_state(state)
        
        return {
            "ok": True,
            "data": {
                "primary_file": filename
            }
        }
    
    async def handle_append_r_code(self, code: str, filename: Optional[str] = None, ensure_trailing_newline: bool = True) -> Dict[str, Any]:
        """Append R code to file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if filename is None:
            filename = self.primary_file
        
        # Ensure .R extension
        if not filename.endswith('.R'):
            if filename.endswith('.r'):
                filename = filename[:-2] + '.R'
            else:
                filename = filename + '.R'
        
        file_path = self.workdir / filename
        
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": f"File path {filename} is outside working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {filename} not found",
                    "hints": ["Create the file first with create_r_file", "Check the filename is correct"]
                }
            }
        
        try:
            # Ensure code uses = instead of <- for assignments
            if "<-" in code:
                code = code.replace("<-", "=")
            
            existing_content = file_path.read_text()
            
            # Ensure proper spacing
            if existing_content and not existing_content.endswith('\n'):
                existing_content += '\n'
            
            # Ensure code ends with newline if requested
            if ensure_trailing_newline and not code.endswith('\n'):
                code += '\n'
            
            file_path.write_text(existing_content + code)
            
            return {
                "ok": True,
                "data": {
                    "filename": filename,
                    "code_length": len(code),
                    "file_size": file_path.stat().st_size,
                    "assignment_style": "using = instead of <-"
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "APPEND_ERROR",
                    "message": f"Failed to append code: {str(e)}"
                }
            }
    
    async def handle_write_r_code(self, code: str, filename: Optional[str] = None, overwrite: bool = False, use_scaffold_header: bool = True) -> Dict[str, Any]:
        """Write R code to file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if filename is None:
            filename = self.primary_file
        
        # Ensure .R extension
        if not filename.endswith('.R'):
            if filename.endswith('.r'):
                filename = filename[:-2] + '.R'
            else:
                filename = filename + '.R'
        
        file_path = self.workdir / filename
        
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": f"File path {filename} is outside working directory"
                }
            }
        
        if file_path.exists() and not overwrite:
            return {
                "ok": False,
                "error": {
                    "code": "FILE_EXISTS",
                    "message": f"File {filename} already exists",
                    "hints": ["Set overwrite=true to replace the file", "Use append_r_code to add to existing file"]
                }
            }
        
        try:
            # Ensure code uses = instead of <- for assignments
            if "<-" in code:
                code = code.replace("<-", "=")
            
            # Prepare content
            if use_scaffold_header and not code.startswith("#"):
                content = R_SCAFFOLD + "\n" + code
            else:
                content = code
            
            file_path.write_text(content)
            
            # Update state
            state = self.load_state()
            if 'r_files' not in state:
                state['r_files'] = []
            if filename not in state['r_files']:
                state['r_files'].append(filename)
            state['updated_at'] = datetime.now().isoformat()
            self.save_state(state)
            
            return {
                "ok": True,
                "data": {
                    "filename": filename,
                    "overwrite": overwrite,
                    "scaffold_used": use_scaffold_header,
                    "assignment_style": "using = instead of <-"
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "WRITE_ERROR",
                    "message": f"Failed to write code: {str(e)}"
                }
            }
    
    async def handle_run_r_script(self, filename: Optional[str] = None, args: Optional[List[str]] = None, timeout_sec: int = 120, save_rdata: bool = True) -> Dict[str, Any]:
        """Execute R script file"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if filename is None:
            filename = self.primary_file
        
        # Ensure .R extension
        if not filename.endswith('.R'):
            if filename.endswith('.r'):
                filename = filename[:-2] + '.R'
            else:
                filename = filename + '.R'
        
        file_path = self.workdir / filename
        
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": f"File path {filename} is outside working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"Script file {filename} not found",
                    "hints": ["Check the filename is correct", "Create the file first with create_r_file"]
                }
            }
        
        # Build R command
        rscript_args = []
        if save_rdata:
            rscript_args = ["-e", f"source('{filename}'); save.image('.RData')"]
        else:
            rscript_args = [filename]
        
        if args:
            rscript_args.extend(args)
        
        # Get files before execution
        files_before = self.scan_directory_files()
        
        # Execute
        result = self.run_r_command(rscript_args, timeout_sec)
        
        # Get files after execution
        files_after = self.scan_directory_files()
        
        # Find new/modified files
        new_files = []
        modified_files = []
        for name, mtime in files_after.items():
            if name not in files_before:
                new_files.append(name)
            elif mtime > files_before[name]:
                modified_files.append(name)
        
        if result['ok']:
            result['data'] = {
                'script': filename,
                'new_files': new_files,
                'modified_files': modified_files,
                'rdata_saved': save_rdata and '.RData' in (new_files + modified_files)
            }
        
        return result
    
    async def handle_run_r_expression(self, expr: str, timeout_sec: int = 60) -> Dict[str, Any]:
        """Execute single R expression"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        # Execute expression
        result = self.run_r_command(["-e", expr], timeout_sec)
        
        if result['ok']:
            result['data'] = {'expression': expr[:100] + '...' if len(expr) > 100 else expr}
        
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
                    stat = item.stat()
                    files.append({
                        "name": item.name,
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "is_r_file": item.suffix.lower() == '.r'
                    })
            
            # Sort
            if sort_by == "name":
                files.sort(key=lambda x: x["name"], reverse=descending)
            elif sort_by == "size":
                files.sort(key=lambda x: x["size"], reverse=descending)
            else:  # mtime
                files.sort(key=lambda x: x["mtime"], reverse=descending)
            
            # Limit
            files = files[:limit]
            
            return {
                "ok": True,
                "data": {
                    "files": files,
                    "total": len(files)
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
        """Read file from working directory"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        file_path = self.workdir / name
        
        if not self.is_safe_path(file_path):
            return {
                "ok": False,
                "error": {
                    "code": "UNSAFE_PATH",
                    "message": f"File path {name} is outside working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {name} not found"
                }
            }
        
        if not file_path.is_file():
            return {
                "ok": False,
                "error": {
                    "code": "NOT_A_FILE",
                    "message": f"{name} is not a file"
                }
            }
        
        try:
            size = file_path.stat().st_size
            
            if size > max_bytes:
                return {
                    "ok": False,
                    "error": {
                        "code": "FILE_TOO_LARGE",
                        "message": f"File size {size} exceeds max_bytes {max_bytes}",
                        "hints": ["Increase max_bytes parameter", "Use preview_table for CSV files"]
                    }
                }
            
            if as_text:
                content = file_path.read_text(encoding=encoding)
                return {
                    "ok": True,
                    "data": {
                        "name": name,
                        "content": content,
                        "size": size
                    }
                }
            else:
                content = file_path.read_bytes()
                content_b64 = base64.b64encode(content).decode('ascii')
                return {
                    "ok": True,
                    "data": {
                        "name": name,
                        "content_base64": content_b64,
                        "size": size
                    }
                }
        except UnicodeDecodeError as e:
            return {
                "ok": False,
                "error": {
                    "code": "DECODE_ERROR",
                    "message": f"Failed to decode file as {encoding}",
                    "hints": ["Try as_text=false for binary files", "Use a different encoding"]
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
                    "message": f"File path {name} is outside working directory"
                }
            }
        
        if not file_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "FILE_NOT_FOUND",
                    "message": f"File {name} not found"
                }
            }
        
        try:
            rows = []
            total_rows = 0
            
            with open(file_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=delimiter)
                for i, row in enumerate(reader):
                    if i <= max_rows:  # Include header + max_rows
                        rows.append(row)
                    total_rows += 1
            
            if not rows:
                return {
                    "ok": True,
                    "data": {
                        "name": name,
                        "rows": [],
                        "total_rows": 0,
                        "columns": []
                    }
                }
            
            # Extract header and data
            header = rows[0] if rows else []
            data = rows[1:max_rows+1] if len(rows) > 1 else []
            
            return {
                "ok": True,
                "data": {
                    "name": name,
                    "columns": header,
                    "rows": data,
                    "total_rows": total_rows,
                    "truncated": total_rows > max_rows + 1
                }
            }
        except Exception as e:
            return {
                "ok": False,
                "error": {
                    "code": "PREVIEW_ERROR",
                    "message": f"Failed to preview table: {str(e)}",
                    "hints": ["Check the delimiter is correct", "Ensure file is a valid CSV/TSV"]
                }
            }
    

    async def handle_ggplot_style_check(self, code: str) -> Dict[str, Any]:
        """Check and optimize ggplot code against publication-quality style guide"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        if not code:
            return {
                "ok": False,
                "error": {
                    "code": "NO_CODE",
                    "message": "No code provided to check",
                    "hints": ["Provide ggplot code to analyze and optimize"]
                }
            }
        
        import re
        
        feedback = []
        optimized_code = code
        
        # 1. Convert all <- to = for assignments
        if "<-" in optimized_code:
            optimized_code = optimized_code.replace("<-", "=")
            feedback.append("✅ Converted <- to = for all variable assignments")
        
        # 2. Check and optimize theme
        if "theme_gray()" in optimized_code or "theme_grey()" in optimized_code:
            optimized_code = re.sub(r'theme_gr[ae]y\([^)]*\)', 'theme_minimal(base_size=14)', optimized_code)
            feedback.append("✅ Replaced theme_gray() with theme_minimal(base_size=14)")
        elif "theme_minimal(" not in optimized_code and "theme_classic(" not in optimized_code:
            # Add theme if missing
            if "+ theme(" in optimized_code:
                optimized_code = optimized_code.replace("+ theme(", "+ theme_minimal(base_size=14) + theme(")
            else:
                # Add before ggsave if present, otherwise at the end
                if "ggsave(" in optimized_code:
                    optimized_code = optimized_code.replace("ggsave(", "+ theme_minimal(base_size=14)\nggsave(")
                else:
                    optimized_code += "\n  + theme_minimal(base_size=14)"
            feedback.append("✅ Added theme_minimal(base_size=14) for clean appearance")
        
        # 3. Ensure proper base_size
        if "base_size" in optimized_code:
            size_matches = re.findall(r'base_size\s*=\s*(\d+)', optimized_code)
            for match in size_matches:
                if int(match) < 14:
                    optimized_code = re.sub(f'base_size\\s*=\\s*{match}', 'base_size=14', optimized_code)
                    feedback.append(f"✅ Increased base_size from {match} to 14")
        
        # 4. Check and add color palettes
        has_color_scale = any(x in optimized_code for x in ["scale_color", "scale_fill", "scale_colour"])
        
        if not has_color_scale and any(x in optimized_code for x in ["aes(", "color=", "fill=", "colour="]):
            # Detect if categorical or continuous
            if any(x in optimized_code for x in ["factor(", "as.factor(", 'color="', 'fill="']):
                # Categorical data - use muted palette
                if "scale_color" in optimized_code or "colour=" in optimized_code:
                    optimized_code += "\n  + scale_color_brewer(palette='Set2')"
                    feedback.append("✅ Added muted categorical color palette (Set2)")
                if "fill=" in optimized_code:
                    optimized_code += "\n  + scale_fill_brewer(palette='Set2')"
                    feedback.append("✅ Added muted categorical fill palette (Set2)")
            else:
                # Continuous data - use viridis
                if "color=" in optimized_code or "colour=" in optimized_code:
                    optimized_code += "\n  + scale_color_viridis_c(option='viridis')"
                    feedback.append("✅ Added viridis continuous color scale")
                if "fill=" in optimized_code:
                    optimized_code += "\n  + scale_fill_viridis_c(option='viridis')"
                    feedback.append("✅ Added viridis continuous fill scale")
        elif has_color_scale:
            # Replace default color scales with better ones
            if "scale_color_discrete()" in optimized_code or "scale_colour_discrete()" in optimized_code:
                optimized_code = re.sub(r'scale_colou?r_discrete\(\)', "scale_color_brewer(palette='Set2')", optimized_code)
                feedback.append("✅ Replaced default discrete colors with Set2 palette")
            if "scale_fill_discrete()" in optimized_code:
                optimized_code = optimized_code.replace("scale_fill_discrete()", "scale_fill_brewer(palette='Set2')")
                feedback.append("✅ Replaced default discrete fill with Set2 palette")
            if "scale_color_continuous()" in optimized_code or "scale_colour_continuous()" in optimized_code:
                optimized_code = re.sub(r'scale_colou?r_continuous\(\)', "scale_color_viridis_c()", optimized_code)
                feedback.append("✅ Replaced default continuous colors with viridis")
            if "scale_fill_continuous()" in optimized_code:
                optimized_code = optimized_code.replace("scale_fill_continuous()", "scale_fill_viridis_c()")
                feedback.append("✅ Replaced default continuous fill with viridis")
        
        # 5. Optimize geom sizes
        if "geom_point(" in optimized_code:
            # Check if size is specified
            point_matches = re.findall(r'geom_point\([^)]*\)', optimized_code)
            for match in point_matches:
                if "size" not in match:
                    new_match = match.replace("geom_point()", "geom_point(size=2.5, alpha=0.8)")
                    if match == "geom_point()":
                        optimized_code = optimized_code.replace(match, new_match)
                    else:
                        # Insert size parameter
                        new_match = match[:-1] + ", size=2.5, alpha=0.8)"
                        optimized_code = optimized_code.replace(match, new_match)
                    feedback.append("✅ Added size=2.5 and alpha=0.8 to geom_point")
        
        if "geom_line(" in optimized_code:
            line_matches = re.findall(r'geom_line\([^)]*\)', optimized_code)
            for match in line_matches:
                if "linewidth" not in match and "size" not in match:
                    if match == "geom_line()":
                        optimized_code = optimized_code.replace(match, "geom_line(linewidth=0.8)")
                    else:
                        new_match = match[:-1] + ", linewidth=0.8)"
                        optimized_code = optimized_code.replace(match, new_match)
                    feedback.append("✅ Added linewidth=0.8 to geom_line")
        
        # 6. Check for text/label overlaps and suggest ggrepel
        if "geom_text(" in optimized_code and "geom_text_repel" not in optimized_code:
            feedback.append("⚠️ Consider using ggrepel::geom_text_repel() to avoid label overlaps")
        
        # 7. Humanize variable names in labels
        variable_fixes = {
            "Sepal.Length": "Sepal Length",
            "Sepal.Width": "Sepal Width", 
            "Petal.Length": "Petal Length",
            "Petal.Width": "Petal Width",
            ".": " "  # General dot replacement in quoted strings
        }
        
        for old, new in variable_fixes.items():
            if old in optimized_code:
                # Only replace in quoted strings (labels)
                optimized_code = re.sub(f'"{old}"', f'"{new}"', optimized_code)
                optimized_code = re.sub(f"'{old}'", f"'{new}'", optimized_code)
                if old != ".":
                    feedback.append(f"✅ Humanized label: {old} → {new}")
        
        # 8. Optimize ggsave dimensions
        if "ggsave(" in optimized_code:
            ggsave_matches = re.findall(r'ggsave\([^)]+\)', optimized_code)
            for match in ggsave_matches:
                new_match = match
                
                # Check and adjust width
                width_match = re.search(r'width\s*=\s*([\d.]+)', match)
                if width_match and float(width_match.group(1)) > 6:
                    new_match = re.sub(r'width\s*=\s*[\d.]+', 'width=5', new_match)
                    feedback.append(f"✅ Reduced width from {width_match.group(1)} to 5 inches")
                elif "width" not in match:
                    new_match = new_match[:-1] + ", width=5, height=4)"
                    feedback.append("✅ Added optimal dimensions: width=5, height=4")
                
                # Check and adjust height
                height_match = re.search(r'height\s*=\s*([\d.]+)', match)
                if height_match and float(height_match.group(1)) > 4.5:
                    new_match = re.sub(r'height\s*=\s*[\d.]+', 'height=4', new_match)
                    feedback.append(f"✅ Reduced height from {height_match.group(1)} to 4 inches")
                
                # Ensure dpi=800
                if "dpi" not in match:
                    new_match = new_match[:-1] + ", dpi=800)"
                    feedback.append("✅ Added dpi=800 for publication quality")
                else:
                    dpi_match = re.search(r'dpi\s*=\s*(\d+)', match)
                    if dpi_match and int(dpi_match.group(1)) < 800:
                        new_match = re.sub(r'dpi\s*=\s*\d+', 'dpi=800', new_match)
                        feedback.append(f"✅ Increased dpi from {dpi_match.group(1)} to 800")
                
                optimized_code = optimized_code.replace(match, new_match)
        else:
            # Add ggsave recommendation
            feedback.append("💡 Add ggsave('plot.png', p, width=5, height=4, dpi=800) to save")
        
        # 9. Add margin adjustments if not present
        if "plot.margin" not in optimized_code and "theme(" in optimized_code:
            optimized_code = optimized_code.replace("theme(", "theme(plot.margin=margin(10,10,10,10), ")
            feedback.append("✅ Added plot margins for better spacing")
        
        # Color palette recommendations
        color_recommendations = {
            "categorical": ["Set2", "Set3", "Pastel1", "Pastel2", "Dark2"],
            "continuous": ["viridis", "magma", "plasma", "inferno", "cividis"],
            "diverging": ["RdBu", "RdYlBu", "Spectral", "PuOr", "BrBG"]
        }
        
        return {
            "ok": True,
            "data": {
                "optimized_code": optimized_code if optimized_code != code else None,
                "feedback": feedback if feedback else ["Code already follows style guidelines"],
                "improvements_made": len(feedback),
                "style_summary": {
                    "assignment": "Use = instead of <-",
                    "theme": "theme_minimal() or theme_classic() with base_size=14",
                    "dimensions": "width=5, height=4 for optimal readability",
                    "colors": "Set2 for categorical, viridis for continuous",
                    "typography": "base_size=14, automatic scaling for titles",
                    "export": "Always use dpi=800"
                },
                "color_palettes": color_recommendations,
                "final_checklist": [
                    "✓ Assignment operators: = not <-",
                    "✓ Theme: minimal or classic, not gray",
                    "✓ Colors: muted palettes, not defaults", 
                    "✓ Dimensions: 5x4 inches optimal",
                    "✓ Font size: base_size ≥ 14",
                    "✓ Export: dpi=800 for publication"
                ]
            }
        }
    
    async def handle_inspect_r_objects(self, objects: Optional[List[str]] = None, str_max_level: int = 1, timeout_sec: int = 60) -> Dict[str, Any]:
        """Inspect R objects from saved session"""
        ok, error = self.ensure_workdir_set()
        if not ok:
            return {"ok": False, "error": error}
        
        rdata_path = self.workdir / ".RData"
        if not rdata_path.exists():
            return {
                "ok": False,
                "error": {
                    "code": "NO_RDATA",
                    "message": "No .RData file found",
                    "hints": ["Run an R script with save_rdata=true first", "Check if .RData was created in the working directory"]
                }
            }
        
        # Build R expression
        if objects:
            obj_list = ', '.join(f'"{obj}"' for obj in objects)
            expr = f"""
            load('.RData')
            objs = list({obj_list})
            for(name in objs){{
                if(exists(name)){{
                    cat('\\n==', name, '==\\n')
                    str(get(name), max.level={str_max_level})
                }}else{{
                    cat('\\n==', name, '== [NOT FOUND]\\n')
                }}
            }}
            """
        else:
            expr = f"""
            load('.RData')
            cat('\\nObjects in workspace:\\n')
            print(ls())
            cat('\\n\\nStructure of objects:\\n')
            for(name in ls()){{
                cat('\\n==', name, '==\\n')
                str(get(name), max.level={str_max_level})
            }}
            """
        
        result = self.run_r_command(["-e", expr], timeout_sec)
        
        if result['ok']:
            result['data'] = {
                'objects_requested': objects,
                'str_max_level': str_max_level
            }
        
        return result
    
    async def handle_which_r(self) -> Dict[str, Any]:
        """Find R executable"""
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
            # Look for both .R and .r extensions
            for pattern in ["*.R", "*.r"]:
                for item in self.workdir.glob(pattern):
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
            Tool(name="ggplot_style_check", description="Analyze and optimize ggplot code for publication-quality styling", 
                 inputSchema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}),
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
            elif name == "ggplot_style_check":
                result = await mcpr.handle_ggplot_style_check(**arguments)
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

