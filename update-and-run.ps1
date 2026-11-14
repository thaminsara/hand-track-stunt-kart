<#
.SYNOPSIS
    Updates the repository from git, sets up the Python virtual environment, installs dependencies, and launches the game.
#>

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "     HAND-TRACK STUNT KART - UPDATE & RUN         " -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Cyan

# 1. Pull latest from git
Write-Host "[1/4] Pulling latest changes from GitHub..." -ForegroundColor Green
try {
    git pull origin main
} catch {
    Write-Host "Git pull skipped or failed (offline / local only). Continuing..." -ForegroundColor Yellow
}

# 2. Virtual environment setup
Write-Host "[2/4] Verifying Python virtual environment..." -ForegroundColor Green
if (!(Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
}

# 3. Activate and install requirements
Write-Host "[3/4] Activating venv and checking dependencies..." -ForegroundColor Green
$venvActivate = ".\.venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
} else {
    Write-Host "Warning: Venv activation script not found, using system python..." -ForegroundColor Yellow
}

pip install --upgrade pip --quiet
if (Test-Path "requirements.txt") {
    pip install -r requirements.txt
}

# 4. Launch game
Write-Host "[4/4] Launching Hand-Track Stunt Kart!" -ForegroundColor Cyan
python main.py
