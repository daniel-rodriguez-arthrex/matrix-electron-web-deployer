# Lessons Learned - OR Deployment

## The Problem We Solved

### Initial Issue
- JavaScript syntax errors on all ORs after deployment
- Error: `Uncaught SyntaxError: Unexpected token ')'`
- Web app wouldn't load properly

### Root Cause
**Version mismatch between web assets and backend:**
- Web assets: v1.4.0 (from `.asar` file)
- Backend dist: v2.0.0 (accidentally deployed during testing)

## Key Learnings

### 1. Architecture Understanding
**The `.asar` file does NOT contain the backend dist folder**
- `.asar` = Electron app package (web assets + Electron wrapper only)
- Backend dist = Separate Node.js service at `/usr/lib/node_modules/matrix.api/dist`
- These are **two separate components** that must match versions

### 2. Deployment Components
```
┌─────────────────────────────────────────┐
│ OR Deployment has 2 parts:              │
├─────────────────────────────────────────┤
│ 1. Web Assets (from .asar)              │
│    Location: /opt/matrix-api-app/       │
│    Source: matrix-app.asar               │
│                                          │
│ 2. Backend API (from dist build)        │
│    Location: /usr/lib/.../matrix.api/dist│
│    Source: matrix-api-linux/dist         │
└─────────────────────────────────────────┘
```

### 3. Version Compatibility
**Web assets and backend dist MUST be from the same version:**
- v1.4.0 web + v1.4.0 dist = ✅ Works
- v1.4.0 web + v2.0.0 dist = ❌ JavaScript errors
- v2.0.0 web + v2.0.0 dist = ✅ Works

### 4. Correct Deployment Workflow
```
1. Pull latest matrix-api code
   └─> git pull

2. Build the dist folder
   └─> npm run build

3. For EACH OR:
   a. Download that OR's .asar file
   b. Extract web assets locally
   c. Upload web assets to /opt/matrix-api-app/
   d. Upload built dist to /usr/lib/.../matrix.api/dist
   e. Fix permissions (755)
   f. Update systemd to use dist/server.js
   g. Restart matrix-api service
```

### 5. Why Per-Room .asar?
Each OR should use its own `.asar` file because:
- Ensures web assets match what's currently deployed
- Prevents version drift between rooms
- Each room can have slightly different configs embedded in the .asar

### 6. Common Mistakes to Avoid
❌ **Don't:** Deploy v2.0.0 dist with v1.4.0 web assets
❌ **Don't:** Assume .asar contains the backend dist
❌ **Don't:** Use a "golden image" .asar for all rooms
❌ **Don't:** Skip permission fixes (755 required)
❌ **Don't:** Forget to update systemd to use dist/server.js

✅ **Do:** Pull latest code before building
✅ **Do:** Build dist locally before deploying
✅ **Do:** Use each OR's own .asar file
✅ **Do:** Deploy matching versions of web + dist
✅ **Do:** Verify service is running after deployment

## The Solution

### Automated Script: `upgrade_or.py`
- Downloads .asar from each OR individually
- Extracts web assets
- Deploys both web assets AND dist folder
- Fixes all permissions
- Restarts service
- Verifies deployment

### One-Command Deployment: `deploy_or.bat`
```powershell
deploy_or.bat 4
```
This automatically:
1. Pulls latest code
2. Builds dist
3. Deploys to OR4

## Testing Checklist

After deployment, verify:
- [ ] Service is running: `systemctl status matrix-api`
- [ ] Web app loads: `https://<router-ip>:100XX/app/`
- [ ] No JavaScript errors in browser console
- [ ] API endpoints respond: `https://<router-ip>:100XX/api/system/status`
- [ ] Permissions are correct: `ls -la /opt/matrix-api-app/`

## Router Configuration

### NAT Rules
Each OR needs proper port forwarding:
```
External Port 100XX → Internal IP 10.X.X.12:8080
```

Example for OR1:
```
ip nat inside source static tcp 10.1.1.12 8080 interface GigabitEthernet0/0/0 10001
```

### SSH Access
```
External Port 20X → Internal IP 10.X.X.12:22
```

## File Locations Reference

### On Each OR:
```
/usr/share/matrix-app/matrix-app.asar          # Electron app (web assets)
/opt/matrix-api-app/                           # Deployed web assets
/usr/lib/node_modules/matrix.api/dist/         # Backend API code
/usr/lib/systemd/system/matrix-api.service     # Service definition
/usr/lib/node_modules/matrix.api/matrix.api.config.json  # API config
```

### On Windows:
```
matrix-api-linux/dist/                         # Built backend code
or-refresh-automation/upgrade_or.py            # Deployment script
or-refresh-automation/deploy_or.bat            # One-command wrapper
or-refresh-automation/.env                     # Credentials
```

## Summary

**The key insight:** The OR deployment has two separate components (web assets and backend dist) that must be version-matched. The automated script now handles this correctly by downloading each OR's .asar, extracting web assets, and deploying them alongside the locally-built dist folder.
