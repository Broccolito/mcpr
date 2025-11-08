# mcpr <img src="logo.png" align="right" alt="MCPR Logo" width="100"/>

MCP-compliant R code execution and management agent for Claude Desktop Extension.

<img src="assets/poster.png" alt="MCPR" width="300" style="display:inline;">

Empower R programming with Model Context Protocol.

## Overview

**mcpr** is an MCP (Model Context Protocol) server that allows Claude to generate, execute, and manage R scripts within a user-specified directory. It provides a complete workflow for statistical programming and data analysis through natural language interaction.

## Features

- **Execute R code and scripts** directly from Claude
- **Manage R script files** including create, rename, append, and write operations
- **Set and manage working directories** for analysis projects
- **Inspect R objects** and session state
- **Read and preview output files** in formats like CSV, TSV, and RDS
- **List and manage exported files** from your analysis
- **Automatic workspace saving** between sessions
- **Safe path operations** sandboxed within designated working directories

## Prerequisites

### 1. Install R

If R is not already installed on your system:

1. Visit [https://www.r-project.org/](https://www.r-project.org/)
2. Click on "Download R" and select a CRAN mirror
3. Download the installer for macOS
4. Run the installer and follow the installation prompts
5. Verify installation by opening Terminal and running:
   ```bash
   Rscript --version
   ```

### 2. Install Homebrew

[Homebrew](https://brew.sh/) is required to install uv. Check if Homebrew is already installed:

```bash
brew --version
```

If not installed, run:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the post-installation instructions to add Homebrew to your PATH.

### 3. Install uv

[uv](https://docs.astral.sh/uv/getting-started/installation/) is a fast Python package installer and runner. Install it using Homebrew:

```bash
brew install uv
```

Verify installation:

```bash
uv --version
```

## Installation

### Step 1: Download the Extension File

Download the `mcpr.dxt` file from this repository.

### Step 2: Install in Claude Desktop

1. Locate the downloaded `mcpr.dxt` file
2. Double-click the file
3. Claude Desktop will open and prompt you to install the extension
4. Confirm the installation

### Step 3: Configure Claude Desktop Settings

To avoid permission prompts for every tool execution:

1. Open Claude Desktop
2. Go to Settings (Cmd+,)
3. Navigate to the Extensions or MCP Servers section
4. Find "mcpr" in the list of installed extensions
5. Enable "Allow tools to run without permission" or "Auto-approve tool execution"
6. Restart Claude Desktop for changes to take effect

## Usage

Once installed, you can interact with mcpr through natural language in Claude Desktop. Here are some example interactions:

> **Setting up a workspace**
> 
> "Set up a working directory at ~/my_analysis"

> **Creating scripts**
> 
> "Create an R script that loads and summarizes my data"

> **Running analysis**
> 
> "Run the analysis script"

> **Viewing outputs**
> 
> "Show me the output files"

> **Reading results**
> 
> "Read the results.csv file"

> **Inspecting objects**
> 
> "Inspect the data objects from the last run"

## Technical Details

- **Python Version:** Requires Python 3.10 or higher
- **Protocol:** Uses the MCP (Model Context Protocol) 1.0+
- **R Execution:** R scripts are executed using Rscript
- **Session Persistence:** Session state is preserved in `.mcpr` directory within the working directory
- **Security:** All file operations are sandboxed to the specified working directory

## Acknowledgments

Special thanks to:

- **Beniamin Krupkin**
- **Sara Smith**
- **Siddharth Mahesh**

for their contributions to this project.

## Maintainer

**Wanjun Gu**  
Email: [wanjun.gu@ucsf.edu](mailto:wanjun.gu@ucsf.edu)

## License

MIT