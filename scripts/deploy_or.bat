@echo off
REM Full OR Upgrade - Pull latest code, build, and deploy

if "%1"=="" (
    echo Usage: deploy_or.bat [room_number]
    echo Example: deploy_or.bat 4
    exit /b 1
)

echo ========================================
echo Step 1: Pulling latest matrix-api code
echo ========================================
cd ..\matrix-api-linux
git pull
if errorlevel 1 (
    echo ERROR: Git pull failed
    exit /b 1
)

echo.
echo ========================================
echo Step 2: Building matrix-api dist
echo ========================================
call npm run build
if errorlevel 1 (
    echo ERROR: Build failed
    exit /b 1
)

echo.
echo ========================================
echo Step 3: Deploying to OR%1
echo ========================================
cd ..\or-refresh-automation
python upgrade_or.py --rooms %1
if errorlevel 1 (
    echo ERROR: Deployment failed
    exit /b 1
)

echo.
echo ========================================
echo SUCCESS! OR%1 upgraded
echo Test: https://YOUR_ROUTER_IP:100%1/app/ (see ROUTER_IP in .env)
echo ========================================
