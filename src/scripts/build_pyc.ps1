<#
.SYNOPSIS
    Byte-compiles the Reyvo Python sources into .pyc files in a build folder.

.DESCRIPTION
    Wraps py_compile so the compiled output lands in a separate directory
    (build\pyc by default) instead of the __pycache__ folders inside the
    source tree. Safe to run repeatedly: stale output is replaced, the
    sources are never modified.

    Virtual environments, caches and previous build output are skipped.

.PARAMETER Source
    Directory to compile, relative to the project root (src\) or absolute.
    Defaults to the project root, so reyvo\, alembic\, tests\ and scripts\
    are all compiled.

.PARAMETER Destination
    Output directory, relative to the project root or absolute.
    Defaults to build\pyc. Removed first when -Clean is passed.

.PARAMETER Optimize
    0 (default) keeps asserts and docstrings, 1 strips asserts, 2 strips
    asserts and docstrings. Output is named module.cpython-3xx.opt-N.pyc.

.PARAMETER Python
    Interpreter to compile with. Defaults to src\.venv\Scripts\python.exe,
    then python.exe on PATH.

.PARAMETER Clean
    Delete the destination directory before compiling.

.EXAMPLE
    .\scripts\build_pyc.ps1

.EXAMPLE
    .\scripts\build_pyc.ps1 -Clean -Optimize 1

.EXAMPLE
    .\scripts\build_pyc.ps1 -Source reyvo -Destination build\pyc-app
#>
[CmdletBinding()]
param(
    [string]$Source = ".",

    [string]$Destination = "build\pyc",

    [ValidateSet(0, 1, 2)]
    [int]$Optimize = 0,

    [string]$Python,

    [switch]$Clean
)

$ErrorActionPreference = "Stop"

# src\scripts -> project root is src\
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProjectRoot) {
    throw "Unable to determine the project root from '$PSScriptRoot'."
}

function Resolve-ProjectPath {
    param([string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
}

$SourceDir = Resolve-ProjectPath $Source
$DestinationDir = Resolve-ProjectPath $Destination

if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
    throw "Source directory not found: $SourceDir"
}

# Never let a stray -Destination wipe the sources.
if ($DestinationDir.TrimEnd('\', '/') -eq $ProjectRoot.TrimEnd('\', '/') -or
    $SourceDir.StartsWith($DestinationDir.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "Destination '$DestinationDir' would overwrite the sources in '$SourceDir'. Choose another folder."
}

if (-not $Python) {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot "venv\Scripts\python.exe")
    )

    $Python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    if (-not $Python) {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command) {
            $Python = $command.Source
        }
    }
}

if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw "No Python interpreter found. Pass -Python <path to python.exe>."
}

if ($Clean -and (Test-Path -LiteralPath $DestinationDir)) {
    Write-Host "Cleaning $DestinationDir"
    Remove-Item -LiteralPath $DestinationDir -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null

$compiler = @'
import os
import py_compile
import sys
import time

source = os.environ["PY_COMPILE_SRC"]
destination = os.environ["PY_COMPILE_OUT"]
optimize = int(os.environ["PY_COMPILE_OPT"])

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    "site-packages",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
}

tag = sys.implementation.cache_tag
suffix = ".pyc" if optimize == 0 else ".opt-%d.pyc" % optimize

sources = []
for dirpath, dirnames, filenames in os.walk(source):
    dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIP_DIRS)
    for filename in sorted(filenames):
        if filename.endswith(".py"):
            sources.append(os.path.join(dirpath, filename))

if not sources:
    print("error: no .py files found under %s" % source, file=sys.stderr)
    sys.exit(1)

failures = []
started = time.perf_counter()

for path in sources:
    relative = os.path.relpath(path, source)
    stem = os.path.splitext(os.path.basename(path))[0]
    compiled_name = "%s.%s%s" % (stem, tag, suffix)
    target = os.path.join(destination, os.path.dirname(relative), compiled_name)

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        py_compile.compile(path, cfile=target, optimize=optimize, doraise=True)
    except (py_compile.PyCompileError, OSError) as exc:
        failures.append((relative, str(exc).strip()))

elapsed = time.perf_counter() - started
compiled = len(sources) - len(failures)

print("Python %s (%s)" % (sys.version.split()[0], sys.implementation.cache_tag))
print("Compiled %d/%d file(s) in %.2fs" % (compiled, len(sources), elapsed))
print("Output: %s" % destination)

if failures:
    sys.stdout.flush()
    print("", file=sys.stderr)
    print("%d file(s) failed to compile:" % len(failures), file=sys.stderr)
    for relative, message in failures:
        print("  %s: %s" % (relative, message), file=sys.stderr)
    sys.exit(1)
'@

$env:PY_COMPILE_SRC = $SourceDir
$env:PY_COMPILE_OUT = $DestinationDir
$env:PY_COMPILE_OPT = $Optimize

try {
    Write-Host "Compiling $SourceDir (optimize=$Optimize)"
    $compiler | & $Python -
    $exitCode = $LASTEXITCODE
}
finally {
    Remove-Item Env:\PY_COMPILE_SRC, Env:\PY_COMPILE_OUT, Env:\PY_COMPILE_OPT -ErrorAction SilentlyContinue
}

exit $exitCode
