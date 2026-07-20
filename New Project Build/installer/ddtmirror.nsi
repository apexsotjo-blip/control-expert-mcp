; DDT Mirror installer - per-user install, no admin rights required.
;
; Build the app first (from New Project Build):
;   .venv\Scripts\pyinstaller installer\ddtmirror.spec --noconfirm ^
;       --distpath installer\dist --workpath installer\build
; Then compile this script:
;   installer\tools\nsis\makensis.exe installer\ddtmirror.nsi
; Produces installer\DDTMirror-Setup-<version>.exe

!include "MUI2.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!define APP_NAME "DDT Mirror"
!define COMPANY "DDT Mirror"
!define EXE_NAME "DDTMirror.exe"
!define UNINST_KEY \
  "Software\Microsoft\Windows\CurrentVersion\Uninstall\DDTMirror"

Name "${APP_NAME}"
OutFile "DDTMirror-Setup-${VERSION}.exe"
; Per-user install: %LOCALAPPDATA%\Programs\DDTMirror - no UAC prompt,
; works on a machine where the engineer has no admin rights.
InstallDir "$LOCALAPPDATA\Programs\DDTMirror"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE_NAME}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch DDT Mirror now"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "dist\DDTMirror\*"

  CreateDirectory "$SMPROGRAMS\DDT Mirror"
  CreateShortcut "$SMPROGRAMS\DDT Mirror\DDT Mirror.lnk" \
    "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME}" 0
  CreateShortcut "$SMPROGRAMS\DDT Mirror\Uninstall DDT Mirror.lnk" \
    "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\DDT Mirror.lnk" \
    "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME}" 0

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; HKCU so Add/Remove Programs works without admin rights.
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${COMPANY}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" \
    '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" \
    '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\DDT Mirror\DDT Mirror.lnk"
  Delete "$SMPROGRAMS\DDT Mirror\Uninstall DDT Mirror.lnk"
  RMDir "$SMPROGRAMS\DDT Mirror"
  Delete "$DESKTOP\DDT Mirror.lnk"
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
