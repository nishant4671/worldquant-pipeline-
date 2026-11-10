# Deployment Guide: WorldQuant BRAIN 24/7 Automation

This guide covers deployment instructions for running the Telegram listener background daemon, configuring the automatic watchdog in Windows Task Scheduler, and scheduling the overnight alpha discovery pipeline.

---

## Section 1: How to Start the Listener Manually

The listener daemon polls for Telegram interactive button events (Submit / Skip decisions) and automatically submits approved alphas to the WorldQuant BRAIN platform.

### Running Silently in Background (Recommended)
1. Navigate to `C:\Users\HP\worldquant-pipeline` in Windows File Explorer.
2. Double-click `run_listener.vbs`.
   - This executes `pythonw.exe main.py listen` with a completely hidden window (`0`, `False`).
   - It will run persistently in the background.

### Verifying Listener is Running
Open PowerShell or Command Prompt and run:
```powershell
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -like "*main.py listen*" } | Select-Object ProcessId, CommandLine
```
Alternatively, open **Windows Task Manager**, go to the **Details** tab, and look for `pythonw.exe`.

### Stopping the Listener
If you need to stop the background listener:
```powershell
Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" | Where-Object { $_.CommandLine -like "*main.py listen*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## Section 2: How to Set Up the Watchdog in Windows Task Scheduler

The watchdog script (`watchdog_listener.ps1`) verifies that `main.py listen` is active. If the process ever terminates or crashes, it automatically logs the timestamp to `listener_watchdog.log` and relaunches the listener via `run_listener.vbs`.

### Option A: One-Line Command (Fastest)
Open PowerShell and run:
```powershell
schtasks /Create /TN "WorldQuant_Listener_Watchdog" `
  /SC MINUTE /MO 15 `
  /TR "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\HP\worldquant-pipeline\watchdog_listener.ps1" `
  /F
```

### Option B: Using Task Scheduler GUI
1. Press `Win + R`, type `taskschd.msc`, and press **Enter**.
2. Click **Create Task...** in the right-hand *Actions* pane.
3. **General** tab:
   - **Name**: `WorldQuant_Listener_Watchdog`
   - **Security options**: Select *Run only when user is logged on* (or *Run whether user is logged on or not* for headless execution).
4. **Triggers** tab:
   - Click **New...**
   - **Begin the task**: *On a schedule*
   - Under *Settings*, choose **Daily** starting today.
   - Under *Advanced settings*:
     - Check **Repeat task every**: `15 minutes` for a duration of: `Indefinitely`
     - Check **Enabled**.
   - Click **OK**.
5. **Actions** tab:
   - Click **New...**
   - **Action**: *Start a program*
   - **Program/script**: `powershell.exe`
   - **Add arguments**: `-ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\HP\worldquant-pipeline\watchdog_listener.ps1"`
   - **Start in**: `C:\Users\HP\worldquant-pipeline`
   - Click **OK**.
6. **Conditions & Settings** tabs:
   - Uncheck *Stop if the computer switches to battery power*.
   - Check *Run task as soon as possible after a scheduled start is missed*.
7. Click **OK** to save the task.

---

## Section 3: How to Set Up the Overnight Pipeline

The overnight pipeline runs alpha proposal, validation, PM critique, simulation, and Telegram digest delivery (`main.py pipeline 50`) automatically every night at 2:00 AM.

### Option A: One-Line Command (Fastest)
Run the following command in PowerShell:
```powershell
schtasks /Create /TN "WorldQuant_Overnight_Pipeline" `
  /SC DAILY /ST 02:00 `
  /TR "C:\Users\HP\worldquant-pipeline\venv\Scripts\python.exe C:\Users\HP\worldquant-pipeline\main.py pipeline 50" `
  /F
```

### Option B: Using Task Scheduler GUI
1. Open **Task Scheduler** (`taskschd.msc`).
2. Click **Create Task...** in the right-hand *Actions* pane.
3. **General** tab:
   - **Name**: `WorldQuant_Overnight_Pipeline`
   - **Description**: Nightly alpha discovery pipeline (50 candidates).
4. **Triggers** tab:
   - Click **New...**
   - **Begin the task**: *On a schedule*
   - Select **Daily**, set Start Time to `02:00:00 AM`.
   - Check **Enabled**.
   - Click **OK**.
5. **Actions** tab:
   - Click **New...**
   - **Action**: *Start a program*
   - **Program/script**: `C:\Users\HP\worldquant-pipeline\venv\Scripts\python.exe`
   - **Add arguments**: `main.py pipeline 50`
   - **Start in**: `C:\Users\HP\worldquant-pipeline`
   - Click **OK**.
6. **Conditions & Settings** tabs:
   - Ensure *Wake the computer to run this task* is checked if your system enters sleep mode.
   - Uncheck *Stop if the computer switches to battery power* (for laptops).
7. Click **OK** to save the task.

---

## File Summary

| File | Purpose |
|---|---|
| `run_listener.vbs` | Runs `pythonw.exe main.py listen` silently with no console window. |
| `watchdog_listener.ps1` | Checks if listener is alive; restarts it and logs downtime if not running. |
| `listener_watchdog.log` | Records watchdog restart events with timestamp. |
| `pipeline.log` | Records all pipeline simulations, critique, validator, and engine activities. |
| `DEPLOYMENT.md` | Complete operational documentation for Windows task scheduling. |
