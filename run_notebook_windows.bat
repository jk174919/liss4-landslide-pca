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
  echo Miniforge or Miniconda is required. Starting the one-time setup...
  call "%ROOT%install_windows.bat"
  if errorlevel 1 exit /b 1
  for %%P in ("%USERPROFILE%\miniforge3\condabin\conda.bat" "%LOCALAPPDATA%\miniforge3\condabin\conda.bat" "%USERPROFILE%\miniconda3\condabin\conda.bat" "%USERPROFILE%\anaconda3\condabin\conda.bat") do (
    if not defined CONDA_BAT if exist "%%~P" set "CONDA_BAT=%%~P"
  )
  if not defined CONDA_BAT (
    for /f "delims=" %%P in ('where conda.bat 2^>nul') do if not defined CONDA_BAT set "CONDA_BAT=%%P"
  )
)
if not exist "%ROOT%.conda-env\python.exe" (
  call "%ROOT%install_windows.bat"
  if errorlevel 1 exit /b 1
)
echo Starting JupyterLab with the bulk preparation notebook...
call "%CONDA_BAT%" run --prefix "%ROOT%.conda-env" --no-capture-output jupyter lab "%ROOT%bulk_scene_preparation.ipynb"

