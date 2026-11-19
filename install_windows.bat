@echo off
setlocal
set "ROOT=%~dp0"
set "CONDA_BAT="

for %%P in ("%USERPROFILE%\miniforge3\condabin\conda.bat" "%LOCALAPPDATA%\miniforge3\condabin\conda.bat" "%USERPROFILE%\miniconda3\condabin\conda.bat" "%USERPROFILE%\anaconda3\condabin\conda.bat") do (
  if not defined CONDA_BAT if exist "%%~P" set "CONDA_BAT=%%~P"
)
if not defined CONDA_BAT (
  for /f "delims=" %%P in ('where conda.bat 2^>nul') do if not defined CONDA_BAT set "CONDA_BAT=%%P"
)
if not defined CONDA_BAT (
  echo Miniforge or Miniconda is required for the matching GDAL libraries.
  echo Install Miniforge for Windows, then double-click install_windows.bat again.
  start "" "https://github.com/conda-forge/miniforge/releases/latest"
  exit /b 1
)

echo Creating or updating the local GDAL and Streamlit environment...
echo If Conda asks to proceed, type Y and press Enter.
call "%CONDA_BAT%" env update --prefix "%ROOT%.conda-env" --file "%ROOT%environment.yml" --prune
if errorlevel 1 (
  echo Environment setup failed. Check the messages above and try again.
  pause
  exit /b 1
)
echo Setup complete. Double-click run_windows.bat to launch the app.
exit /b 0


